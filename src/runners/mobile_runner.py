# -*- coding:utf-8 -*-
import os
import sys
import pytest
from src.runners._common import resolve_report_dirs
from src.utils.logger import LOGGER

def run(case=None, extra_args=None, ci_mode=True, alluredir=None):
    """
    运行 mobile UI 测试（pytest + allure）
    支持 Docker Compose 场景下通过 Appium 容器执行测试。
    """
    LOGGER.info("[INFO] 启动移动 UI 自动化...")
    # 解析报告目录
    report_data_dir, report_dir = resolve_report_dirs(alluredir)
    # 读取 Appium 服务信息（来自 docker-compose 环境变量）
    appium_host = os.getenv("APPIUM_HOST", "localhost")
    appium_port = os.getenv("APPIUM_PORT", "4723")
    device_name = os.getenv("DEVICE_NAME", "emulator-5554")
    LOGGER.info(f"Appium service -> http://{appium_host}:{appium_port}/wd/hub")
    LOGGER.info(f"[INFO] Target device -> {device_name}")
    # 写入 pytest 可读取的环境变量
    os.environ["APPIUM_SERVER_URL"] = f"http://{appium_host}:{appium_port}/wd/hub"
    os.environ["DEVICE_NAME"] = device_name
    # 组装 pytest 执行参数
    pytest_args = []
    if case:
        pytest_args.append(case)
    else:
        pytest_args.append("./tests/android_ui")
    pytest_args.append(f"--alluredir={report_data_dir}")
    if extra_args:
        pytest_args.extend(extra_args)
    LOGGER.info(f"[INFO] pytest args: {' '.join(pytest_args)}")
    # 启动 pytest 测试执行
    exit_code = pytest.main(pytest_args)
    # 生成 Allure 报告
    os.system(f"allure generate {report_data_dir} -o {report_dir} --clean")
    if not ci_mode and os.getenv("CI") != "true":
        os.system(f"allure serve {report_data_dir} -o {report_dir} -h 127.0.0.1 -p 52357")
    LOGGER.info("[INFO] 移动测试执行完成。")
    return exit_code