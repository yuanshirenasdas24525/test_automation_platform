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
            ERROR_LOGGER.error(f"rep_expr需要一个字符串输入，得到 {type(text)}")
            return text

        pattern = re.compile(r'\$\{(.*?)\}')
        def replacer(match):
            key = match.group(1)
            value = extra_pool.get(key, match.group(0))
            return str(value)
        try:
            result = pattern.sub(replacer, text)
            LOGGER.debug(f"rep_expr 输入: {text}, 输出: {result}")
            return result
        except Exception as e:
            ERROR_LOGGER.error(f"rep_expr错误: {e}")
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
            return json_path

    def extract_code(self, text: str, pattern: str) -> Union[str, None]:
        """
        从文本中使用正则表达式提取验证码。
        """
        try:
            match = re.search(pattern, text)
            if match:
                code = match.group(1)
                LOGGER.debug(f"extract_code 模式: {pattern}, code: {code}")
                return code
            return None
        except Exception as e:
            ERROR_LOGGER.error(f"extract_code 错误: {e}")
            return None

    def convert_json(self, data: str) -> Any:
        """
        将字符串转换为json对象，支持dict和list。
        """
        if not isinstance(data, str):
            ERROR_LOGGER.error(f"convert_json 需要一个字符串输入，得到 {type(data)}")
            return data
        try:
            obj = json.loads(data)
            LOGGER.debug(f"convert_json 输入: {data}, 输出: {obj}")
            return obj
        except Exception as e:
            ERROR_LOGGER.error(f"convert_json 错误: {e}")
            return data
