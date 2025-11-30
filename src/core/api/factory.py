from src.core.api.request_data_processor import RequestDataProcessor
from src.core.api.api_client import ApiClient
from src.utils.read_test_cases import read_conf

def create_request_data_processor():
    return RequestDataProcessor(
        header_key=read_conf.get_dict("header"),
        host_key=read_conf.get_dict("host"),
        default_parameters=read_conf.get_dict("default_parameters"),
        ed=read_conf.get_dict("encryption_decryption")
    )

def create_api_client():
    """
    将 RequestDataProcessor 注入 ApiClient
    """
    processor = create_request_data_processor()
    return ApiClient(processor)