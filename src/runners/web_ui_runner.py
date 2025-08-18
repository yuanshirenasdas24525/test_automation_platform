import os
import sys
import pytest
from src.runners._common import resolve_report_dirs

def run(case=None, extra_args=None, alluredir=None):
    """
    运行 Web UI 测试（pytest + allure）
    """
    report_data_dir, report_dir = resolve_report_dirs(alluredir)

    pytest_args = []
    if case:
        pytest_args.append(case)
    else:
        pytest_args.append("./tests/web_ui")

    pytest_args.append(f"--alluredir={report_data_dir}")

    if extra_args:
        pytest_args.extend(extra_args)

    exit_code = pytest.main(pytest_args)

    os.system(f"allure generate {report_data_dir} -o {report_dir} --clean")
    os.system(f"allure serve {report_dir}")

    return exit_code