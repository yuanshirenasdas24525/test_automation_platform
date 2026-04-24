"""
/api/config/* 配置中心。

四个接口：
  - GET  /config/all         查询所有（可按 category 筛选）
  - POST /config/save        有则改、无则增（upsert）
  - POST /config/add         insert（用 raw SQL，保持老行为）
  - DELETE /config/delete/{id}

save 和 delete 完成后都会触发 `config_center.reload()` 做内存级热更新。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from server.api.deps import DBDep
from database.models import ConfigStore, ConfigUpdateItem
from utils.reload_config import config_center

router = APIRouter(prefix="/config", tags=["config"])


@router.get("/schema/{category}")
async def get_config_schema(category: str):
    """返回某个分类的"推荐配置项"清单，给前端做提示面板用。
    目前只支持 category=web；其它分类返回空数组（前端会隐藏面板）。
    """
    cat = (category or "").strip().lower()
    if cat == "web":
        # 惰性 import 避免在还没装 playwright 的纯 API 模式下报错
        try:
            from runners.web.session import WEB_CONFIG_SCHEMA
        except Exception:  # noqa: BLE001
            return {"status": "success", "data": []}
        # 补一个 config_group 字段方便前端点"填入"直接预填
        items = [
            {"config_group": "browser", **s} for s in WEB_CONFIG_SCHEMA
        ]
        return {"status": "success", "data": items}
    return {"status": "success", "data": []}


@router.get("/all")
async def get_all_configs(
    db: DBDep,
    category: Optional[str] = Query(None),
):
    if category:
        sql = (
            "SELECT * FROM config_store "
            "WHERE category = :category ORDER BY config_group"
        )
        params = {"category": category.lower()}
    else:
        sql = "SELECT * FROM config_store ORDER BY category, config_group"
        params = {}
    data = db.sql.query(sql, params)
    return {"status": "success", "data": data}


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
        )
        .first()
    )

    if db_item:
        db_item.config_value = configs.config_value
        db_item.update_time = datetime.now()
    else:
        db.session.add(
            ConfigStore(
                config_group=configs.config_group,
                config_key=configs.config_key,
                config_value=configs.config_value,
                category=configs.category,
            )
        )

    db.session.flush()
    config_center.reload(db.sql)  # 触发热更新
    return {"status": "success", "message": "保存成功"}


@router.post("/add")
def add_config(item: ConfigUpdateItem, db: DBDep):
    if not item.config_group:
        raise HTTPException(status_code=400, detail="config_group 不能为空")

    sql = (
        "INSERT INTO config_store (config_group, config_key, config_value, category) "
        "VALUES (:g, :k, :v, :c)"
    )
    params = {
        "g": item.config_group,
        "k": item.config_key,
        "v": item.config_value,
        "c": item.category or "api",
    }
    db.sql.execute(sql, params)
    return {"status": "success"}


@router.delete("/delete/{config_id}")
async def delete_config(config_id: int, db: DBDep):
    db.sql.execute("DELETE FROM config_store WHERE id = :id", {"id": config_id})
    config_center.reload(db.sql)
    return {"status": "success"}
