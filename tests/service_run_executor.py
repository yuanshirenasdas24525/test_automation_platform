# tests/service_run_executor.py
import pytest

class TestServiceApi:
    def test_api_runner(self, case):
        if case is None:
            pytest.skip("没有接收到待执行的用例数据")

        from src.core.api.factory import create_api_client
        create_api_client().send_case(case=case)