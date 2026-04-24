"""/api/devices/* 路由。

核心职责：
  - 维护 `devices` 表（列表 / 注册 / 更新 / 删除）；
  - 给运行时提供 `acquire` / `release`；前者走 DevicePool 的乐观锁，
    后者是幂等的 busy→idle。

路由前缀：/devices。main.py 里由 include_router(..., prefix="/api") 再加一层 /api。

返回信封统一 `{"status": "success", "data": ...}`，和其它接口保持一致。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from server.api.deps import DBDep
from database.models import (
    DEVICE_STATUS_BUSY,
    DEVICE_STATUS_IDLE,
    DEVICE_STATUS_OFFLINE,
    Device,
)
from database.schemas.device_schema import (
    DeviceAcquireRequest,
    DeviceAcquireResponse,
    DeviceRead,
)

router = APIRouter(prefix="/devices", tags=["devices"])


# ---------------------------------------------------------------------------
# 入参 Schema（写操作用）
# ---------------------------------------------------------------------------
class DeviceUpsert(BaseModel):
    """注册 / 编辑设备时的请求体。

    - POST / 创建：除 udid/platform 外都可以为空，后端会填默认值；
    - PUT /{id}：允许部分字段更新（None 视为"不改"）。
    """

    udid: str = Field(..., min_length=1, max_length=128)
    platform: str = Field(..., description="Android 或 iOS（大小写不敏感）")
    platform_version: Optional[str] = None
    device_name: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    agent_host: Optional[str] = None
    agent_port: Optional[int] = None
    appium_port: Optional[int] = None
    pool: Optional[str] = "default"
    status: Optional[str] = None  # idle/busy/offline；不传则沿用或默认 offline
    capabilities: Optional[dict] = None
    tags: Optional[List[str]] = None

    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
_ALLOWED_STATUSES = {DEVICE_STATUS_IDLE, DEVICE_STATUS_BUSY, DEVICE_STATUS_OFFLINE}


def _normalize_platform(p: str) -> str:
    """统一 platform 大小写：Android / iOS。"""
    v = (p or "").strip().lower()
    if v in ("android",):
        return "Android"
    if v in ("ios",):
        return "iOS"
    # 原样返回给上层自己决定 —— 但做个长度兜底
    return (p or "").strip()[:20]


def _serialize(dev: Device) -> dict:
    return DeviceRead.model_validate(dev).model_dump(mode="json")


# ---------------------------------------------------------------------------
# 查询类接口
# ---------------------------------------------------------------------------
@router.get("/list")
def list_devices(
    db: DBDep,
    pool: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
):
    """按 pool / platform / status 过滤。任一参数都可以省略。"""
    q = db.session.query(Device)
    if pool:
        q = q.filter(Device.pool == pool)
    if platform:
        # 大小写不敏感
        q = q.filter(Device.platform.ilike(platform))
    if status:
        q = q.filter(Device.status == status.lower())
    items = [_serialize(d) for d in q.order_by(Device.id).all()]
    return {"status": "success", "data": items}


@router.get("/pools")
def list_pools(db: DBDep):
    """返回所有已存在的 pool 名去重列表，前端下拉用。"""
    rows = (
        db.session.query(Device.pool)
        .filter(Device.pool.isnot(None))
        .distinct()
        .all()
    )
    pools = sorted({(r[0] or "default") for r in rows})
    if "default" not in pools:
        pools = ["default", *pools]
    return {"status": "success", "data": pools}


@router.get("/{device_id}")
def get_device(device_id: int, db: DBDep):
    dev = db.session.query(Device).filter(Device.id == device_id).first()
    if dev is None:
        raise HTTPException(status_code=404, detail="设备不存在")
    return {"status": "success", "data": _serialize(dev)}


# ---------------------------------------------------------------------------
# 写操作：注册 / 更新 / 删除
# ---------------------------------------------------------------------------
@router.post("")
def create_device(body: DeviceUpsert, db: DBDep):
    """注册一台设备。udid 必须唯一，已存在则返回 409。"""
    existing = db.session.query(Device).filter(Device.udid == body.udid).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"udid={body.udid} 已存在")

    status = (body.status or DEVICE_STATUS_OFFLINE).lower()
    if status not in _ALLOWED_STATUSES:
        raise HTTPException(status_code=400, detail=f"非法 status={status}")

    dev = Device(
        udid=body.udid.strip(),
        platform=_normalize_platform(body.platform),
        platform_version=body.platform_version,
        device_name=body.device_name,
        brand=body.brand,
        model=body.model,
        agent_host=body.agent_host,
        agent_port=body.agent_port,
        appium_port=body.appium_port,
        pool=(body.pool or "default").strip() or "default",
        status=status,
        capabilities=body.capabilities or None,
        tags=body.tags or None,
    )
    db.session.add(dev)
    db.session.flush()
    db.session.refresh(dev)
    return {"status": "success", "data": _serialize(dev)}


@router.put("/{device_id}")
def update_device(device_id: int, body: DeviceUpsert, db: DBDep):
    """部分更新：None 字段不改。status 允许显式设置为 idle/offline，但不允许直接改成 busy
    —— busy 只由 acquire 走。
    """
    dev = db.session.query(Device).filter(Device.id == device_id).first()
    if dev is None:
        raise HTTPException(status_code=404, detail="设备不存在")

    # udid 改了要保持唯一
    if body.udid and body.udid != dev.udid:
        conflict = (
            db.session.query(Device)
            .filter(Device.udid == body.udid, Device.id != device_id)
            .first()
        )
        if conflict is not None:
            raise HTTPException(status_code=409, detail=f"udid={body.udid} 已存在")
        dev.udid = body.udid.strip()

    if body.platform:
        dev.platform = _normalize_platform(body.platform)
    for attr in (
        "platform_version",
        "device_name",
        "brand",
        "model",
        "agent_host",
        "agent_port",
        "appium_port",
        "capabilities",
        "tags",
    ):
        val = getattr(body, attr)
        if val is not None:
            setattr(dev, attr, val)

    if body.pool is not None:
        dev.pool = (body.pool or "default").strip() or "default"

    if body.status is not None:
        status = body.status.lower()
        if status not in _ALLOWED_STATUSES:
            raise HTTPException(status_code=400, detail=f"非法 status={status}")
        if status == DEVICE_STATUS_BUSY:
            raise HTTPException(
                status_code=400,
                detail="不允许直接把设备改成 busy，busy 只由 /acquire 设置",
            )
        dev.status = status
        if status == DEVICE_STATUS_IDLE:
            dev.owner_execution_id = None

    db.session.flush()
    db.session.refresh(dev)
    return {"status": "success", "data": _serialize(dev)}


@router.delete("/{device_id}")
def delete_device(device_id: int, db: DBDep):
    dev = db.session.query(Device).filter(Device.id == device_id).first()
    if dev is None:
        raise HTTPException(status_code=404, detail="设备不存在")
    if dev.status == DEVICE_STATUS_BUSY:
        raise HTTPException(
            status_code=409,
            detail="设备正被占用（busy），请先 release 再删除",
        )
    db.session.delete(dev)
    db.session.flush()
    return {"status": "success"}


# ---------------------------------------------------------------------------
# 运行时：acquire / release
# ---------------------------------------------------------------------------
@router.post("/acquire", response_model=None)
def acquire_device(body: DeviceAcquireRequest):
    """测试运行时调用：idle→busy。
    通常不是前端直接用，而是给外部运行器 / 手动排障用。
    """
    from runners.app.device_pool import DevicePool

    pool = DevicePool.default()
    device = pool.acquire(
        pool_name=body.pool or "default",
        platform=body.platform,
        execution_id=body.execution_id,
    )
    if device is None:
        raise HTTPException(
            status_code=409,
            detail=f"pool={body.pool} platform={body.platform} 没有 idle 设备",
        )

    # 用 runners 里同一套 base_path 解析逻辑，保证 UI 里显示的 URL 和
    # driver 实际连的 URL 一致（Appium 2 默认 '/'；老环境可在 capabilities
    # 或 APPIUM_BASE_PATH 里指定 /wd/hub）
    from runners.app.session import _build_appium_url
    appium_url = _build_appium_url(device)

    # 把 datetime 序列化成 str，免得 pydantic 报 type error
    last_hb = device.get("last_heartbeat")
    if isinstance(last_hb, datetime):
        device = {**device, "last_heartbeat": last_hb.isoformat()}

    resp = {
        "device": DeviceRead.model_validate(device).model_dump(mode="json"),
        "appium_url": appium_url,
    }
    return {"status": "success", "data": resp}


@router.post("/release/{device_id}")
def release_device(device_id: int):
    """归还设备：busy→idle。幂等。"""
    from runners.app.device_pool import DevicePool

    pool = DevicePool.default()
    ok = pool.release(device_id)
    if not ok:
        raise HTTPException(status_code=500, detail="release 失败（DB 异常）")
    return {"status": "success"}
