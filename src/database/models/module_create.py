import pydantic
from typing import Optional



class ModuleCreate(pydantic.BaseModel):
    project_id: int
    parent_id: Optional[int] = None
    name: str