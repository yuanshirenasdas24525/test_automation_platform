# -*- coding:utf-8 -*-
"""
Allure 报告工具集

提供了一系列便捷函数用于增强 Allure 测试报告的可读性和信息量。
包括添加测试步骤、附件、标题、描述等功能。
"""
from __future__ import annotations

import json
from typing import Any, Optional

import allure
import pytest


class _AllureFailureMarker(AssertionError):
    """只用于把详情节点标成失败，调用方捕获后不改变执行控制流。"""


def set_allure_project(project: str) -> None:
    """设置 Allure 报告中的项目名称 (epic 级别)"""
    allure.dynamic.epic(project)


def set_allure_module(module: str) -> None:
    """设置 Allure 报告中的模块名称 (feature 级别)"""
    allure.dynamic.feature(module)


def set_allure_case(case: str) -> None:
    """设置 Allure 报告中的用例名称 (story 级别)"""
    allure.dynamic.story(case)


def set_allure_case_id(case_id: int) -> None:
    """写入稳定的用例 id，供报告落库兜底按 case 精准补缺。"""
    allure.dynamic.label("case_id", str(case_id))


def set_allure_suites(parent: Optional[str] = None, suite: Optional[str] = None,
                     sub: Optional[str] = None) -> None:
    """设置 Allure "Suites" Tab 的三层层级。

    `Behaviors` 和 `Suites` 是 Allure 两个互相独立的分组面板：
      - Behaviors 走 epic / feature / story（业务视角）
      - Suites    走 parent_suite / suite / sub_suite（执行视角）

    Web/App 用例希望两个面板里都能看到「项目 > 模块 > 用例」三层层级，所以
    平台的 v2 入口同时调用 `set_allure_project/module/case` + 这个函数。
    任一参数为 None / 空串就跳过对应级别的 dynamic 调用，避免覆盖成空字符串。
    """
    if parent:
        allure.dynamic.parent_suite(parent)
    if suite:
        allure.dynamic.suite(suite)
    if sub:
        allure.dynamic.sub_suite(sub)


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

def add_allure_step(
    step_name: str,
    content: Optional[Any] = None,
    attachment_name: str | None = None,
) -> None:
    """
    添加带附件的 Allure 测试步骤

    Args:
        step_name: 步骤名称
        content: 要附加的内容 (可选)，如果提供会以 JSON 格式附加
        attachment_name: 附件显示名，默认与步骤名一致
    """
    with allure.step(step_name):
        if content is not None:
            allure.attach(
                json.dumps(content, ensure_ascii=False, indent=4),
                attachment_name or step_name,
                allure.attachment_type.JSON
            )


def add_allure_failed_step(
    step_name: str,
    error_message: str,
    content: Optional[Any] = None,
    attachment_name: str | None = None,
) -> None:
    """添加红色失败步骤并附带结构化错误详情，但不直接终止用例。"""
    try:
        with allure.step(step_name):
            if content is not None:
                allure.attach(
                    json.dumps(content, ensure_ascii=False, indent=4),
                    attachment_name or step_name,
                    allure.attachment_type.JSON,
                )
            raise _AllureFailureMarker(error_message)
    except _AllureFailureMarker:
        # Allure 已在退出 step 上下文时记录失败；真实执行结果仍由 StepResult 决定。
        pass


def add_allure_attachment(name: str, content: Any, attachment_type: allure.attachment_type = None) -> None:
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
