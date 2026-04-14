import pydantic
from typing import Optional


class ProjectCreate(pydantic.BaseModel):
    name: str
    description: Optional[str]
    icon: Optional[str]
    type: str