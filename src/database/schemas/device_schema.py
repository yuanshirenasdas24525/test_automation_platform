from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict


DeviceStatus = Literal["idle", "busy", "offline"]
Platform = Literal["Android", "iOS"]


class DeviceRead(BaseModel):
    id: int
    udid: str
    platform: Platform
    platform_version: Optional[str] = None
    device_name: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    agent_host: Optional[str] = None
    agent_port: Optional[int] = None
    appium_port: Optional[int] = None
    pool: str = "default"
    status: DeviceStatus = "offline"
    owner_execution_id: Optional[int] = None
    capabilities: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None
    last_heartbeat: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# ====== Agent <-> Platform 通信 ======

class _DeviceReport(BaseModel):
    """Agent 心跳包里的单个设备信息"""
    udid: str
    platform: Platform
    platform_version: Optional[str] = None
    device_name: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    appium_port: Optional[int] = None
    status: DeviceStatus = "idle"
    capabilities: Optional[Dict[str, Any]] = None


class DeviceHeartbeat(BaseModel):
    agent_host: str
    agent_port: Optional[int] = None
    devices: List[_DeviceReport]


class DeviceAcquireRequest(BaseModel):
    """AppRunner 向平台请求分配设备"""
    pool: str = "default"
    execution_id: int
    platform: Optional[Platform] = None
    min_version: Optional[str] = None
    tags: Optional[List[str]] = None


class DeviceAcquireResponse(BaseModel):
    device: DeviceRead
    appium_url: str     # 完整的 Appium Server URL，例如 http://agent-1:4733/wd/hub
