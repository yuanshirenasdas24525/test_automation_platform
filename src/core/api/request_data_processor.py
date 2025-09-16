
from typing import Any, List
from src.core.api.expression_handler import ExpressionHandler
from src.core.api.file_handler import FileHandler
from src.utils.function_executor import exec_func
from src.utils.sql_handler import SQLHandlerFactory
from src.utils.read_file import read_conf
from src.utils.logger import LOGGER, ERROR_LOGGER
from src.utils.allure_utils import add_allure_step
import json



class RequestDataProcessor:
    """
    处理请求数据的类，负责路径、请求头、请求数据和文件的处理。
    """

    def __init__(self, header_key, host_key, ed=None, default_parameters=None):
        """
        初始化所需的处理器和配置字典。
        """
        self.expr = ExpressionHandler()
        self.file_handler = FileHandler()
        self.encryption_decryption = self.expr.rep_expr(ed, {}) if isinstance(ed, str) else ed or {}
        self.base_header = self.expr.rep_expr(header_key, {}) if isinstance(header_key, str) else header_key or {}
        self.base_url = self.expr.rep_expr(host_key, {}) if isinstance(host_key, str) else host_key or {}
        self.extra_pool = self.expr.rep_expr(default_parameters, {}) if isinstance(default_parameters, str) else default_parameters or {}

    def handler_path(self, path_str: str) -> str:
        """
        处理请求路径，支持完整url或拼接base_url。
        """
        if path_str.startswith(('http://', 'https://')):
            return self.expr.rep_expr(path_str, self.extra_pool)
        return self.base_url.get('url', '') + self.expr.rep_expr(path_str, self.extra_pool)

    def handler_header(self, header_str: str, data: str, sql: str) -> dict:
        """
        处理请求头，合并base_header和传入header，支持加密处理。
        """
        if not header_str:
            header_str = '{}'
        headers = {**self.base_header, **self.handler_data(header_str, sql)}
        # 请求头参数加密
        if self.encryption_decryption.get('on_off'):
            variable = self.handler_data(data, sql)
            from src.utils.encryption_handler import ParameterEncryption
            pe = ParameterEncryption(data=variable, power_access_key=self.encryption_decryption['key'])
            headers.update(pe.ed_header())
        return headers

    def handler_data(self, variable: str, sql: str, extra_str: str = None) -> Any:
        """
        处理请求数据，替换表达式并执行函数。
        """
        if not variable:
            return {}

        try:
            variable = self.expr.rep_expr(variable, self.extra_pool)
            data_obj = self.expr.convert_json(variable)
        except Exception:
            return {}

        if isinstance(data_obj, dict):
            self._process_functions(data_obj, sql, extra_str)
        return data_obj

    def _process_functions(self, data: dict, sql: str, extra_str: str):
        """
        处理字典中以 function: 开头的值，执行对应函数。
        自动传入 sql 查询结果（可能是多个）、当前变量和参数池。
        """
        sql_results = self.execute_select_fetchone(sql, extra_str)  # 返回 list
        def _process(item: dict, parent_data):
            if isinstance(item, dict):
                for k, v in item.items():
                    item[k] = _process(v, item)  # 递归，并传当前 dict 给子节点
                return item
            elif isinstance(item, list):
                for i, v in enumerate(item):
                    item[i] = _process(v, item)  # 递归，并传当前 list 给子节点
                return item
            elif isinstance(item, str) and item.startswith("function:"):
                return exec_func(item, sql_results, parent_data, self.extra_pool)
            return item

        return _process(data, data)

    def handler_files(self, file_obj: Any) -> List[dict]:
        """
        处理文件上传，返回文件信息列表。
        """
        return self.file_handler.process(file_obj, self.extra_pool)

    def handler_extra(self, extra_str: str, response: dict) -> None:
        """
        从响应中提取参数，加入 extra_pool
        """
        if not extra_str:
            return
        extra_dict = self.expr.convert_json(extra_str)
        for k, v in extra_dict.items():
            if isinstance(v, str) and v.startswith("function:"):
                self.extra_pool[k] = exec_func(v)
            extracted_value = self.expr.extractor(response, v)
            if extracted_value is not None:
                self.extra_pool[k] = extracted_value

    def assert_result(self, response: dict, expect_str: str) -> None:
        """
        断言响应与预期是否一致
        """
        add_allure_step("当前可用参数池", self.extra_pool)
        expect_str = self.expr.rep_expr(expect_str, self.extra_pool)
        expect_dict = self.expr.convert_json(expect_str)
        for k, v in expect_dict.items():
            actual = self.expr.extractor(response, k)
            assert actual == v, f"断言失败: 实际值 {actual} != 预期值 {v}"

    def execute_select_fetchone(self, sql: str, extra_str: str):
        """
        执行查询语句（支持多条），返回每条SQL的第一行结果。
        如果只需要第一条SQL的结果，可取 [0]。
        """
        if not sql:
            return []

        results = []
        sql = self.expr.rep_expr(sql, self.extra_pool)
        for sql_statement in sql.split(";"):
            sql_statement = sql_statement.strip()
            if not sql_statement:
                continue
            db_handler = self._get_db_handler_from_extra(extra_str)
            result = db_handler.fetchone(sql_statement)
            if result:
                results.append(result[0] if isinstance(result, (list, tuple)) else result)

        return results

    def _get_db_handler_from_extra(self, extra_str: str):
        """从 extra 字段获取 db_key 并创建数据库连接，默认使用 mysql_db"""
        try:
            db_key = "mysql_db"  # 默认数据库 key

            if extra_str:
                extra_dict = json.loads(extra_str)
                db_key = extra_dict.get("db", db_key)

            db_conf = read_conf.get_dict(db_key)
            return SQLHandlerFactory.create(db_conf)

        except Exception as e:
            ERROR_LOGGER.error(f"获取数据库连接失败: {e}")
            return None

    def execute_sql_from_case(self, sql: str, extra_str: str):
        """执行 SQL 并更新参数池"""
        db_handler = self._get_db_handler_from_extra(extra_str)
        if not db_handler:
            ERROR_LOGGER.warning("未获取到数据库连接，跳过 SQL 执行")
            return
        try:
            index = 0
            for statement in sql.split(";"):
                stmt = statement.strip()
                if not stmt:
                    continue
                index += 1
                result = db_handler.execute_query(stmt)
                if result:
                    self.extra_pool[f"sql_query_results_{index}"] = result
            LOGGER.info(f"SQL 执行完成，更新参数池: {self.extra_pool}")
        finally:
            db_handler.close()

if __name__ == '__main__':
    data = {"convertOrderVO":{"amountCalcBaseOn": "exchangeAmount", "balanceId": 110733, "balancePwd": "111111", "coinId": 34, "coinSymbol": "USDT", "coinType": 2, "convertedAmount": "function:converter", "convertedBalanceId": 110730, "convertedCoinId": 31, "convertedCoinSymbol": "PHP", "convertedCoinType": 1, "convertMarketId": 68, "convertRate": "52.4", "convertRateExtend": "", "counterpartyId": 602, "counterpartyUserType": 4, "exchangeAmount": "10", "exchangeType": "convert_forex", "orderType": "convert", "orderTypeNext": "forex"}}
    processor = RequestDataProcessor({}, "")
    c = processor._process_functions(data, "", "")
    print(c)
