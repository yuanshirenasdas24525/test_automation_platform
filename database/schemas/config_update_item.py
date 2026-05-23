from __future__ import annotations

from typing import Optional

import pydantic


class ConfigUpdateItem(pydantic.BaseModel):
    config_group: str
    config_key: str
    config_value: str
    category: str
    project_id: Optional[int] = None