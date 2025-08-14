# file_handler.py
import os
from typing import Any, Dict, List, Union
from src.utils.logger import LOGGER, ERROR_LOGGER

class FileHandler:
    """
    处理文件上传路径解析和文件对象生成。
    """

    def process(self, file_obj: Union[List[str], str], extra_pool: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        处理文件对象，支持列表和单文件字符串。
        返回文件描述字典列表，包含文件名和文件路径。
        """
        files = []
        if not file_obj:
            return files
        try:
            if isinstance(file_obj, str):
                file_obj = [file_obj]
            for f in file_obj:
                path = self._parse_path(f, extra_pool)
                if not os.path.exists(path):
                    ERROR_LOGGER.error(f"File not found: {path}")
                    continue
                file_info = {
                    "filename": os.path.basename(path),
                    "filepath": path
                }
                files.append(file_info)
            LOGGER.debug(f"FileHandler processed files: {files}")
            return files
        except Exception as e:
            ERROR_LOGGER.error(f"Error in FileHandler.process: {e}")
            return []

    def _parse_path(self, path_str: str, extra_pool: Dict[str, Any]) -> str:
        """
        解析文件路径，支持表达式替换。
        """
        if not isinstance(path_str, str):
            ERROR_LOGGER.error(f"_parse_path expects a string input, got {type(path_str)}")
            return path_str
        try:
            # 简单替换表达式格式${var}
            for key, val in extra_pool.items():
                placeholder = f"${{{key}}}"
                if placeholder in path_str:
                    path_str = path_str.replace(placeholder, str(val))
            abs_path = os.path.abspath(path_str)
            LOGGER.debug(f"_parse_path input: {path_str}, output: {abs_path}")
            return abs_path
        except Exception as e:
            ERROR_LOGGER.error(f"Error in _parse_path: {e}")
            return path_str