"""DevicePool：从 `devices` 表里拿一台设备，用完归还。

这是"最小可用"的设备池实现，特点：
  - 只依赖 DB（用 SELECT ... FOR UPDATE SKIP LOCKED 做并发互斥，但 SQLite 退化成普通 SELECT）；
  - 把设备状态机限定为 idle / busy / offline 三种，acquire 时 idle→busy，release 时 busy→idle；
  - 不负责启动 Appium Server —— 那是 Device Agent 进程的活，这里只消费"已经
    有 appium_host:appium_port 的设备记录"。

未来 Device Agent 进程接入后，只需要它定时 heartbeat 维护 devices.last_heartbeat 和
status 字段，DevicePool 侧不用改。
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)


# 状态常量，和 src/database/models/device.py 里保持一致
DEVICE_STATUS_IDLE = "idle"
DEVICE_STATUS_BUSY = "busy"
DEVICE_STATUS_OFFLINE = "offline"


class DevicePool:
    def __init__(self, session_factory=None):
        """
        :param session_factory: 可调用对象 () -> SQLAlchemy Session。默认用 DB()。
        """
        self._session_factory = session_factory

    @classmethod
    def default(cls) -> "DevicePool":
        return cls()

    # ------------------------------------------------------------
    # acquire
    # ------------------------------------------------------------
    def acquire(
        self,
        pool_name: str = "default",
        platform: Optional[str] = None,
        execution_id: Optional[int] = None,
        wait_seconds: float = 0.0,
        poll_interval: float = 1.0,
    ) -> Optional[dict]:
        """尝试拿一台设备。
           wait_seconds > 0 时会轮询等待；0 表示拿不到就立即返回 None。"""
        deadline = time.time() + wait_seconds
        while True:
            device = self._try_pick_one(pool_name, platform, execution_id)
            if device is not None:
                return device
            if time.time() >= deadline:
                return None
            time.sleep(poll_interval)

    def _try_pick_one(self, pool_name, platform, execution_id):
        with self._db() as sess:
            dialect = sess.bind.dialect.name if sess.bind else ""
            # 基础过滤条件：idle + pool_name 匹配 + 平台匹配（可选）
            where = ["status = :idle", "pool = :pool"]
            params: dict = {"idle": DEVICE_STATUS_IDLE, "pool": pool_name}
            if platform:
                where.append("LOWER(platform) = :platform")
                params["platform"] = platform.lower()

            sql = f"SELECT * FROM devices WHERE {' AND '.join(where)} ORDER BY id LIMIT 1"
            if dialect == "postgresql":
                sql += " FOR UPDATE SKIP LOCKED"

            row = sess.execute(text(sql), params).mappings().first()
            if row is None:
                return None

            device_id = row["id"]
            # 乐观锁 update：只有 status 还是 idle 才改成 busy
            updated = sess.execute(
                text(
                    "UPDATE devices "
                    "SET status = :busy, owner_execution_id = :execution_id, "
                    "    busy_since = CURRENT_TIMESTAMP "
                    "WHERE id = :id AND status = :idle"
                ),
                {
                    "busy": DEVICE_STATUS_BUSY,
                    "idle": DEVICE_STATUS_IDLE,
                    "execution_id": execution_id,
                    "id": device_id,
                },
            )
            if updated.rowcount != 1:
                # 并发抢锁输了，换一台
                sess.rollback()
                return None
            sess.commit()

            device = dict(row)
            logger.info("acquire 设备 id=%s udid=%s pool=%s platform=%s",
                        device_id, device.get("udid"), pool_name, platform)
            return device

    # ------------------------------------------------------------
    # acquire_by_id：调度器指定某一台设备（忽略 pool / platform 过滤）
    # ------------------------------------------------------------
    def acquire_by_id(
        self,
        device_id: int,
        execution_id: Optional[int] = None,
        wait_seconds: float = 0.0,
        poll_interval: float = 1.0,
    ) -> Optional[dict]:
        """按 device.id 锁定一台指定的设备。

        和 `acquire()` 的区别：
          - 不走 pool_name / platform 过滤；调用方（前端选设备）已经确定要哪台了；
          - 设备必须处于 `idle` 状态才能锁定；offline / busy 都会失败；
          - 依旧走乐观锁 UPDATE，避免和 acquire() 以及其它 acquire_by_id() 并发打架。

        失败时返回 None——典型场景是设备已经离线或被别的任务抢走了。
        """
        deadline = time.time() + wait_seconds
        while True:
            device = self._try_pick_by_id(device_id, execution_id)
            if device is not None:
                return device
            if time.time() >= deadline:
                return None
            time.sleep(poll_interval)

    def _try_pick_by_id(self, device_id: int, execution_id):
        with self._db() as sess:
            row = sess.execute(
                text("SELECT * FROM devices WHERE id = :id"),
                {"id": device_id},
            ).mappings().first()
            if row is None:
                return None

            if row["status"] != DEVICE_STATUS_IDLE:
                # 只在 idle 才允许锁定
                logger.warning(
                    "acquire_by_id 失败：device id=%s 当前 status=%s，不是 idle",
                    device_id, row["status"],
                )
                return None

            updated = sess.execute(
                text(
                    "UPDATE devices "
                    "SET status = :busy, owner_execution_id = :execution_id, "
                    "    busy_since = CURRENT_TIMESTAMP "
                    "WHERE id = :id AND status = :idle"
                ),
                {
                    "busy": DEVICE_STATUS_BUSY,
                    "idle": DEVICE_STATUS_IDLE,
                    "execution_id": execution_id,
                    "id": device_id,
                },
            )
            if updated.rowcount != 1:
                sess.rollback()
                logger.warning(
                    "acquire_by_id 失败：device id=%s 被并发抢走了", device_id,
                )
                return None
            sess.commit()

            device = dict(row)
            logger.info(
                "acquire_by_id 设备 id=%s udid=%s （指定选择）",
                device_id, device.get("udid"),
            )
            return device

    # ------------------------------------------------------------
    # release
    # ------------------------------------------------------------
    def release(self, device_id_or_udid: int | str) -> bool:
        """归还设备：busy→idle。幂等——已经 idle 的调用也返回 True。"""
        with self._db() as sess:
            if isinstance(device_id_or_udid, int) or str(device_id_or_udid).isdigit():
                where = "id = :key"
                params = {"key": int(device_id_or_udid)}
            else:
                where = "udid = :key"
                params = {"key": str(device_id_or_udid)}
            params.update({
                "idle": DEVICE_STATUS_IDLE,
                "busy": DEVICE_STATUS_BUSY,
            })
            result = sess.execute(
                text(
                    f"UPDATE devices SET status = :idle, owner_execution_id = NULL, busy_since = NULL "
                    f"WHERE {where} AND status = :busy"
                ),
                params,
            )
            sess.commit()
            logger.info("release 设备 key=%s 受影响行数=%s", device_id_or_udid, result.rowcount)
            return result.rowcount >= 0  # 幂等，即使 0 行也算成功

    # ------------------------------------------------------------
    # heartbeat（供 Device Agent 进程调用）
    # ------------------------------------------------------------
    def heartbeat(self, udid: str, status: str = DEVICE_STATUS_IDLE, **fields) -> None:
        """Agent 进程定期调用，维持设备的 last_heartbeat + status。
        未出现的设备可以走另外的 upsert 脚本，这里只处理已有记录。"""
        fields["status"] = status
        set_clause = ", ".join([f"{k} = :{k}" for k in fields])
        set_clause += ", last_heartbeat = CURRENT_TIMESTAMP"
        with self._db() as sess:
            sess.execute(
                text(f"UPDATE devices SET {set_clause} WHERE udid = :udid"),
                {**fields, "udid": udid},
            )
            sess.commit()

    # ------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------
    @contextmanager
    def _db(self):
        if self._session_factory:
            sess = self._session_factory()
            try:
                yield sess
            finally:
                sess.close()
        else:
            from database.db import DB
            db = DB()
            try:
                yield db.session
            finally:
                db.close()
