# -*- coding:utf-8 -*-
from .input_controller import InputController
from .system_controller import SystemController
from .app_controller import AppController

class DeviceAction:
    def __init__(self, driver):
        self.driver = driver
        self.input = InputController(driver)
        self.system = SystemController(driver)
        self.app = AppController(driver)

    # InputController
    def back(self):
        return self.input.back()

    def tap(self, pos):
        return self.input.tap(pos)

    def swipe(self, *args, **kwargs):
        return self.input.swipe(*args, **kwargs)

    def ac_send(self, el, val):
        return self.input.ac_send(el, val)

    def touch_action(self, *args):
        return self.input.touch_action(*args)

    def gesture_unlock(self, el=None, *args):
        return self.input.gesture_unlock(el, *args)

    def send_key_event(self, keycode):
        return self.input.send_key_event(keycode)

    def trigger_physical_button(self, name):
        return self.input.trigger_physical_button(name)

    # SystemController
    def get_clipboard(self):
        return self.system.get_clipboard()

    def open_notifications(self):
        return self.system.open_notifications()

    def handle_alert(self, action='accept'):
        return self.system.handle_alert(action)

    def handle_permissions_dialog(self, accept=True):
        return self.system.handle_permissions_dialog(accept)

    def use_touch_id(self, match=True):
        return self.system.use_touch_id(match)

    def use_face_id(self, match=True):
        return self.system.use_face_id(match)

    def send_intent(self, action, data=None):
        return self.system.send_intent(action, data)

    def start_screen_recording(self):
        return self.system.start_screen_recording()

    def stop_screen_recording(self, filename):
        return self.system.stop_screen_recording(filename)

    def capture_logs(self, log_type='logcat'):
        return self.system.capture_logs(log_type)

    def get_performance_data(self, package, data_type, timeout=5):
        return self.system.get_performance_data(package, data_type, timeout)

    def simulate_network_condition(self, condition):
        return self.system.simulate_network_condition(condition)

    def set_orientation(self, orientation):
        return self.system.set_orientation(orientation)

    def adjust_volume(self, volume_type, level):
        return self.system.adjust_volume(volume_type, level)

    def get_device_time(self):
        return self.system.get_device_time()

    def slider_validation(self, path):
        return self.system.slider_validation(path)

    # AppController
    def install_app(self, path):
        return self.app.install_app(path)

    def uninstall_app(self, app_id):
        return self.app.uninstall_app(app_id)

    def launch_app(self):
        return self.app.launch_app()

    def close_app(self):
        return self.app.close_app()

    def is_app_installed(self, app_id):
        return self.app.is_app_installed(app_id)

    def reset_app(self):
        return self.app.reset_app()