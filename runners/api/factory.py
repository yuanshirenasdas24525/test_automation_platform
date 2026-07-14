"""请求数据处理器的工厂。

v2 唯一路径：v2 的 http_request StepRunner 仍然复用 RequestDataProcessor 拿
base_url / header / 加密 / target_db 等配置（这部分是 v2 的，不是 v1）。

v1 时期的 `ApiClient` + `create_api_client` 已删除——v1 的"一条 case = 一个
HTTP 请求"入口在 v2 改造完成后没有用户路径。
"""
from runners.api.request_data_processor import RequestDataProcessor
from utils.reload_config import config_center
from utils.logger import LOGGER
from database.db import DB
from sqlalchemy import text


def create_request_data_processor(db=None, project_id: int | None = None):
    """构造 v2 http_request step 用的 RequestDataProcessor。

    Celery prefork 环境下 DB 连接可能因 fork 变 stale，这里显式 dispose engine
    再建新 session，保证能正常读 config_store。
    """
    if db is None:
        db = DB()
    else:
        # 外部传入的连接也做一下 pre-ping 保活
        try:
            db.session.execute(text("SELECT 1"))
        except Exception:
            db = DB()

    config_center.reload(db=db.sql, project_id=project_id, category="api")
    host = config_center.get("host", project_id=project_id)
    LOGGER.info(f"[factory] config_center host={host}")
    target_db = config_center.get("target_db", project_id=project_id)

    return RequestDataProcessor(
        header_key=config_center.get("header", project_id=project_id),
        host_key=host,
        default_parameters=config_center.get("default_parameters", project_id=project_id),
        ed=config_center.get("encryption_decryption", project_id=project_id),
        db=DB(target_db) if target_db else None,
    )
