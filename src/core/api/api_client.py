# coding: utf-8
import requests
import time
from requests.exceptions import JSONDecodeError, ChunkedEncodingError
from src.utils.allure_utils import (
    set_allure_project, set_allure_module, set_allure_case,
    set_allure_description, add_allure_step, set_allure_link
)
from src.utils.logger import LOGGER


def clean_val(val):
    if val is None:
        return None
    # strip() 会去掉前后的空格、换行符
    s_val = str(val).strip()
    return s_val if s_val != "" else None


class ApiClient:
    _sessions = {}
    _default_session = None

    def __init__(self, request_data_processor, record_property):
        self.session = requests.Session()
        self.processor = request_data_processor
        self.record_property = record_property
        # 保存上一次的层级
        self.last_module = None
        self.last_submodule = None
        self.last_case_name = None

        # 各层计数器
        self.module_counter = 0
        self.submodule_counter = 0
        self.case_name_counter = 0
        self.case_title_counter = 0

    def _add_case_numbering(self, case_module, case_submodule, case_name, case_title):
        # 模块计数
        if case_module != self.last_module:
            self.module_counter += 1
            self.submodule_counter = 0
            self.case_name_counter = 0
            self.case_title_counter = 0
            self.last_module = case_module
            self.last_submodule = None
            self.last_case_name = None

        # 子模块计数
        if case_submodule != self.last_submodule:
            self.submodule_counter += 1
            self.case_name_counter = 0
            self.case_title_counter = 0
            self.last_submodule = case_submodule
            self.last_case_name = None

        # 用例名称计数
        if case_name != self.last_case_name:
            self.case_name_counter += 1
            self.case_title_counter = 0
            self.last_case_name = case_name

        # 用例标题计数
        self.case_title_counter += 1

        # 生成带编号的字符串
        numbered_module = f"{self.module_counter:04d}_{case_module}"
        numbered_submodule = f"{self.submodule_counter:04d}_{case_submodule}"
        numbered_case_name = f"{self.case_name_counter:04d}_{case_name}"
        numbered_case_title = f"{self.case_title_counter:04d}_{case_title}"

        return numbered_module, numbered_submodule, numbered_case_name, numbered_case_title

    def get_session(self, token: str = None) -> requests.Session:
        if token:
            if token not in self._sessions:
                session = requests.Session()
                if "Authorization" in token:
                    session.headers.update({"Authorization": f"Bearer {token}"})
                else:
                    session.headers.update({"token": token})
                self._sessions[token] = session
            return self._sessions[token]
        else:
            if self._default_session is None:
                self._default_session = requests.Session()
            return self._default_session

    def send_case(self, case: dict) -> object:
        case_project = case.get("project_name", None)  # 对应你之前 JOIN 查出来的项目名
        case_module = case.get("module_name", None)  # 对应模块名
        case_name = clean_val(case.get("name"))  # 用例名称
        case_description= case.get("description", None)  # 对应描述

        skip = clean_val(case.get("skip"))
        method = clean_val(case.get("method"))
        path = clean_val(case.get("path"))
        header = clean_val(case.get("headers"))
        parametric_type = clean_val(case.get("data_type"))
        data = clean_val(case.get("params"))
        file_path = clean_val(case.get("file_path"))
        extra = clean_val(case.get("extract_data"))
        sql = clean_val(case.get("sql_query"))
        expect = clean_val(case.get("assertion"))
        wait = clean_val(case.get("wait_time"))

        numbered_project, numbered_module, numbered_case_name, numbered_case_description = self._add_case_numbering(
            case_project, case_module, case_name, case_description)

        LOGGER.info(f"开始运行测试用例: {numbered_project} - {numbered_module} - {numbered_case_name} \n")

        if wait is not None:
            time.sleep(float(wait))

        set_allure_project(numbered_project)
        set_allure_module(numbered_module)
        set_allure_case(numbered_case_name)

        set_allure_description(description=f"{numbered_case_description}")

        url = self.processor.handler_path(path_str=path)
        header = self.processor.handler_header(header, data, sql)
        data = self.processor.handler_data(data, sql, extra)
        file = self.processor.handler_files(file_path)
        set_allure_link(url)
        add_allure_step(f'Request Time (s): {time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())}')
        add_allure_step('Header', header)
        add_allure_step('Request', data)

        response = self._send_api_with_retry(
            url, method, parametric_type, header, data, file
        )

        self.processor.handler_extra(extra, response)
        self.processor.assert_result(response, expect)

        self.record_property("action", method)
        self.record_property("target", url)
        self.record_property("input_data", {"Header":header,"Request":data})
        self.record_property("output_data", response)
        self.record_property("assertion_results", response.status_code)

        return response, sql

    def _send_api_with_retry(
        self, url: str, method: str, parametric_type: str,
        header=None, data=None, file=None, retries=3, delay=2
    ) -> dict:
        for attempt in range(retries):
            try:
                return self._send_api(url, method, parametric_type, header, data, file)
            except ChunkedEncodingError as e:
                LOGGER.warning(f'ChunkedEncodingError: {e}. Retrying {attempt + 1}/{retries}...')
                time.sleep(delay)
        raise ChunkedEncodingError('超过 ChunkedEncodingError 的最大重试次数')

    def _send_api(
        self, url: str, method: str, parametric_type: str,
        header=None, data=None, file=None
    ) -> dict:
        # 检查 token 或 authorization 是否存在，并获取有效的 session
        token = header.get('token') or header.get('Authorization')
        session = self.get_session(token)
        request_kwargs = {
            "method": method,
            "url": url,
            "headers": header or session.headers,
        }

        if parametric_type == 'application/x-www-form-urlencoded':
            request_kwargs["params"] = data
        elif parametric_type == 'multipart/form-data':
            request_kwargs["data"] = data
            request_kwargs["files"] = file
        elif parametric_type == 'application/json':
            request_kwargs["json"] = data
            request_kwargs["files"] = file
        else:
            raise ValueError(
                'Unsupported parametric_type. Choose from: '
                'application/x-www-form-urlencoded, application/json, multipart/form-data'
            )
        try:
            res = session.request(**request_kwargs,timeout=5)
        except Exception as e:
            # 打印具体的异常类型，比如是 Timeout 还是 ConnectionRefusedError
            LOGGER.error(f'请求失败类型: {type(e).__name__}')
            LOGGER.error(f'错误详细信息: {str(e)}')
            raise

        try:
            response = res.json()
            LOGGER.info(f'请求发送成功, 响应体: {response}')
        except JSONDecodeError:
            response = res.text
            LOGGER.error(f'响应格式错误, 响应体: {response}')

        add_allure_step(f'Response Time (s): {res.elapsed.total_seconds()}')
        add_allure_step('Response', response)

        return response
