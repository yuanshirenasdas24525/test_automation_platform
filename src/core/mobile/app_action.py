# -*- coding:utf-8 -*-
# Create on
import time
from string import Template
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import StaleElementReferenceException
from tenacity import retry, stop_after_attempt, wait_fixed
from .device_action import DeviceAction

from src.utils.function_executor import exec_func
from src.utils.platform_utils import execution_time_decorator
from src.utils.allure_utils import add_allure_attachment
from src.utils.logger import LOGGER, ERROR_LOGGER




class AppAction:
    _blacklist = [('id', 'com.wallet.uu:id/close')]  # 黑名单列表，用于处理在case运行过程中可能出现的未知弹窗
    _whitelist = [('id', 'com.wallet.uu:id/skip'),
                  ('id', 'com.android.permissioncontroller:id/permission_allow_button')]
    _error_count = 0  # 定位元素的错误次数
    _consecutive_errors = 0  # 类变量用于跟踪连续错误
    _error_max = 10  # 允许进行元素定位的最大错误次数
    _locator_count = 0  # 记录事件点击次数
    _cache_pool = {"account": "6Xn6wqy6HT", "phone": "9256634156"}  # 缓存池

    def __init__(self, driver, db_connection=None):
        self._driver = driver
        self.db_connection = db_connection
        self.device_action = DeviceAction(driver)

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
    def find(self, by, locator):
        """
        查找元素方法。
        :param by: 定位元素的方法，可以是元组形式的 (by, value)，也可以是单独的定位方法字符串。
        :param locator: 定位元素对应的所需值，如果 by 为元组形式，则忽略该参数。
        返回:
            WebElement: 找到的元素对象。
        异常:
            Exception: 查找元素失败，如果重试次数超过设定的最大次数或出现未知弹窗时抛出异常。
        """
        try:
            locator_tuple = (by, locator) if isinstance(by, str) else by
            element = WebDriverWait(self._driver, 5, poll_frequency=0.1).until(
                EC.presence_of_element_located(_AppiumBy.by(*locator_tuple))
            )
            self._error_count = 0
            self._locator_count += 1
            LOGGER.info(f"第{self._locator_count}次定位元素定位元素: {locator_tuple}")
            return element
        except Exception as e:
            self.handle_blacklist()  # 解决非预期弹框问题
            if (by, locator) in self._whitelist:  # 解决单个执行和批量执行时一些元素查询差异的问题
                return "whitelist"
            self._error_count += 1
            ERROR_LOGGER.error(f"第：{self._error_count}次定位元素{by}_{locator}报错: {e}")
            if self._error_count % 3 == 0:  # 用例执行错误截图
                self.device_action.take_screenshot('find_error.png')
                add_allure_attachment("元素定位",e)
            if self._error_count >= self._error_max:
                self.close()
                raise Exception("达到最大错误次数") from e
            raise

    @execution_time_decorator
    def swipe_find_element(self, step: dict):
        """
        在指定元素内或整个页面上滑动寻找元素。
        :param step: 包含定位信息的字典。
        """
        size = self._driver.get_window_size()
        split_values = step['sliding_location'].split('_')
        if len(split_values) >= 2:  # 指定元素矩形内滑动
            element = self.find(split_values[1], split_values[2])
            size = element.size
            start_x = element.location['x']
            start_y = element.location['y']
            end_x, end_y = size['width'], size['height']
        else:
            start_x, start_y = 0, 0
            end_x, end_y = size['width'], size['height']
        if split_values[0] == 'horizontal':  # 水平滑动
            mid_y = (start_y + end_y) // 2
            for _ in range(3):  # 尝试滑动3次
                try:
                    element = WebDriverWait(self._driver, 10).until(
                        EC.presence_of_element_located(_AppiumBy.by(step['by'], step['locator']))
                    )
                    LOGGER.info(f"horizontal:{step['by']},{step['locator']}, {step['case_line']}")
                    return element  # 如果找到元素，直接返回
                except Exception as e:
                    self._driver.swipe(start_x / 10 * 8, mid_y, end_x / 10 * 2, mid_y)  # 从右至左滑动
                    LOGGER.info(f"没有找到{step['by']}{step['locator']}元素：{e}")
                    LOGGER.info(f"执行滑动动作 从X:{start_x / 10 * 8}Y：{mid_y}滑动到X：{end_x / 10 * 2}Y：{mid_y}")
        elif split_values[0] == 'vertical':  # 垂直滑动
            mid_x = (start_x + end_x) // 2
            for _ in range(3):  # 尝试滑动3次
                try:
                    element = WebDriverWait(self._driver, 10).until(
                        EC.presence_of_element_located(_AppiumBy.by(step['by'], step['locator']))
                    )
                    LOGGER.info(f"vertical:{step['by']},{step['locator']}, {step['case_line']}")
                    return element  # 如果找到元素，直接返回
                except Exception as e:
                    self._driver.swipe(mid_x, end_y / 10 * 8, mid_x, end_y / 10 * 2)  # 从下至上滑动
                    LOGGER.info(f"没有找到{step['by']}{step['locator']}元素报错：{e}")
                    LOGGER.info(f"执行滑动动作 从X:{mid_x}Y：{end_y / 10 * 8}滑动到X：{mid_x}Y：{end_y / 10 * 3}")
        raise Exception("未能在指定次数内找到元素")

    @execution_time_decorator
    def handle_blacklist(self):
        size = self._driver.get_window_size()
        for _ in range(10):
            elements_clicked = self._click_blacklisted_elements(size)
            if not elements_clicked:
                break
        else:
            self.device_action.take_screenshot('blacklist_error.png')
            add_allure_attachment("黑名单","达到最大尝试次数，程序已关闭")
            self.close()
            raise Exception("达到最大尝试次数，程序已关闭")

    def _click_blacklisted_elements(self, size):
        elements_clicked = False
        for black in self._blacklist:
            elements = self._find_blacklist_elements(black)
            if elements:
                self._driver.tap([(size['width'] / 10 * 9, size['height'] / 10 * 2)])
                elements_clicked = True
        return elements_clicked

    def _find_blacklist_elements(self, black):
        elements = self._driver.find_elements(*black)
        return elements

    def app_steps(self, step: dict):
        required_keys = ['by', 'locator', 'action', 'expected']

        # 等待时间处理
        wait_time = step.get('wait')
        if wait_time:
            time.sleep(int(wait_time))

        # 检查是否缺少必要的键
        for key in required_keys:
            if key not in step:
                raise KeyError(f"缺少必要的步骤键: {key}")

        try:
            # 根据步骤中的定位方式选择合适的定位方法
            element = (
                self.swipe_find_element(step) if step.get('sliding_location')
                else self.device_action.slider_validation(step['locator']) if step['by'] == 'image'
                else self.find(step['by'], step['locator'])
            )

            # 执行动作
            result = self.perform_action(step, element)

            # 如果需要，将复制的内容存储到缓存池
            if step.get('deposit', '') and step.get('deposit', '').upper() == 'Y' and 'case_line' in step:
                self._cache_pool["id"+"_"+str(step['case_line'])] = result
                LOGGER.info(f'成功添加 {result} 到缓存池：{self._cache_pool}')

            # 执行断言
            if step.get('expected') and not step.get('value').startswith("function:"):
                expected = self.replace_str(step.get('expected'))
                if expected.startswith('function:'):
                    function = expected.split("-")
                    expected = exec_func(function[0], *function[1:])
                self.assert_step_result(result, expected)

            # 重置连续错误计数
            AppAction._consecutive_errors = 0

        except Exception as e:
            self.handle_exception(step, e)

    def handle_exception(self, step, exception):
        ERROR_LOGGER.error(f"执行{step}时出错: {exception}")
        add_allure_attachment("执行操作", f"错误，关闭应用{exception}")
        AppAction._consecutive_errors += 1
        if AppAction._consecutive_errors >= 3:
            ERROR_LOGGER.error("连续3个步骤失败，准备结束测试执行")
            self.close()
            raise Exception("连续3个步骤失败，测试执行终止") from exception

    @execution_time_decorator
    def assert_step_result(self, result, expected_result):
        """
        断言结果与预期相符，并记录相关日志。
        :param result: 实际结果
        :param expected_result: 预期结果
        """
        try:
            # 处理预期结果为 SQL 查询的情况
            expected_result = self.get_db_value(expected_result)

            # 确保比较前数据类型一致，假设转换为字符串比较
            assert str(result) == str(expected_result), f"预期结果 {expected_result} 与实际结果 {result} 不符"
            LOGGER.info(f"步骤成功完成，预期结果: {expected_result}")
        except AssertionError as e:
            self.device_action.take_screenshot('assert_fail.png')
            LOGGER.error(f"断言失败，预期结果 {expected_result} 与实际结果 {result} 不符")
            raise AssertionError(f"预期结果 {expected_result} 与实际结果 {result} 不符") from e
        except Exception as e:
            self.device_action.take_screenshot('assert_error.png')
            ERROR_LOGGER.error(f"执行过程中出现异常: {e}")
            raise Exception(f"执行过程中出现异常: {e}") from e

    @execution_time_decorator
    def get_db_value(self, expected_result):
        if isinstance(expected_result, str) and expected_result.startswith("sql:"):
            content = expected_result.split("sql:", 1)[1].strip()
            query = self.replace_str(content)
            expected_result = self.execute_sql_query(query)
            return expected_result[0] if expected_result else None
        return expected_result

    def execute_sql_query(self, query):
        """
        执行SQL查询并返回结果。
        :param query: SQL查询字符串。
        :return: 查询结果,返回单条数据。
        """
        # 手动提交事务，确保获取最新数据
        self.db_connection.commit()
        with self.db_connection.cursor() as cursor:
            # 清理之前的结果集
            cursor.close()
            # 执行实际查询
            cursor = self.db_connection.cursor()
            cursor.execute(query)
            result = cursor.fetchone()
            LOGGER.info(f"SQL查询结果: {result}")
        return result

    def perform_action(self, step: dict, element):
        actions = {
            'click': lambda el, _: el.click(),  # 点击
            'clear': lambda el, _: el.clear(),  # 清空输入框内容
            'send_keys': lambda el, val: el.send_keys(val),  # 输入内容
            'ac_send': lambda el, val: self.device_action.ac_send(el, val),
            'get_attribute': lambda el, val: el.get_attribute(val),  # 获取内容（文本，图片等）
            'is_enabled': lambda el, _: el.is_enabled(),  # 判断按钮是否可点击
            'is_disabled': lambda el, _: el.is_displayed(),  # 判断按状态
            'text': lambda el, _: el.text,  # 获取文本
            'size': lambda el, _: el.size,  # 获取屏幕大小
            'h5_code': lambda el, val: [self.find("xpath", i).click() for i in val],
            'back': lambda el, _: self.device_action.driver_back(),  # 后退
            'get_url': lambda el, url: self._driver.get(url),  # 访问网页地址
            'finger_print': lambda el, num: el.finger_print(num),  # 安卓指纹解锁
            'use_touch_id': lambda _, val: self.device_action.use_touch_id(val),  # ios的指纹识别
            'use_face_id': lambda _, val: self.device_action.use_face_id(val),  # 脸部识别
            'get_element_position': lambda el, _: el.location,  # 获取元素位置
            'touch_action': lambda el, val: self.device_action.touch_action(val),  # 屏幕滑动
            'get_clipboard': lambda _, __: self.device_action.get_clipboard(),  # 获取粘贴内容
            'open_notifications': lambda _, __: self.device_action.open_notifications(),  # 打开通知
            'handle_alert': lambda _, val: self.device_action.handle_alert(val),
            'handle_permissions_dialog': lambda _, val: self.device_action.handle_permissions_dialog(val),
            'send_intent': lambda _, val: self.device_action.send_intent(*val),
            'take_screenshot': lambda _, val: self.device_action.take_screenshot(val),  # 获取截屏
            'start_screen_recording': lambda _, __: self.device_action.start_screen_recording(),  # 录屏(功能有问题)
            'stop_screen_recording': lambda _, val: self.device_action.stop_screen_recording(val),  # 停止录屏
            'capture_logs': lambda _, val: self.device_action.capture_logs(val),  # 获取日志
            'get_performance_data': lambda _, val: self.device_action.get_performance_data(*val),  # 获取性能日志
            'simulate_network_condition': lambda _, val: self.device_action.simulate_network_condition(val),
            'install_app': lambda _, val: self.device_action.install_app(val),  # 安装app
            'uninstall_app': lambda _, val: self.device_action.uninstall_app(val),  # 卸载app
            'launch_app': lambda _, __: self.device_action.launch_app(),  # 启动应用
            'close_app': lambda _, __: self.device_action.close_app(),  # 关闭应用
            'is_app_installed': lambda _, val: self.device_action.is_app_installed(val),  # 检查应用程序是否安装
            'reset_app': lambda _, __: self.device_action.reset_app(),  # 重启app
            'set_orientation': lambda _, val: self.device_action.set_orientation(val),  # 设置手机方向
            'adjust_volume': lambda _, val: self.device_action.adjust_volume(*val),  # 调整音量
            'get_device_time': lambda _, __: self.device_action.get_device_time(),  # 获取系统时间
            'send_key_event': lambda _, val: self.device_action.send_key_event(val),
            'trigger_physical_button': lambda _, val: self.device_action.trigger_physical_button(val),  # 出发物理按键
        }

        if element == "whitelist":  # 如果是在白名单中元素就随便点一下
            result = self._driver.tap([(0, 0)])
            return result

        action = step.get('action')
        text_value = step.get('value')
        retrieve_key = step.get('retrieve')
        expected = step.get('expected')
        function_parameters = self._cache_pool.get(str(retrieve_key), '')

        if text_value in ["function:extract_code", "function:db_value",
                          "function:generate_phone",
                          "function:google_authentication",
                          "function:h5_code"]:
            if expected:
                function_parameters = self.get_db_value(expected)
        value = exec_func(text_value, function_parameters)
        self.cache_value(step, value)  # 把随机生成的账号或者邮箱放进缓存池中

        action_func = actions.get(action)
        if action_func:
            try:
                result = action_func(element, value)
                LOGGER.info(f"执行动作: {action}，值: {value}")
                return result
            except StaleElementReferenceException:
                LOGGER.error("元素状态已变，尝试重新获取元素")
                element = self.find(step['by'], step['locator'])
                return action_func(element, value)
        else:
            self.device_action.take_screenshot('action_error.png')
            raise ValueError(f"执行动作出错: {action}")

    @execution_time_decorator
    def cache_value(self, c_step, c_value):
        value_keys = {
            "function:generate_account": "account",
            "function:generate_email": "email",
            "function:generate_phone": "phone",
        }
        key = value_keys.get(c_step.get('value'))
        if key:
            self._cache_pool[key] = c_value
            LOGGER.info(f"添加 {key} 到缓存里: {self._cache_pool}")

    @execution_time_decorator
    def replace_str(self, content: str) -> str:
        """替换内容中的表达式为实际值
        :param content: 待替换的字符串
        :return: 替换后的字符串
        """
        try:
            template = Template(content)
            content = template.safe_substitute(self._cache_pool)
        except KeyError as e:
            LOGGER.error(f'替换时出现KeyError: {e}')
        except Exception as e:
            LOGGER.error(f'替换时出现错误: {e}')
        return content

    @execution_time_decorator
    def close(self):
        # 关闭应用
        self._driver.quit()
        raise Exception("连续3个步骤失败，测试执行终止")


class _AppiumBy:
    LOCATORS = {
        "id": AppiumBy.ID,
        "xpath": AppiumBy.XPATH,
        "image": AppiumBy.IMAGE,
        "accessibility_id": AppiumBy.ACCESSIBILITY_ID,
        "android_uiautomator": AppiumBy.ANDROID_UIAUTOMATOR,
        "android_viewtag": AppiumBy.ANDROID_VIEWTAG,
        "android_data_matcher": AppiumBy.ANDROID_DATA_MATCHER,
        "android_view_matcher": AppiumBy.ANDROID_VIEW_MATCHER,
        "ios_predicate": AppiumBy.IOS_PREDICATE,
        "ios_class_chain": AppiumBy.IOS_CLASS_CHAIN,
        "class_name": AppiumBy.CLASS_NAME,
        "link_text": AppiumBy.LINK_TEXT,
        "css_selector": AppiumBy.CSS_SELECTOR,
        "name": AppiumBy.NAME,
    }

    @classmethod
    def by(cls, by, value):
        by = by.lower()
        if by not in cls.LOCATORS:
            raise ValueError(f"无效的定位策略 '{by}'")
        return cls.LOCATORS[by], value
