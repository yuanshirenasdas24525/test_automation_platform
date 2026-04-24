import pydantic
from typing import Optional


class ProjectCreate(pydantic.BaseModel):
    """
    创建 / 编辑项目的请求体。

    注意：Pydantic v2 不再把 `Optional[X]` 当成"带 None 默认值"——
    必须显式 `= None` 才是选填。这里 description / icon 都是选填。
    """
    name: str
    type: str
    description: Optional[str] = None
    icon: Optional[str] = None
