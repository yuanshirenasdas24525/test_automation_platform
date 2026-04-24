"""Device Agent —— 暂未实现（计划中）。

设备 Agent 运行在物理机 / 模拟器宿主上，独立于平台进程。职责：

  - 发现设备：adb devices / ios xcrun simctl list 并上报
  - 管理 Appium Server 生命周期（启停、端口分配、日志落盘）
  - 上报设备心跳、状态（idle / busy / offline）到 devices 表
  - 提供简单 HTTP 接口给 platform 层反向查询（可选）

当前平台侧的 `runners/app/device_pool.py` 直接读写 devices 表，占坑实现。
Agent 就位后只需定期 `DevicePool.heartbeat(udid, status, ...)`，平台逻辑无需改动。

预期文件：
  - main.py                进程入口（长驻 daemon）
  - device_discovery.py    adb / ios-deploy / simctl 扫描
  - appium_supervisor.py   Appium Server 的 spawn / kill / health
  - reporter.py            心跳 & 状态回传
"""
