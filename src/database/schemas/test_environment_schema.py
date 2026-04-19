from __future__ import annotations

from typing import Any, Dict, Literal, Optional
from pydantic import BaseModel, ConfigDict


EnvCategory = Literal["api", "app", "web", "mixed"]


class _TestEnvironmentBase(BaseModel):
    name: str
    category: Optional[EnvCategory] = None
    description: Optional[str] = None
    host: Optional[str] = None
    device_pool: Optional[str] = None
    browser_config: Optional[Dict[str, Any]] = None
    variables: Optional[Dict[str, Any]] = None
    secrets: Optional[Dict[str, Any]] = None


class TestEnvironmentCreate(_TestEnvironmentBase):
    project_id: int


class TestEnvironmentUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[EnvCategory] = None
    description: Optional[str] = None
    host: Optional[str] = None
    device_pool: Optional[str] = None
    browser_config: Optional[Dict[str, Any]] = None
    variables: Optional[Dict[str, Any]] = None
    secrets: Optional[Dict[str, Any]] = None


class TestEnvironmentRead(_TestEnvironmentBase):
    id: int
    project_id: int

    model_config = ConfigDict(from_attributes=True)
