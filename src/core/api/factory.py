from src.core.api.request_data_processor import RequestDataProcessor
from src.core.api.api_client import ApiClient
from src.utils.reload_config import config_center
from src.database.db import DB

def create_request_data_processor():
    platform_db = DB()
    config_center.reload(db=platform_db.sql, category="api")
    return RequestDataProcessor(
        header_key=config_center.get("header"),
        host_key=config_center.get("host"),
        default_parameters=config_center.get("default_parameters"),
        ed=config_center.get("encryption_decryption"),
        db=DB(config_center.get("target_db")) if config_center.get("target_db") else None,
    )

def create_api_client(record_property):
    """
    将 RequestDataProcessor 注入 ApiClient
    """
    processor = create_request_data_processor()
    return ApiClient(processor, record_property)