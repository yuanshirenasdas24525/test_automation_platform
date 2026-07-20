"""Celery 定时任务：定期对设备池里所有设备做 Appium 心跳探测。

执行节奏：
  - 由 celery_app.py 里的 beat_schedule 每 30s 调度一次；
  - 任务内部遍历 devices 表（忽略 udid 缺 agent_host/appium_port 的：它们根本没登记探测点）；
  - 对每台设备 HTTP GET Appium `/status`：
      成功 → failures 清零 + 刷 last_heartbeat + 若原 status=offline 则恢复为 idle；
      失败 → failures += 1；达到 OFFLINE_THRESHOLD（默认 2）就把 status 置为 offline。
  - **busy 设备** 探测失败不会立刻翻 offline —— 只累计 failures，等 release 后的下一轮才视 offline。
    这样避免"用例正在跑、Appium 短暂 503"直接把任务打断。

幂等：同一台设备的状态机只在 DB 事务里切换；并发 beat 不会相互踩脚
（两轮任务如果碰到同一台设备，最多让 failures +2，不会错乱状态）。

依赖：探测逻辑复用 utils/appium_probe.probe_appium，避免 import 回 server.api 链路。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import List

from celery_app import celery_app
from database.db import DB
from database.models import (
    DEVICE_STATUS_BUSY,
    DEVICE_STATUS_IDLE,
    DEVICE_STATUS_OFFLINE,
    Device,
)
from utils.appium_probe import probe_appium
from utils.device_probe import probe_device_alive

logger = logging.getLogger(__name__)


# 连续失败多少次才判 offline。写死在代码里，后续要可调可以挪进 celery env。
OFFLINE_THRESHOLD = 2

# 设备租约超时（分钟）：busy 超过这个时长视为持有方已死（worker 崩溃 / 任务丢失），
# 由本任务强制释放，避免设备永久卡 busy。0 或负数 = 关闭该机制。
# 默认 120 分钟 —— 大于最长的正常执行（web/app 大批量回归），小于"人工发现再手工修库"的代价。
DEVICE_LEASE_TIMEOUT_MINUTES = int(os.environ.get("DEVICE_LEASE_TIMEOUT_MINUTES", "120"))


def _reclaim_expired_leases(db) -> List[str]:
    """强制释放租约超时的 busy 设备，返回被回收的 udid 列表（审计走 warning 日志）。"""
    if DEVICE_LEASE_TIMEOUT_MINUTES <= 0:
        return []
    deadline = datetime.now() - timedelta(minutes=DEVICE_LEASE_TIMEOUT_MINUTES)
    expired = (
        db.session.query(Device)
        .filter(
            Device.status == DEVICE_STATUS_BUSY,
            Device.busy_since.isnot(None),
            Device.busy_since < deadline,
        )
        .all()
    )
    reclaimed: List[str] = []
    for dev in expired:
        logger.warning(
            "[lease] 强制释放设备 udid=%s：busy 自 %s 起已超 %d 分钟（owner_execution_id=%s），"
            "判定持有方已死",
            dev.udid, dev.busy_since, DEVICE_LEASE_TIMEOUT_MINUTES, dev.owner_execution_id,
        )
        dev.status = DEVICE_STATUS_IDLE
        dev.owner_execution_id = None
        dev.busy_since = None
        reclaimed.append(dev.udid)
    return reclaimed


def _probe_one(db, dev: Device) -> None:
    """单台设备的探测 + 状态机切换。异常自己吞掉，不让一台坏设备影响整轮。

    端口缺省值：Appium 默认 4723。这里把"用户没填 appium_port"也当作合法情况
    （而不是直接跳过），让用户在 DevicesPage 上能立即看到探测失败的提示，
    去 fix 端口配置；否则用户加完设备一脸懵 ——"为啥另外那台显示从未探测过？"。
    """
    host = (dev.agent_host or "").strip() or "localhost"
    port = int(dev.appium_port or 4723)

    # —— 第 1 道：Appium server 是否活着 ——
    try:
        appium_ok, appium_detail = probe_appium(host, port, timeout=2.0)
    except Exception as exc:  # noqa: BLE001
        appium_ok, appium_detail = False, f"probe 抛异常: {type(exc).__name__}: {exc}"

    # —— 第 2 道：设备本身在系统层面是否"看得见"（adb / idevice_id / simctl）——
    # 这个是修 v1 的痛点：Appium 在跑、但模拟器关掉了，仅看 /status 会误判成"心跳正常"。
    # supported=False 时（adb 没装等）保持 dev_ok=None，不参与判定。
    try:
        dev_ok, dev_detail, dev_supported = probe_device_alive(
            dev.udid, dev.platform or ""
        )
    except Exception as exc:  # noqa: BLE001
        dev_ok, dev_detail, dev_supported = None, f"device probe 抛异常: {exc}", False

    # 综合判定：
    #   - Appium 失败：直接判失败（详情用 Appium 的）
    #   - Appium OK 但 dev_supported=True 且 dev_ok=False：设备真挂了 → 失败
    #   - 其它：成功
    if not appium_ok:
        ok, detail = False, appium_detail
    elif dev_supported and dev_ok is False:
        ok, detail = False, f"Appium OK 但设备不在线: {dev_detail}"
    else:
        ok, detail = True, f"appium={appium_detail}; device={dev_detail}"

    if ok:
        dev.consecutive_failures = 0
        dev.last_heartbeat = datetime.now()
        if dev.status == DEVICE_STATUS_OFFLINE:
            dev.status = DEVICE_STATUS_IDLE
            logger.info(
                "device udid=%s (%s:%s) 探测恢复 → idle",
                dev.udid, host, port,
            )
        return

    # 失败路径
    dev.consecutive_failures = (dev.consecutive_failures or 0) + 1
    logger.warning(
        "device udid=%s (%s:%s) 探测失败（%s）累计 %d 次",
        dev.udid, host, port, detail, dev.consecutive_failures,
    )

    # busy 设备不强改 offline：正在跑用例，失败可能只是短暂 503。
    # 等 release 后下一轮仍然失败，会再走一次累计，到阈值自然翻 offline。
    if dev.status == DEVICE_STATUS_BUSY:
        return

    if dev.consecutive_failures >= OFFLINE_THRESHOLD and dev.status != DEVICE_STATUS_OFFLINE:
        dev.status = DEVICE_STATUS_OFFLINE
        dev.owner_execution_id = None  # offline 了占用锁也没意义，清掉
        logger.warning(
            "device udid=%s 连续失败 %d 次，置 offline",
            dev.udid, dev.consecutive_failures,
        )


@celery_app.task(name="tasks.probe_devices")
def probe_devices_task() -> dict:
    """遍历设备池，对每台设备做一次 Appium 心跳探测。

    返回一个小摘要 dict，方便在 celery flower / 日志里看每轮处理了多少。
    """
    total = 0
    ok_count = 0
    fail_count = 0
    turned_offline: List[str] = []
    recovered: List[str] = []

    db = DB()
    try:
        # 历史坑：之前用 `Device.appium_port.isnot(None)` 过滤，结果用户在 DevicesPage
        # 注册第二台设备时如果忘填 appium_port（前端 placeholder 是 4723，不是 default
        # 自动填充），就被这里 silently 跳过 —— 表现就是「只有第一台设备的心跳一直在
        # 更新，后面加的都显示『未探测过』」，用户一脸懵。
        #
        # 现在改成全量遍历：appium_port 缺省的设备也试着用 4723（Appium 默认端口）
        # 探一次。探不通就累计失败、最终翻 offline，行为对用户来说是可见、可排障的。
        # 先回收租约超时的 busy 设备（worker 崩溃兜底），再做常规心跳探测
        reclaimed = _reclaim_expired_leases(db)

        devices = db.session.query(Device).all()
        total = len(devices)
        for dev in devices:
            prev_status = dev.status
            _probe_one(db, dev)
            if dev.status == DEVICE_STATUS_OFFLINE and prev_status != DEVICE_STATUS_OFFLINE:
                turned_offline.append(dev.udid)
            if dev.status == DEVICE_STATUS_IDLE and prev_status == DEVICE_STATUS_OFFLINE:
                recovered.append(dev.udid)
            if (dev.consecutive_failures or 0) == 0 and dev.status != DEVICE_STATUS_OFFLINE:
                ok_count += 1
            else:
                fail_count += 1
        db.session.commit()
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        logger.exception("probe_devices_task 执行失败: %s", exc)
        raise
    finally:
        db.close()

    summary = {
        "total": total,
        "ok": ok_count,
        "fail": fail_count,
        "turned_offline": turned_offline,
        "recovered": recovered,
        "lease_reclaimed": reclaimed,
    }
    if turned_offline or recovered or reclaimed:
        logger.info("probe_devices_task 完成: %s", summary)
    return summary
