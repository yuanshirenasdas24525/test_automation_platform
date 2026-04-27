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

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from server.api.deps import DBDep
from database.models import ConfigStore, ConfigUpdateItem
from utils.reload_config import config_center

router = APIRouter(prefix="/config", tags=["config"])


@router.get("/schema/{category}")
async def get_config_schema(category: str):
    """返回某个分类的"推荐配置项"清单，给前端做提示面板用。

    支持的 category：
      - api  → host / mysql_db / redis / default_parameters / encryption_decryption / headers
      - app  → 黑名单（udids / app_packages / bundle_ids）+ session 默认开关 + probe 节奏
      - web  → 浏览器引擎 / headless / viewport / 超时等（来自 WEB_CONFIG_SCHEMA）
      - 其它 → 空数组（前端会自动隐藏面板）
    """
    from server.api.config_schemas import get_schema

    return {"status": "success", "data": get_schema(category)}


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
        # 注意：ConfigStore 没有 update_time 列；以前这一行是无效赋值（SQLAlchemy 静默忽略）。
        # 如果要审计修改时间，请先在迁移里加列再开启。
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
    """删配置项。

    历史上这里有 is_system 保护（host / mysql / redis / default_parameters /
    encryption_decryption 五节禁删），但既然推荐配置改成了"用户自助一键填入"，
    所有配置项都允许用户自由删除。is_system 列已通过 alembic 删除。
    """
    row = (
        db.session.query(ConfigStore).filter(ConfigStore.id == config_id).first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="配置项不存在")
    db.session.delete(row)
    db.session.flush()
    config_center.reload(db.sql)
    return {"status": "success"}
