from src.core.api.request_data_processor import RequestDataProcessor
from src.core.api.api_client import ApiClient
from src.common.context import ctx

def create_request_data_processor():
    ctx.warm_up(category="api")
    return RequestDataProcessor(
        header_key=ctx.config.get("header"),
        host_key=ctx.config.get("host"),
        default_parameters=ctx.config.get("default_parameters"),
        ed=ctx.config.get("encryption_decryption"),
        db=ctx.db
    )

def create_api_client():
    """
    将 RequestDataProcessor 注入 ApiClient
    """
    processor = create_request_data_processor()
    return ApiClient(processor)