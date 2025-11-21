# -*- coding:utf-8 -*-
import base64
from src.utils.logger import LOGGER, ERROR_LOGGER

class SystemController:
    def __init__(self, driver):
        self.driver = driver

    def get_clipboard(self):
        try:
            content = self.driver.get_clipboard_text()
            LOGGER.info(f"剪贴板内容: {content}")
            return content
        except:
            return None

    def open_notifications(self):
        try:
            self.driver.open_notifications()
            LOGGER.info("通知栏已打开")
        except:
            pass

    def handle_alert(self, action='accept'):
        try:
            alert = self.driver.switch_to.alert
            if action == 'accept':
                alert.accept()
            else:
                alert.dismiss()
            LOGGER.info(f"弹窗已{('接受' if action=='accept' else '拒绝')}")
        except:
            pass

    def handle_permissions_dialog(self, accept=True):
        try:
            self.driver.execute_script('mobile: acceptAlert' if accept else 'mobile: dismissAlert')
        except:
            pass

    def use_touch_id(self, match=True):
        try:
            self.driver.execute_script('mobile: touchId', {'match': match})
        except:
            pass

    def use_face_id(self, match=True):
        try:
            self.driver.execute_script('mobile: faceId', {'match': match})
        except:
            pass

    def send_intent(self, action, data=None):
        try:
            payload = {'action': action}
            if data:
                payload['data'] = data
            self.driver.execute_script('mobile: sendIntent', payload)
        except:
            pass

    def start_screen_recording(self):
        try:
            self.driver.start_recording_screen()
        except:
            pass

    def stop_screen_recording(self, filename):
        try:
            video_raw = self.driver.stop_recording_screen()
            with open(filename, 'wb') as f:
                f.write(base64.b64decode(video_raw))
        except:
            pass

    def capture_logs(self, log_type='logcat'):
        try:
            return self.driver.get_log(log_type)
        except:
            return None

    def get_performance_data(self, package_name, data_type, timeout=5):
        try:
            return self.driver.get_performance_data(package_name, data_type, timeout)
        except:
            return None

    def simulate_network_condition(self, condition):
        try:
            self.driver.set_network_connection(condition)
        except:
            pass

    def set_orientation(self, orientation):
        try:
            self.driver.orientation = orientation
        except:
            pass

    def adjust_volume(self, volume_type, level):
        try:
            self.driver.execute_script('mobile: volume', {'volume': volume_type, 'level': level})
        except:
            pass

    def get_device_time(self):
        try:
            return self.driver.device_time
        except:
            return None

    def slider_validation(self, image_path):
        try:
            self.driver.update_settings({
                "fixImageFindScreenshotDims": False,
                "fixImageTemplateSize": True,
                "autoUpdateImageElementPosition": True,
                "getMatchedImageResult": False,
                "imageElementTapStrategy": "w3cActions"
            })
            with open(image_path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except:
            return None