import pydantic
from typing import Optional



class RunTestRequest(pydantic.BaseModel):
    project: int
    module: Optional[int] = None
    category: Optional[str] = None
    case: Optional[int] = None