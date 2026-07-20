from sqlalchemy import Column, Integer, String, DateTime, func

from database.base import Base, JSONType


# 设备状态
DEVICE_STATUS_IDLE = "idle"        # 空闲，可被分配
DEVICE_STATUS_BUSY = "busy"        # 已被占用
DEVICE_STATUS_OFFLINE = "offline"  # 离线（超过心跳窗口未上报）


class Device(Base):
    """
    App 自动化设备池记录。
    由 Agent 通过心跳接口上报；平台的 AppRunner 通过 Device.acquire() 申请设备。
    """
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)

    # 设备基础信息（由 adb devices / idevice_id 采集）
    udid = Column(String(128), unique=True, nullable=False, index=True)
    platform = Column(String(20), nullable=False)     # Android | iOS
    platform_version = Column(String(32))
    device_name = Column(String(128))
    brand = Column(String(64))
    model = Column(String(128))

    # Agent 侧信息
    agent_host = Column(String(128), index=True)      # 设备所在 Agent 机器
    agent_port = Column(Integer)                      # Agent 的 HTTP 端口
    appium_port = Column(Integer)                     # 该设备对应的 Appium Server 端口

    # 调度相关
    pool = Column(String(64), default="default", index=True)  # 设备池标签
    status = Column(String(20), default=DEVICE_STATUS_OFFLINE, index=True)
    owner_execution_id = Column(Integer, nullable=True, index=True)  # 占用锁：当前被哪个 execution 持有
    busy_since = Column(DateTime, nullable=True)  # 租约起点：acquire 时刻；超时由 probe_devices 强制释放

    # 扩展字段
    capabilities = Column(JSONType)                   # 额外 Appium capabilities
    tags = Column(JSONType)                          # 用户打标：['高版本','4G卡']

    # 时间戳
    last_heartbeat = Column(DateTime)
    create_time = Column(DateTime, server_default=func.now())
    update_time = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # 心跳探测：连续失败次数。成功一次就清零；到达阈值（默认 2）就把 status 置 offline。
    # 这个字段由后端的心跳探测 Celery 任务写入，不建议人工改。
    consecutive_failures = Column(Integer, default=0, nullable=False, server_default="0")

    def __repr__(self):
        return f"<Device udid={self.udid} status={self.status} pool={self.pool}>"
