# expression_handler.py
import json
import re
from typing import Any, Dict, List, Union
from jsonpath import jsonpath
from src.utils.logger import LOGGER, ERROR_LOGGER

class ExpressionHandler:
    """
    处理表达式替换、jsonpath提取、验证码提取及json转换的工具类。
    """

    def rep_expr(self, text: str, extra_pool: Dict[str, Any]) -> str:
        """
        替换文本中的表达式变量，格式为${var}，从extra_pool中取值替换。
        """
        if not isinstance(text, str):
            ERROR_LOGGER.error(f"rep_expr expects a string input, got {type(text)}")
            return text

        pattern = re.compile(r'\$\{(.*?)\}')
        def replacer(match):
            key = match.group(1)
            value = extra_pool.get(key, match.group(0))
            return str(value)
        try:
            result = pattern.sub(replacer, text)
            LOGGER.debug(f"rep_expr input: {text}, output: {result}")
            return result
        except Exception as e:
            ERROR_LOGGER.error(f"Error in rep_expr: {e}")
            return text

    def extractor(self, json_obj: Union[Dict, List], json_path: str) -> Any:
        """
        使用jsonpath表达式从json对象中提取数据。
        """
        try:
            matches = jsonpath(json_obj, json_path)
            if not matches:
                return None
            if len(matches) == 1:
                return matches[0]
            return matches
        except Exception as e:
            ERROR_LOGGER.error(f"提取器错误: {e}| json_path: {json_path}")
            return None

    def extract_code(self, text: str, pattern: str) -> Union[str, None]:
        """
        从文本中使用正则表达式提取验证码。
        """
        try:
            match = re.search(pattern, text)
            if match:
                code = match.group(1)
                LOGGER.debug(f"extract_code pattern: {pattern}, code: {code}")
                return code
            return None
        except Exception as e:
            ERROR_LOGGER.error(f"Error in extract_code: {e}")
            return None

    def convert_json(self, data: str) -> Any:
        """
        将字符串转换为json对象，支持dict和list。
        """
        if not isinstance(data, str):
            ERROR_LOGGER.error(f"convert_json expects a string input, got {type(data)}")
            return data
        try:
            obj = json.loads(data)
            LOGGER.debug(f"convert_json input: {data}, output: {obj}")
            return obj
        except Exception as e:
            ERROR_LOGGER.error(f"Error in convert_json: {e}")
            return data
