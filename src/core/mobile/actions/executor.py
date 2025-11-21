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
ActionRegistry.register("click", lambda executor, el, v: el.click())  # 点击元素
ActionRegistry.register("send_keys", lambda executor, el, v: el.send_keys(v))  # 输入内容
ActionRegistry.register("clear", lambda executor, el, v: el.clear())  # 清空输入框
ActionRegistry.register("text", lambda executor, el, v: el.text)  # 获取元素文本
ActionRegistry.register("back", lambda executor, el, v: executor.device.driver_back())  # 后退操作

# 扩展动作（自动从旧动作字典迁移）
ActionRegistry.register("ac_send", lambda ex, el, v: ex.device.ac_send(el, v))  # 逐字符输入
ActionRegistry.register("get_attribute", lambda ex, el, v: el.get_attribute(v))  # 获取元素属性
ActionRegistry.register("is_enabled", lambda ex, el, v: el.is_enabled())  # 判断元素是否可用
ActionRegistry.register("is_disabled", lambda ex, el, v: not el.is_enabled())  # 判断元素是否不可用
ActionRegistry.register("size", lambda ex, el, v: el.size)  # 获取元素尺寸
ActionRegistry.register("gesture_unlock", lambda ex, el, v: ex.device.gesture_unlock(el, *v))  # 手势解锁
ActionRegistry.register("h5_code", lambda ex, el, v: [ex.driver.find_element("xpath", i).click() for i in v])  # H5 页面批量点击
ActionRegistry.register("get_url", lambda ex, el, v: ex.driver.get(v))  # 打开指定 URL
ActionRegistry.register("finger_print", lambda ex, el, v: el.finger_print(v))  # Android 指纹解锁
ActionRegistry.register("use_touch_id", lambda ex, el, v: ex.device.use_touch_id(v))  # iOS TouchID
ActionRegistry.register("use_face_id", lambda ex, el, v: ex.device.use_face_id(v))  # iOS FaceID
ActionRegistry.register("get_element_position", lambda ex, el, v: el.location)  # 获取元素位置
ActionRegistry.register("touch_action", lambda ex, el, v: ex.device.touch_action(v))  # 多点触控 / 手势
ActionRegistry.register("get_clipboard", lambda ex, el, v: ex.device.get_clipboard())  # 获取剪贴板内容
ActionRegistry.register("open_notifications", lambda ex, el, v: ex.device.open_notifications())  # 打开通知栏
ActionRegistry.register("handle_alert", lambda ex, el, v: ex.device.handle_alert(v))  # 处理系统弹窗
ActionRegistry.register("handle_permissions_dialog", lambda ex, el, v: ex.device.handle_permissions_dialog(v))  # 处理权限弹窗
ActionRegistry.register("send_intent", lambda ex, el, v: ex.device.send_intent(*v))  # 发送 Android Intent
ActionRegistry.register("start_screen_recording", lambda ex, el, v: ex.device.start_screen_recording())  # 开始屏幕录制
ActionRegistry.register("stop_screen_recording", lambda ex, el, v: ex.device.stop_screen_recording(v))  # 停止屏幕录制
ActionRegistry.register("capture_logs", lambda ex, el, v: ex.device.capture_logs(v))  # 获取日志
ActionRegistry.register("get_performance_data", lambda ex, el, v: ex.device.get_performance_data(*v))  # 获取性能数据
ActionRegistry.register("simulate_network_condition", lambda ex, el, v: ex.device.simulate_network_condition(v))  # 模拟网络条件
ActionRegistry.register("install_app", lambda ex, el, v: ex.device.install_app(v))  # 安装应用
ActionRegistry.register("uninstall_app", lambda ex, el, v: ex.device.uninstall_app(v))  # 卸载应用
ActionRegistry.register("launch_app", lambda ex, el, v: ex.device.launch_app())  # 启动应用
ActionRegistry.register("close_app", lambda ex, el, v: ex.device.close_app())  # 关闭应用
ActionRegistry.register("is_app_installed", lambda ex, el, v: ex.device.is_app_installed(v))  # 检查应用是否安装
ActionRegistry.register("reset_app", lambda ex, el, v: ex.device.reset_app())  # 重置应用
ActionRegistry.register("set_orientation", lambda ex, el, v: ex.device.set_orientation(v))  # 设置屏幕方向
ActionRegistry.register("adjust_volume", lambda ex, el, v: ex.device.adjust_volume(*v))  # 调整音量
ActionRegistry.register("get_device_time", lambda ex, el, v: ex.device.get_device_time())  # 获取设备时间
ActionRegistry.register("send_key_event", lambda ex, el, v: ex.device.send_key_event(v))  # 发送按键事件
ActionRegistry.register("trigger_physical_button", lambda ex, el, v: ex.device.trigger_physical_button(v))  # 触发物理按钮