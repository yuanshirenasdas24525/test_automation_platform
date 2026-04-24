import pydantic
from typing import Optional



class RunTestRequest(pydantic.BaseModel):
    project: int
    module: Optional[int] = None
    category: Optional[str] = None
    case: Optional[int] = None
    # v2=True：用带 steps/env 的新 loader（推荐）；留空等价于 v1 行为。
    v2: Optional[bool] = False
    # 指定一台设备跑 app 用例。传了就忽略 env.device_pool/platform 过滤，
    # 直接 `DevicePool.acquire_by_id(device_id)`；设备必须 idle，否则 409。
    # 只对 category='app' 或 case_type 含 app 的用例有意义。
    device_id: Optional[int] = None