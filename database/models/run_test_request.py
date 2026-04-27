import pydantic
from typing import Optional



class RunTestRequest(pydantic.BaseModel):
    project: int
    module: Optional[int] = None
    category: Optional[str] = None
    case: Optional[int] = None
    # 指定一台设备跑 app/android/ios 用例。传了就忽略 env.device_pool/platform 过滤，
    # 直接 `DevicePool.acquire_by_id(device_id)`；设备必须 idle，否则 409。
    # 只对 category 在 {app, android, ios} 时有意义；其它类型忽略。
    device_id: Optional[int] = None