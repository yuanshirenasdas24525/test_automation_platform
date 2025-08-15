from src.core.api.request_data_processor import RequestDataProcessor
from src.utils.read_file import read_conf

def create_request_data_processor():
    return RequestDataProcessor(
        header_key=read_conf.get_dict("header"),
        host_key=read_conf.get_dict("host"),
        default_parameters=read_conf.get_dict("default_parameters"),
        ed=read_conf.get_dict("encryption_decryption")
    )
