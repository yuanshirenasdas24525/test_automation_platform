"""AppSessionRegistry：一轮 pytest run 内复用 AppSession。

背景：
  原来的实现是 CaseExecutor 每条 case 自建一个 AppSession，case 跑完就 driver.quit()
  + release 设备。结果同一轮跑的多个 app case 之间，App 会被强制退出、登录态 /
  设备 session 全丢 —— 用户实际的诉求是"整轮只开一次 app，除非我显式 app_close"。

设计思路（Option A：跨 case 持久化）：
  - 提供一个 process-scoped 的 singleton `AppSessionRegistry`。
  - 当 CaseExecutor 需要 App 会话时，不再直接 `acquire_session_for_case`，而是走
    `registry.get_or_create(case_dict)`。命中缓存 → 复用同一个 AppSession；未命中
    才真正 acquire（=占设备 + 起 driver）。
  - case 结束时 **不** close；driver 继续挂着。
  - pytest_sessionfinish 钩子在 `config/pytest_config.py` 里触发 `close_all()`，
    把所有还活着的 driver 依次 quit + release，保证设备资源最终一定归还。
  - 测试友好：`AppSessionRegistry.reset()` 干掉 singleton；
    `CaseExecutor(app_session_factory=...)` 路径绕开 registry（保持老单测的行为不变）。

Key 选择：
  - 第一优先 device_id（RunCaseDialog 让用户显式选设备后，case_dict 里就有）。
  - 没 device_id 的回退路径：按 env.device_pool + 平台拼 key，这样同一个 pool/平台
    的 case 能共享 session；但这种情况下 pool.acquire 会随机选一台，复用语义其实
    较弱 —— 现实里只要用了 RunCaseDialog 就一定走 device_id 路径。

同 key 不同 caps 的语义：
  - 保留现有 session 不变，更新 session.caps（只影响未启动时的 driver 工厂；已起
    的 driver 改不了）。这是"只手动关"的直接翻译 —— 切 appPackage 想要从头打开新
    app 时，请加一条 app_launch step 显式切，而不是期望 registry 给你重建。
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from runners.app.session import AppSession

logger = logging.getLogger(__name__)


class AppSessionRegistry:
    """跨 case 持久化 AppSession 的进程级缓存。

    线程安全：外层有一把 `_lock`，保证 get_or_create / close_all / close_one 之间
    不会相互踩；但并不保证两次并发 get_or_create 同 key 时"只 acquire 一次"的
    极强语义 —— 现实中 pytest 是串行执行 case 的，这里不做更精细的 per-key 锁。
    """

    _singleton: "AppSessionRegistry | None" = None
    _singleton_lock = threading.Lock()

    def __init__(self) -> None:
        self._sessions: dict[str, "AppSession"] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------
    # singleton 入口
    # ------------------------------------------------------------
    @classmethod
    def default(cls) -> "AppSessionRegistry":
        if cls._singleton is None:
            with cls._singleton_lock:
                if cls._singleton is None:
                    cls._singleton = cls()
        return cls._singleton

    @classmethod
    def reset(cls) -> None:
        """丢弃当前 singleton。**不会** close 里头的 session —— 那是业务方的事。

        调用场景：pytest_sessionstart 开一轮新跑时，避免上一轮的残留状态影响；
        或单测里隔离不同测试用例。
        """
        with cls._singleton_lock:
            cls._singleton = None

    # ------------------------------------------------------------
    # key 推导
    # ------------------------------------------------------------
    @staticmethod
    def _case_key(case_dict: dict) -> str:
        """按 case 信息推一个复用 key。能拿到 device_id 就按 id 拼，拿不到就退化
        成 pool+platform —— 拿不到 key 的情况本模块对外保证还能工作，只是复用语义弱。
        """
        did = case_dict.get("device_id")
        if did is not None:
            return f"id:{did}"
        env = case_dict.get("environment") or {}
        pool = (env.get("device_pool") if isinstance(env, dict) else None) or "default"
        variables = case_dict.get("variables") or {}
        platform = ""
        if isinstance(variables, dict):
            platform = str(
                variables.get("platform") or variables.get("platformName") or ""
            ).lower()
        return f"pool:{pool}:platform:{platform}"

    # ------------------------------------------------------------
    # 主要接口
    # ------------------------------------------------------------
    def get_or_create(
        self,
        case_dict: dict,
        *,
        device_pool=None,
        driver_factory: Callable | None = None,
    ) -> "AppSession":
        """命中就复用，不命中就新建。

        `_force_new_app_session=True` 时强制重建：先 close 旧的，再起新的。这是给
        "我想换台设备接着跑"或"这条 case 需要全新状态"的用户留的逃生舱。
        """
        from runners.app.session import acquire_session_for_case

        key = self._case_key(case_dict)
        force_new = bool(case_dict.get("_force_new_app_session"))

        with self._lock:
            existing = self._sessions.get(key)
            if existing is not None and not force_new and not existing._closed:
                logger.info("♻ AppSessionRegistry 命中 key=%s，复用旧 session", key)
                # caps 差异不做 hot reload（driver 已起就改不了）；下次重开才生效
                return existing

            if existing is not None:
                reason = "force_new" if force_new else "已关闭"
                logger.info(
                    "🔄 AppSessionRegistry key=%s 替换旧 session（原因：%s）",
                    key, reason,
                )
                try:
                    existing.close()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("替换时关闭旧 session 失败（忽略）：%s", exc)
                self._sessions.pop(key, None)

            session = acquire_session_for_case(
                case_dict,
                device_pool=device_pool,
                driver_factory=driver_factory,
            )
            self._sessions[key] = session
            logger.info(
                "✨ AppSessionRegistry 新建 session key=%s device=%s",
                key, session.device.get("udid"),
            )
            return session

    def close_one(self, case_dict: dict) -> bool:
        """按 case 关掉对应的 session。返回是否真的关掉一条。

        暂时没有对外的 step 在用它；留给后续 `app_terminate_session` 之类的入口。
        """
        key = self._case_key(case_dict)
        with self._lock:
            session = self._sessions.pop(key, None)
        if session is None:
            return False
        try:
            session.close()
            logger.info("🧹 AppSessionRegistry 显式关闭 key=%s", key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("关闭 session key=%s 失败（忽略）：%s", key, exc)
        return True

    def close_all(self) -> None:
        """关掉所有活着的 session。pytest_sessionfinish 调用。"""
        with self._lock:
            items = list(self._sessions.items())
            self._sessions.clear()
        for key, session in items:
            try:
                session.close()
                logger.info("🧹 AppSessionRegistry 收尾关闭 key=%s", key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("收尾关闭 key=%s 失败（忽略）：%s", key, exc)

    # ------------------------------------------------------------
    # 运维 / 调试
    # ------------------------------------------------------------
    def snapshot(self) -> dict[str, dict]:
        """只读视图：方便单元测试/诊断接口查看当前缓存。"""
        with self._lock:
            return {
                key: {
                    "device_udid": s.device.get("udid"),
                    "device_id": s.device.get("id"),
                    "driver_started": s._driver is not None,
                    "closed": s._closed,
                }
                for key, s in self._sessions.items()
            }
