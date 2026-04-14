import pydantic
from typing import Optional



class ConfigItem(pydantic.BaseModel):
    id: Optional[int] = None
    config_group: str
    config_key: str
    config_value: str
    value_type: str = "str"
    category: str
    description: Optional[str] = ""