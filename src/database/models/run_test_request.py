import pydantic
from typing import Optional



class RunTestRequest(pydantic.BaseModel):
    project: int
    module: Optional[int] = None
    category: Optional[str] = None
    case: Optional[int] = None
    # v2=True：用带 steps/env 的新 loader（推荐）；留空等价于 v1 行为。
    v2: Optional[bool] = False