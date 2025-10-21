# -*- coding:utf-8 -*-
"""
Allure 报告工具集

提供了一系列便捷函数用于增强 Allure 测试报告的可读性和信息量。
包括添加测试步骤、附件、标题、描述等功能。
"""

import allure
import json
import pytest
from typing import Any, Optional


def set_allure_project(project: str) -> None:
    """设置 Allure 报告中的项目名称 (epic 级别)"""
    allure.dynamic.epic(project)


def set_allure_module(module: str) -> None:
    """设置 Allure 报告中的模块名称 (feature 级别)"""
    allure.dynamic.feature(module)


def set_allure_case(case: str) -> None:
    """设置 Allure 报告中的用例名称 (story 级别)"""
    allure.dynamic.story(case)


def set_allure_title(title: str) -> None:
    """设置 Allure 报告中测试的标题"""
    allure.dynamic.title(title)


def set_allure_description(description: str) -> None:
    """设置 Allure 报告中测试的描述信息"""
    allure.dynamic.description(description)

def set_allure_testcase(testcase: str) -> None:
    """设置 Allure 展示测试用例具体信息"""
    allure.dynamic.testcase(testcase)

def set_allure_link(url: str) -> None:
    """设置 Allure 展示测试用例链接"""
    allure.dynamic.link(url)

def add_allure_step(step_name: str, content: Optional[Any] = None) -> None:
    """
    添加带附件的 Allure 测试步骤

    Args:
        step_name: 步骤名称
        content: 要附加的内容 (可选)，如果提供会以 JSON 格式附加
    """
    with allure.step(step_name):
        if content is not None:
            allure.attach(
                json.dumps(content, ensure_ascii=False, indent=4),
                step_name,
                allure.attachment_type.JSON
            )


def add_allure_attachment(name: str, content: Any, attachment_type: allure.attachment_type) -> None:
    """
    添加 Allure 附件

    Args:
        name: 附件名称
        content: 附件内容
        attachment_type: 附件类型
    """
    allure.attach(str(content), name, attachment_type)


def fail_test_with_allure(error: Exception) -> None:
    """
    在 Allure 报告中记录错误并标记测试失败

    Args:
        error: 错误/异常对象
    """
    add_allure_attachment("详细错误信息", str(error), allure.attachment_type.TEXT)
    pytest.fail(str(error))


def add_allure_image(image_path: str, name: Optional[str] = None) -> None:
    """
    添加图片到 Allure 报告

    Args:
        image_path: 图片文件路径
        name: 图片显示名称 (可选)
    """
    allure.attach.file(
        image_path,
        name or "测试截图",
        attachment_type=allure.attachment_type.PNG
    )