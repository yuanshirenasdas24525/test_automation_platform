"""
/api/config/* 配置中心 — 项目级配置。

接口：
  - GET  /config/all             查询（必须传 project_id，可按 category 筛选）
  - GET  /config/schema/{category} 返回推荐配置项清单
  - POST /config/save            upsert（有则改无则增）
  - POST /config/add             insert
  - DELETE /config/delete/{id}   删除
  - POST /config/test-ai-model   测试项目级 AI 模型连通性

所有配置必须属于具体项目；不再维护全局模板。
"""
from __future__ import annotations

import pydantic
from fastapi import APIRouter, HTTPException, Query

from server.api.deps import DBDep, RequireAdmin
from database.models import ConfigStore, ConfigUpdateItem
from utils.reload_config import config_center, is_database_config

router = APIRouter(prefix="/config", tags=["config"])

# ---------------------------------------------------------------------------
# Schema / 推荐配置项
# ---------------------------------------------------------------------------
@router.get("/schema/{category}")
async def get_config_schema(category: str):
    from server.api.config_schemas import get_schema

    return {"status": "success", "data": get_schema(category)}


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
@router.get("/database-connections")
async def get_database_connections(
    db: DBDep,
    project_id: int = Query(...),
):
    """返回项目 API 配置中的数据库连接组，按首次配置时间排序。

    只返回组名和展示信息，不返回账号、密码等连接明细。
    """
    rows = db.sql.query(
        "SELECT id, config_group, config_key, config_value FROM config_store "
        "WHERE project_id = :pid AND category = 'api' ORDER BY id ASC",
        {"pid": project_id},
    )
    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        group = str(row["config_group"] or "").strip()
        if not group:
            continue
        current = grouped.setdefault(group, {"first_id": row["id"], "values": {}})
        values = current["values"]
        if isinstance(values, dict):
            values[str(row["config_key"] or "").strip().lower()] = row["config_value"]

    connections = [
        {"name": group, "label": group, "first_config_id": item["first_id"]}
        for group, item in grouped.items()
        if isinstance(item["values"], dict) and is_database_config(group, item["values"])
    ]
    connections.sort(key=lambda item: item["first_config_id"])
    return {"status": "success", "data": connections}


@router.get("/all")
async def get_all_configs(
    db: DBDep,
    category: str | None = Query(None),
    project_id: int | None = Query(None),
):
    """查询某个项目的配置。"""
    if project_id is None:
        raise HTTPException(status_code=400, detail="project_id 必填")

    sql = "SELECT * FROM config_store WHERE project_id = :pid"
    params = {"pid": project_id}
    if category:
        sql += " AND category = :cat"
        params["cat"] = category
    sql += " ORDER BY config_group, config_key"

    data = db.sql.query(sql, params)
    return {"status": "success", "data": data}


# ---------------------------------------------------------------------------
# 写
# ---------------------------------------------------------------------------
@router.post("/save", dependencies=[RequireAdmin])
async def save_configs(configs: ConfigUpdateItem, db: DBDep):
    if not configs.category:
        raise HTTPException(status_code=400, detail="category 不能为空")
    if not configs.config_group or not configs.config_key:
        raise HTTPException(status_code=400, detail="Group 和 Key 不能为空")
    if configs.project_id is None:
        raise HTTPException(status_code=400, detail="project_id 必填")

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


@router.post("/add", dependencies=[RequireAdmin])
def add_config(item: ConfigUpdateItem, db: DBDep):
    if not item.config_group:
        raise HTTPException(status_code=400, detail="config_group 不能为空")
    if item.project_id is None:
        raise HTTPException(status_code=400, detail="project_id 必填")

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


@router.delete("/delete/{config_id}", dependencies=[RequireAdmin])
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
        from database.schemas.ai_config import AiModelConfig
        from server.services.cli_case_enhancement_service import (
            check_cli_agent,
            is_cli_case_provider,
        )

        cfg = AiModelConfig(
            name=payload.model_name, provider=provider, model=model,
            base_url=kvs.get("base_url") or None, api_key=kvs.get("api_key") or None,
            supports_vision=False, is_default=False, enabled=True, extra={},
        )
        if is_cli_case_provider(cfg.provider):
            checked = check_cli_agent(cfg)
            return {
                "status": "success",
                "data": {
                    "ok": bool(checked["ok"]),
                    "result": checked.get("sample"),
                    "error": checked.get("error"),
                },
            }

        from ai_gateway.gateway import provider_ping

        ping_result = provider_ping(cfg)
        return {"status": "success", "data": {"ok": True, "result": ping_result}}
    except Exception as exc:
        return {"status": "success", "data": {"ok": False, "error": str(exc)[:500]}}


# ---------------------------------------------------------------------------
# 测试 RAG Embedding 模型连通性
# ---------------------------------------------------------------------------
class TestEmbeddingRequest(pydantic.BaseModel):
    project_id: int


@router.post("/test-embedding")
async def test_embedding_model(payload: TestEmbeddingRequest, db: DBDep):
    """测试项目级 RAG Embedding 模型是否可连通。"""
    rows = db.sql.query(
        "SELECT config_key, config_value FROM config_store "
        "WHERE category = 'ai' AND config_group = 'rag_embedding' AND project_id = :pid",
        {"pid": payload.project_id},
    )
    if not rows:
        return {
            "status": "success",
            "data": {"ok": False, "error": "未配置 Embedding 模型（config_group=rag_embedding）"},
        }

    kvs: dict[str, str] = {}
    for r in rows:
        kvs[r["config_key"]] = r["config_value"]

    provider = kvs.get("provider", "")
    model = kvs.get("model", "")
    if not provider or not model:
        return {
            "status": "success",
            "data": {"ok": False, "error": "Embedding 配置不完整：缺少 provider 或 model"},
        }

    try:
        from ai_gateway.embeddings import EmbeddingConfig, embed_texts

        cfg = EmbeddingConfig(
            provider=provider,
            model=model,
            api_key=kvs.get("api_key") or None,
            base_url=kvs.get("base_url") or None,
            dim=int(kvs.get("dim") or 768),
        )
        vectors, _tokens = embed_texts(["hello"], cfg=cfg, timeout=15)
        dim = len(vectors[0]) if vectors else 0
        return {
            "status": "success",
            "data": {"ok": True, "result": f"连通成功，向量维度 {dim}"},
        }
    except Exception as exc:
        return {"status": "success", "data": {"ok": False, "error": str(exc)[:500]}}
