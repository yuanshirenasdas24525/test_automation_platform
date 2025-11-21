from selenium.common.exceptions import StaleElementReferenceException
from src.core.mobile.actions.registry import ActionRegistry
from src.utils.logger import LOGGER, ERROR_LOGGER


class ActionExecutor:
    def __init__(self, driver, device_action):
        self.driver = driver
        self.device = device_action

    def execute(self, step, element, value):
        action = step["action"]
        func = ActionRegistry.get(action)
        if not func:
            raise Exception(f"未注册动作: {action}")

        try:
            return func(self, element, value)
        except StaleElementReferenceException:
            LOGGER.warning("元素过期，尝试重新查找一次")
            from src.core.mobile.finder.finder import Finder
            finder = Finder(self.driver)
            new_el = finder.find(step["by"], step["finder"])
            return func(self, new_el, value)


# 注册基础动作
ActionRegistry.register("click", lambda executor, el, v: el.click())
ActionRegistry.register("send_keys", lambda executor, el, v: el.send_keys(v))
ActionRegistry.register("clear", lambda executor, el, v: el.clear())
ActionRegistry.register("text", lambda executor, el, v: el.text)
ActionRegistry.register("back", lambda executor, el, v: executor.device.driver_back())

# 扩展动作（自动从旧动作字典迁移）
ActionRegistry.register("ac_send", lambda ex, el, v: ex.device.ac_send(el, v))
ActionRegistry.register("get_attribute", lambda ex, el, v: el.get_attribute(v))
ActionRegistry.register("is_enabled", lambda ex, el, v: el.is_enabled())
ActionRegistry.register("is_disabled", lambda ex, el, v: not el.is_enabled())
ActionRegistry.register("size", lambda ex, el, v: el.size)
ActionRegistry.register("h5_code", lambda ex, el, v: [ex.driver.find_element("xpath", i).click() for i in v])
ActionRegistry.register("get_url", lambda ex, el, v: ex.driver.get(v))
ActionRegistry.register("finger_print", lambda ex, el, v: el.finger_print(v))
ActionRegistry.register("use_touch_id", lambda ex, el, v: ex.device.use_touch_id(v))
ActionRegistry.register("use_face_id", lambda ex, el, v: ex.device.use_face_id(v))
ActionRegistry.register("get_element_position", lambda ex, el, v: el.location)
ActionRegistry.register("touch_action", lambda ex, el, v: ex.device.touch_action(v))
ActionRegistry.register("get_clipboard", lambda ex, el, v: ex.device.get_clipboard())
ActionRegistry.register("open_notifications", lambda ex, el, v: ex.device.open_notifications())
ActionRegistry.register("handle_alert", lambda ex, el, v: ex.device.handle_alert(v))
ActionRegistry.register("handle_permissions_dialog", lambda ex, el, v: ex.device.handle_permissions_dialog(v))
ActionRegistry.register("send_intent", lambda ex, el, v: ex.device.send_intent(*v))
ActionRegistry.register("start_screen_recording", lambda ex, el, v: ex.device.start_screen_recording())
ActionRegistry.register("stop_screen_recording", lambda ex, el, v: ex.device.stop_screen_recording(v))
ActionRegistry.register("capture_logs", lambda ex, el, v: ex.device.capture_logs(v))
ActionRegistry.register("get_performance_data", lambda ex, el, v: ex.device.get_performance_data(*v))
ActionRegistry.register("simulate_network_condition", lambda ex, el, v: ex.device.simulate_network_condition(v))
ActionRegistry.register("install_app", lambda ex, el, v: ex.device.install_app(v))
ActionRegistry.register("uninstall_app", lambda ex, el, v: ex.device.uninstall_app(v))
ActionRegistry.register("launch_app", lambda ex, el, v: ex.device.launch_app())
ActionRegistry.register("close_app", lambda ex, el, v: ex.device.close_app())
ActionRegistry.register("is_app_installed", lambda ex, el, v: ex.device.is_app_installed(v))
ActionRegistry.register("reset_app", lambda ex, el, v: ex.device.reset_app())
ActionRegistry.register("set_orientation", lambda ex, el, v: ex.device.set_orientation(v))
ActionRegistry.register("adjust_volume", lambda ex, el, v: ex.device.adjust_volume(*v))
ActionRegistry.register("get_device_time", lambda ex, el, v: ex.device.get_device_time())
ActionRegistry.register("send_key_event", lambda ex, el, v: ex.device.send_key_event(v))
ActionRegistry.register("trigger_physical_button", lambda ex, el, v: ex.device.trigger_physical_button(v))