# config/pytest_config.py
import pytest
import json

def pytest_addoption(parser):
    parser.addoption("--cases_data", action="store", default="[]")

def pytest_generate_tests(metafunc):
    if "case" in metafunc.fixturenames:
        # 1. 尝试获取数据
        data_str = metafunc.config.getoption("--cases_data")
        try:
            cases = json.loads(data_str)
        except Exception:
            cases = []

        # 2. 关键点：即便 cases 为空，也要注入一个 [None] 或空列表
        # 否则 pytest 发现函数有参数但没数据，会直接报错“not found”
        if cases:
            metafunc.parametrize("case", cases)
        else:
            # 这是一个占位符，防止收集失败
            metafunc.parametrize("case", [None], ids=["no_cases_found"])