# -*- coding:utf-8 -*-
from src.utils.logger import LOGGER, ERROR_LOGGER

class AssertionEngine:
    def __init__(self, db_connection=None, device_action=None):
        self.db = db_connection      # MySQL / PostgreSQL / SQLite 连接
        self.device = device_action  # 用于截图


    def assert_value(self, actual, expected, assert_type="equal"):
        """
        总断言入口
        :param actual: 实际值
        :param expected: 期望值
        :param assert_type: equal / not_equal / gt / lt / contains / not_contains ...
        """
        try:
            method = getattr(self, f"_assert_{assert_type}")
        except AttributeError:
            raise Exception(f"未知断言类型：{assert_type}")

        if isinstance(expected, str) and expected.startswith("sql:"):
            expected = self.db.fetchone(expected[4:])

        try:
            method(actual, expected)
            LOGGER.info(f"断言成功：{assert_type} (实际={actual}, 预期={expected})")
        except AssertionError as e:
            if self.device:
                self.device.take_screenshot("assert_fail.png")
            ERROR_LOGGER.error(f"断言失败：{e}")
            raise e


    def _assert_equal(self, actual, expected):
        assert str(actual) == str(expected), f"预期 = {expected}，实际 = {actual}"

    def _assert_not_equal(self, actual, expected):
        assert str(actual) != str(expected), f"预期 != {expected}，但实际相等：{actual}"

    def _assert_gt(self, actual, expected):
        assert float(actual) > float(expected), f"预期 > {expected}，实际 = {actual}"

    def _assert_lt(self, actual, expected):
        assert float(actual) < float(expected), f"预期 < {expected}，实际 = {actual}"

    def _assert_contains(self, actual, expected):
        assert str(expected) in str(actual), f"预期包含 {expected}，实际 = {actual}"

    def _assert_not_contains(self, actual, expected):
        assert str(expected) not in str(actual), f"预期不包含 {expected}，实际 = {actual}"

    def _assert_empty(self, actual, expected=None):
        assert not actual, f"预期为空，但实际为：{actual}"

    def _assert_not_empty(self, actual, expected=None):
        assert actual, f"预期非空，但实际为空"

    def _assert_length_equal(self, actual, expected):
        assert len(actual) == int(expected), f"预期长度 = {expected}，实际长度 = {len(actual)}"

    def _assert_length_gt(self, actual, expected):
        assert len(actual) > int(expected), f"预期长度 > {expected}，实际长度 = {len(actual)}"

    def _assert_length_lt(self, actual, expected):
        assert len(actual) < int(expected), f"预期长度 < {expected}，实际长度 = {len(actual)}"