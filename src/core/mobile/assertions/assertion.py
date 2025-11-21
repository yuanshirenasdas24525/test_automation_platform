from src.utils.logger import LOGGER


class AssertionEngine:
    def __init__(self, db_connection, device_action):
        self.db = db_connection
        self.device = device_action

    def assert_result(self, actual, expected):
        if str(actual) != str(expected):
            self.device.take_screenshot("assert_fail.png")
            raise AssertionError(f"预期 {expected}，实际 {actual}")
        LOGGER.info("断言成功")