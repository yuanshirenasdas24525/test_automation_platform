# coding: utf-8
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
                # 支持逗号分隔的多文件字符串
                file_obj = [f.strip() for f in file_obj.split(";")]
            for f in file_obj:
                path = self._parse_path(f, extra_pool)
                if not os.path.exists(path):
                    ERROR_LOGGER.error(f"找不到文件: {path}")
                    continue
                file_info = {
                    "filename": os.path.basename(path),
                    "filepath": path
                }
                files.append(file_info)
            LOGGER.debug(f"FileHandler 已处理的文件: {files}")
            return files
        except Exception as e:
            ERROR_LOGGER.error(f"FileHandler.process 中的错误: {e}")
            return []

    def _parse_path(self, path_str: str, extra_pool: Dict[str, Any]) -> str:
        """
        解析文件路径，支持表达式替换。
        """
        if not isinstance(path_str, str):
            ERROR_LOGGER.error(f"_parse_path需要一个字符串输入，得到{type(path_str)}")
            return path_str
        try:
            # 简单替换表达式格式${var}
            for key, val in extra_pool.items():
                placeholder = f"${{{key}}}"
                if placeholder in path_str:
                    path_str = path_str.replace(placeholder, str(val))
            abs_path = os.path.abspath(path_str)
            LOGGER.debug(f"_parse_path输入: {path_str}, 输出: {abs_path}")
            return abs_path
        except Exception as e:
            ERROR_LOGGER.error(f"_parse_path错误: {e}")
            return path_str