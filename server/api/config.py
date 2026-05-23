"""
/api/config/* 配置中心 — 支持 per-project 配置 + 全局模板回退。

接口：
  - GET  /config/all             查询（可按 category / project_id 筛选）
  - GET  /config/schema/{category} 返回推荐配置项清单
  - POST /config/save            upsert（有则改无则增）
  - POST /config/add             insert
  - DELETE /config/delete/{id}   删除
  - POST /config/copy-from-global 从全局模板导入到项目
  - POST /config/test-ai-model   测试项目级 AI 模型连通性

project_id=None → 全局模板，仅用于拷贝。
project_id=int → 项目专属配置，查询时自动回退到全局模板未覆盖的项。
"""
from __future__ import annotations

from typing import Optional

import pydantic
from fastapi import APIRouter, HTTPException, Query

from server.api.deps import DBDep
from database.models import ConfigStore, ConfigUpdateItem
from utils.reload_config import config_center

router = APIRouter(prefix="/config", tags=["config"])


# ---------------------------------------------------------------------------
# Schema / 推荐配置项
# ---------------------------------------------------------------------------
@router.get("/schema/{category}")
async def get_config_schema(category: str, project_id: int | None = Query(None)):
    from server.api.config_schemas import get_schema

    return {"status": "success", "data": get_schema(category)}


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
@router.get("/all")
async def get_all_configs(
    db: DBDep,
    category: Optional[str] = Query(None),
    project_id: int | None = Query(None),
):
    """查询配置。

    project_id 不为 None 时：返回该项目专属配置 + 全局模板中该项目未覆盖的项（去重）。
    project_id 为 None 时：只返回全局模板（project_id IS NULL）。
    """
    if project_id is not None:
        sql = (
            "SELECT DISTINCT ON (config_group, config_key) * FROM config_store "
            "WHERE project_id = :pid"
            + (" AND category = :cat" if category else "")
            + " OR (project_id IS NULL AND (config_group, config_key) NOT IN "
            "   (SELECT config_group, config_key FROM config_store WHERE project_id = :pid))"
            + (" AND category = :cat" if category else "")
            + " ORDER BY config_group, config_key, project_id NULLS LAST"
        )
        params = {"pid": project_id}
        if category:
            params["cat"] = category
    else:
        sql = "SELECT * FROM config_store WHERE project_id IS NULL"
        params = {}
        if category:
            sql += " AND category = :cat"
            params["cat"] = category
        sql += " ORDER BY config_group"

    data = db.sql.query(sql, params)
    return {"status": "success", "data": data}


# ---------------------------------------------------------------------------
# 写
# ---------------------------------------------------------------------------
@router.post("/save")
async def save_configs(configs: ConfigUpdateItem, db: DBDep):
    if not configs.category:
        raise HTTPException(status_code=400, detail="category 不能为空")
    if not configs.config_group or not configs.config_key:
        raise HTTPException(status_code=400, detail="Group 和 Key 不能为空")

    db_item = (
        db.session.query(ConfigStore)
        .filter(
            ConfigStore.config_group == configs.config_group,
            ConfigStore.config_key == configs.config_key,
            ConfigStore.category == configs.category,
            ConfigStore.project_id == configs.project_id,
        )
        .first()
    )

    if db_item:
        db_item.config_value = configs.config_value
    else:
        db.session.add(
            ConfigStore(
                config_group=configs.config_group,
                config_key=configs.config_key,
                config_value=configs.config_value,
                category=configs.category,
                project_id=configs.project_id,
            )
        )

    db.session.flush()
    config_center.reload(db.sql)
    return {"status": "success", "message": "保存成功"}


@router.post("/add")
def add_config(item: ConfigUpdateItem, db: DBDep):
    if not item.config_group:
        raise HTTPException(status_code=400, detail="config_group 不能为空")

    sql = (
        "INSERT INTO config_store (config_group, config_key, config_value, category, project_id) "
        "VALUES (:g, :k, :v, :c, :pid)"
    )
    params = {
        "g": item.config_group,
        "k": item.config_key,
        "v": item.config_value,
        "c": item.category or "api",
        "pid": item.project_id,
    }
    db.sql.execute(sql, params)
    config_center.reload(db.sql)
    return {"status": "success"}


@router.delete("/delete/{config_id}")
async def delete_config(config_id: int, db: DBDep):
    row = (
        db.session.query(ConfigStore).filter(ConfigStore.id == config_id).first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="配置项不存在")
    db.session.delete(row)
    db.session.flush()
    config_center.reload(db.sql)
    return {"status": "success"}


# ---------------------------------------------------------------------------
# 从全局模板导入到项目
# ---------------------------------------------------------------------------
class CopyFromGlobalRequest(pydantic.BaseModel):
    project_id: int
    categories: list[str] | None = None


@router.post("/copy-from-global")
async def copy_from_global(payload: CopyFromGlobalRequest, db: DBDep):
    """将全局模板（project_id IS NULL）的配置拷贝到指定项目。

    已经存在的项目配置不会被覆盖。
    """
    project_id = payload.project_id
    categories = payload.categories

    sql = "SELECT * FROM config_store WHERE project_id IS NULL"
    params = {}
    if categories:
        cat_list = ",".join(f"'{c}'" for c in categories)
        sql += f" AND category IN ({cat_list})"

    global_rows = db.sql.query(sql, params)

    copied = 0
    for row in global_rows:
        existing = (
            db.session.query(ConfigStore)
            .filter(
                ConfigStore.config_group == row["config_group"],
                ConfigStore.config_key == row["config_key"],
                ConfigStore.category == row["category"],
                ConfigStore.project_id == project_id,
            )
            .first()
        )
        if existing:
            continue

        db.session.add(
            ConfigStore(
                config_group=row["config_group"],
                config_key=row["config_key"],
                config_value=row["config_value"],
                category=row["category"],
                project_id=project_id,
            )
        )
        copied += 1

    db.session.flush()
    config_center.reload(db.sql)
    return {"status": "success", "data": {"copied": copied}}


# ---------------------------------------------------------------------------
# 测试项目级 AI 模型连通性
# ---------------------------------------------------------------------------
class TestAiModelRequest(pydantic.BaseModel):
    project_id: int
    model_name: str


@router.post("/test-ai-model")
async def test_project_ai_model(payload: TestAiModelRequest, db: DBDep):
    """测试项目级 AI 模型是否可连通。"""
    rows = db.sql.query(
        "SELECT config_key, config_value FROM config_store "
        "WHERE category = 'ai' AND config_group = :name AND project_id = :pid",
        {"name": payload.model_name, "pid": payload.project_id},
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"模型 {payload.model_name!r} 不存在")

    kvs: dict[str, str] = {}
    for r in rows:
        kvs[r["config_key"]] = r["config_value"]

    provider = kvs.get("provider", "")
    model = kvs.get("model", "")
    if not provider or not model:
        raise HTTPException(status_code=400, detail="模型配置不完整")

    try:
        from ai_gateway.gateway import provider_ping
        from database.schemas.ai_config import AiModelConfig

        cfg = AiModelConfig(
            name=payload.model_name, provider=provider, model=model,
            base_url=kvs.get("base_url") or None, api_key=kvs.get("api_key") or None,
            supports_vision=False, is_default=False, enabled=True, extra={},
        )
        ping_result = provider_ping(cfg)
        return {"status": "success", "data": {"ok": True, "result": ping_result}}
    except Exception as exc:
        return {"status": "success", "data": {"ok": False, "error": str(exc)[:500]}}
