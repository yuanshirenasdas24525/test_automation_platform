"""请求数据处理器的工厂。

v2 唯一路径：v2 的 http_request StepRunner 仍然复用 RequestDataProcessor 拿
base_url / header / 加密 / target_db 等配置（这部分是 v2 的，不是 v1）。

v1 时期的 `ApiClient` + `create_api_client` 已删除——v1 的"一条 case = 一个
HTTP 请求"入口在 v2 改造完成后没有用户路径。
"""
from core.api.request_data_processor import RequestDataProcessor
from utils.reload_config import config_center
from database.db import DB


def create_request_data_processor(db=None):
    """构造 v2 http_request step 用的 RequestDataProcessor。

    db 不传时自建一个临时 DB 连接做 config_center.reload；外部传 db 时，
    复用它（避免在 worker 进程里重复连接）。
    """
    if db is None:
        db = DB()

    config_center.reload(db=db.sql, category="api")

    return RequestDataProcessor(
        header_key=config_center.get("header"),
        host_key=config_center.get("host"),
        default_parameters=config_center.get("default_parameters"),
        ed=config_center.get("encryption_decryption"),
        db=DB(config_center.get("target_db")) if config_center.get("target_db") else None,
    )