# coding: utf-8
import requests
from requests.exceptions import JSONDecodeError, ChunkedEncodingError
from src.utils.allure_utils import (

    set_allure_project, set_allure_module, set_allure_case, set_allure_title,
    set_allure_description, add_allure_step
)
from src.utils.logger import LOGGER
from src.core.api.factory import create_request_data_processor
import time


class Client:
    _sessions = {}
    _default_session = None

    def __init__(self):
        self.request_data_processor = create_request_data_processor()

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

    def send_case(self, case: list) -> object:
        (
            case_module, case_submodule, case_name, case_title, skip, method, path, header,
            parametric_type, data, file_path, extra, sql, expect, wait
        ) = case

        LOGGER.info(
            f"TestCase: {case_module} - {case_submodule} - {case_name} - {case_title}\n"
            f"Path: {path}\nData: {data}\nExtra: {extra}\nSQL: {sql}\nExpected: {expect}"
        )

        set_allure_project(case_module)
        set_allure_module(case_submodule)
        set_allure_case(case_name)
        set_allure_title(case_title)
        set_allure_description(description=f"测试点：{case_title}")

        url = self.request_data_processor.handler_path(path_str=path)
        header = self.request_data_processor.handler_header(header, data, sql)
        data = self.request_data_processor.handler_data(data, sql, extra)
        file = self.request_data_processor.handler_files(file_path)

        add_allure_step('Request Data', data)

        response = self._send_api_with_retry(
            url, method, parametric_type, header, data, file
        )

        if wait is not None:
            time.sleep(float(wait))

        self.request_data_processor.handler_extra(extra, response)
        self.request_data_processor.assert_result(response, expect)

        return response, sql

    def _send_api_with_retry(
        self, url: str, method: str, parametric_type: str,
        header=None, data=None, file=None, retries=3, delay=2
    ) -> dict:
        """Send an API request with retry logic.

        Args:
            url (str): The request URL.
            method (str): The HTTP method.
            parametric_type (str): The content type of the request.
            header (dict): The request headers.
            data (dict): The request data.
            file (dict): The files to upload.
            retries (int): Number of retry attempts.
            delay (int): Delay between retries.

        Returns:
            dict: The API response.
        """
        for attempt in range(retries):
            try:
                return self._send_api(url, method, parametric_type, header, data, file)
            except ChunkedEncodingError as e:
                LOGGER.warning(f'ChunkedEncodingError: {e}. Retrying {attempt + 1}/{retries}...')
                time.sleep(delay)
        raise ChunkedEncodingError('Exceeded maximum retries for ChunkedEncodingError')

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

        res = session.request(**request_kwargs)

        try:
            response = res.json()
            LOGGER.debug('JSON Response: %s', response)
        except JSONDecodeError:
            response = res.text
            LOGGER.debug('Text Response: %s', response)

        LOGGER.info(
            'Request Details:\n'
            f'URL: {res.url}\nMethod: {method}\nHeaders: {header}\n'
            f'Data: {data}\nFiles: {file}\nResponse: {response}'
        )

        add_allure_step(f'Response Time (s): {res.elapsed.total_seconds()}')
        add_allure_step('Response', response)

        return response


client = Client()
