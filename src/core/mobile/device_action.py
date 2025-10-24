# -*- coding:utf-8 -*-
# Create on
from selenium.webdriver import ActionChains
from selenium.webdriver.common.actions import interaction
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.actions.pointer_input import PointerInput
from src.utils.logger import LOGGER, ERROR_LOGGER
from config.settings import ProjectPaths
from src.utils.allure_utils import add_allure_image
import os
import datetime
import base64



class DeviceAction:
    def __init__(self, driver):
        self._driver = driver

    """------------------------appium_api------------------------"""

    def touch_action(self, *args):
        # if len(args) % 2 != 0:
        #     raise ValueError("参数数量必须是偶数个")
        # args = [(int(args[i]), int(args[i+1])) for i in range(0, len(args), 2)]
        actions = ActionChains(self._driver)
        actions.w3c_actions = ActionBuilder(self._driver, mouse=PointerInput(interaction.POINTER_TOUCH, 'touch'))
        for i in range(len(args)):
            if i == 0:
                actions.w3c_actions.pointer_action.move_to_location(*args[i]).pointer_down()
            actions.w3c_actions.pointer_action.move_to_location(*args[i])
        actions.w3c_actions.pointer_action.release()
        actions.perform()

    def gesture_unlock(self, element, *args):
        if element:
            size = element.size
            start_x = element.location['x']
            start_y = element.location['y']
            end_x, end_y = size['width'], size['height']
        else:
            size = self._driver.get_window_size()
            start_x, start_y = 0, 0
            end_x, end_y = size['width'], size['height']
        width_1 = start_x['x'] + end_x['width'] / 8
        width_2 = start_x['x'] + end_x['width'] / 8 * 4
        width_3 = start_x['x'] + end_x['width'] / 8 * 7
        height_1 = start_y['y'] + end_y['height'] / 8
        height_2 = start_y['y'] + end_y['height'] / 8 * 4
        height_3 = start_y['y'] + end_y['height'] / 8 * 7
        mapping = {1: (width_1, height_1),
                   2: (width_2, height_1),
                   3: (width_3, height_1),
                   4: (width_1, height_2),
                   5: (width_2, height_2),
                   6: (width_3, height_2),
                   7: (width_1, height_3),
                   8: (width_2, height_3),
                   9: (width_3, height_3)}
        result = [mapping[i] for i in args]
        self.touch_action(result)

    def slider_validation(self, path):
        self._driver.update_settings({"fixImageFindScreenshotDims": False,
                                      "fixImageTemplateSize": True,
                                      "autoUpdateImageElementPosition": True,
                                      "getMatchedImageResult": False,
                                      "imageElementTapStrategy": "w3cActions"})
        base64_str = base64.b64encode(open(path, 'rb').read()).decode('utf-8')
        return base64_str

    # def swipe(self, start_x, start_y, end_x, end_y, duration=None):
    #     """执行屏幕滑动操作"""
    #     action = TouchAction(self._driver)
    #     action.press(x=start_x, y=start_y)
    #     action.wait(ms=duration) if duration else None
    #     action.move_to(x=end_x, y=end_y)
    #     action.release()
    #     action.perform()
    #     LOGGER.info(f"滑动操作从 ({start_x}, {start_y}) 到 ({end_x}, {end_y})")

    # def long_press(self, x, y, duration=1000):
    #     """执行长按操作"""
    #     action = TouchAction(self._driver)
    #     action.long_press(x=x, y=y, duration=duration)
    #     action.release()
    #     action.perform()
    #     LOGGER.info(f"在 ({x}, {y}) 位置长按 {duration} 毫秒")

    def ac_send(self, element, value):
        # 点击元素以确保其被聚焦
        element.click()

        # 使用 ActionChains 逐个字符发送输入
        actions = ActionChains(self._driver)
        for char in value:
            actions.send_keys(char)
        actions.perform()

    """------------------------系统交互------------------------"""

    def driver_back(self):
        self._driver.back()

    def get_clipboard(self):
        """获取剪贴板内容"""
        content = self._driver.get_clipboard_text()
        LOGGER.info(f"剪贴板内容: {content}")
        return content

    def open_notifications(self):
        """打开通知栏"""
        self._driver.open_notifications()
        LOGGER.info("通知栏已打开")

    def handle_alert(self, action='accept'):
        """处理系统弹窗"""
        alert = self._driver.switch_to.alert
        if action == 'accept':
            alert.accept()
        else:
            alert.dismiss()
        LOGGER.info(f"弹窗已{('接受' if action == 'accept' else '拒绝')}")

    def handle_permissions_dialog(self, accept=True):
        """处理权限弹窗"""
        if accept:
            self._driver.execute_script('mobile: acceptAlert')
        else:
            self._driver.execute_script('mobile: dismissAlert')
        LOGGER.info("权限弹窗处理完成")

    def use_touch_id(self, match=True):
        """使用 Touch ID"""
        self._driver.execute_script('mobile: touchId', {'match': match})
        LOGGER.info("Touch ID 模拟完成")

    def use_face_id(self, match=True):
        """使用 Face ID"""
        self._driver.execute_script('mobile: faceId', {'match': match})
        LOGGER.info("Face ID 模拟完成")

    def send_intent(self, action, data=None):
        """发送 Intent (仅限 Android)"""
        self._driver.execute_script('mobile: sendIntent', {'action': action, 'data': data})
        LOGGER.info(f"Intent '{action}' 已发送，数据: {data}")

    """------------------------高级功能------------------------"""

    def take_screenshot(self, img_name):
        """获取截屏"""
        now = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        path = str(os.path.join(str(ProjectPaths.IMG_DIR), now+img_name))
        self._driver.get_screenshot_as_file(path)
        LOGGER.info(f"截屏已保存到：{path}")
        add_allure_image(path)

    def start_screen_recording(self):
        """开始录屏"""
        self._driver.start_recording_screen()
        LOGGER.info("屏幕录制开始")

    def stop_screen_recording(self, pm4_name):
        """停止录屏并保存文件"""
        now = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        file_path = str(os.path.join(str(ProjectPaths.IMG_DIR), now+pm4_name))
        video_raw = self._driver.stop_recording_screen()
        with open(file_path, "wb") as vd:
            vd.write(video_raw)
        LOGGER.info(f"屏幕录制已保存到 {file_path}")

    def capture_logs(self, log_type='logcat'):
        """日志捕获"""
        logs = self._driver.get_log(log_type)
        LOGGER.info(f"捕获的日志: {logs}")
        return logs

    def get_performance_data(self, package_name, data_type, data_read_timeout=1000):
        """获取性能数据"""
        data = self._driver.get_performance_data(package_name, data_type, data_read_timeout)
        LOGGER.info(f"性能数据: {data}")
        return data

    def simulate_network_condition(self, condition='offline'):
        """模拟网络条件"""
        self._driver.set_network_connection(condition)
        LOGGER.info(f"网络条件设置为: {condition}")

    """------------------------应用管理------------------------"""

    def install_app(self, app_path):
        """安装应用"""
        self._driver.install_app(app_path)
        LOGGER.info(f"应用已安装: {app_path}")

    def uninstall_app(self, app_id):
        """卸载应用"""
        self._driver.remove_app(app_id)
        LOGGER.info(f"应用已卸载: {app_id}")

    def launch_app(self):
        """启动应用"""
        self._driver.launch_app()
        LOGGER.info("应用已启动")

    def close_app(self):
        """关闭应用"""
        self._driver.close_app()
        LOGGER.info("应用已关闭")

    def is_app_installed(self, app_id):
        """检查应用是否安装"""
        installed = self._driver.is_app_installed(app_id)
        LOGGER.info(f"应用 {app_id} 安装状态: {installed}")
        return installed

    def reset_app(self):
        """清除应用数据"""
        self._driver.reset()
        LOGGER.info("应用数据已清除")

    """------------------------设备交互------------------------"""

    def set_orientation(self, orientation):
        """设置屏幕方向"""
        self._driver.orientation = orientation
        LOGGER.info(f"屏幕方向设置为: {orientation}")

    def adjust_volume(self, volume_type, level):
        """调整音量"""
        self._driver.execute_script('mobile: volume', {'volume': volume_type, 'level': level})
        LOGGER.info(f"音量 {volume_type} 调整到 {level}")

    def get_device_time(self):
        """获取设备时间"""
        device_time = self._driver.device_time
        LOGGER.info(f"设备时间: {device_time}")
        return device_time

    def send_key_event(self, key_code):
        """发送键盘事件"""
        self._driver.press_keycode(key_code)
        LOGGER.info(f"键盘事件 {key_code} 已发送")

    def trigger_physical_button(self, button_name):
        """触发物理按钮"""
        self._driver.execute_script('mobile: pressButton', {'name': button_name})
        LOGGER.info(f"物理按钮 {button_name} 已触发")
