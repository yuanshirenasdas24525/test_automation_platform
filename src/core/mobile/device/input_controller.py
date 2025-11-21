# -*- coding:utf-8 -*-
from selenium.webdriver import ActionChains
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.actions.pointer_input import PointerInput
from selenium.webdriver.common.actions import interaction
from src.utils.logger import LOGGER, ERROR_LOGGER

class InputController:
    def __init__(self, driver):
        self.driver = driver

    def back(self):
        try:
            return self.driver.back()
        except:
            try:
                self.driver.press_keycode(4)
            except:
                pass

    def tap(self, positions):
        try:
            return self.driver.tap(positions)
        except:
            try:
                for x, y in positions:
                    self.driver.execute_script("mobile: tap", {"x": x, "y": y})
            except:
                pass

    def swipe(self, start_x, start_y, end_x, end_y, duration=500):
        try:
            return self.driver.swipe(start_x, start_y, end_x, end_y, duration)
        except:
            try:
                action = self.driver.create_touch_action()
                action.press(x=start_x, y=start_y).wait(ms=duration).move_to(x=end_x, y=end_y).release().perform()
            except:
                pass

    def ac_send(self, element, value):
        try:
            element.click()
            actions = ActionChains(self.driver)
            for char in value:
                actions.send_keys(char)
            actions.perform()
        except Exception as e:
            ERROR_LOGGER.error(f"ac_send 执行失败: {e}")

    def touch_action(self, *args):
        try:
            actions = ActionChains(self.driver)
            actions.w3c_actions = ActionBuilder(self.driver, mouse=PointerInput(interaction.POINTER_TOUCH, 'touch'))
            for i, point in enumerate(args):
                if i == 0:
                    actions.w3c_actions.pointer_action.move_to_location(*point).pointer_down()
                else:
                    actions.w3c_actions.pointer_action.move_to_location(*point)
            actions.w3c_actions.pointer_action.release()
            actions.perform()
        except Exception as e:
            ERROR_LOGGER.error(f"touch_action 执行失败: {e}")

    def gesture_unlock(self, element=None, *args):
        try:
            if element:
                size = element.size
                start_x = element.location['x']
                start_y = element.location['y']
                end_x, end_y = size['width'], size['height']
            else:
                size = self.driver.get_window_size()
                start_x, start_y = 0, 0
                end_x, end_y = size['width'], size['height']

            width_1 = start_x + end_x / 8
            width_2 = start_x + end_x / 8 * 4
            width_3 = start_x + end_x / 8 * 7
            height_1 = start_y + end_y / 8
            height_2 = start_y + end_y / 8 * 4
            height_3 = start_y + end_y / 8 * 7

            mapping = {1: (width_1, height_1),
                       2: (width_2, height_1),
                       3: (width_3, height_1),
                       4: (width_1, height_2),
                       5: (width_2, height_2),
                       6: (width_3, height_2),
                       7: (width_1, height_3),
                       8: (width_2, height_3),
                       9: (width_3, height_3)}

            points = [mapping[i] for i in args]
            self.touch_action(*points)
        except Exception as e:
            ERROR_LOGGER.error(f"gesture_unlock 执行失败: {e}")

    def send_key_event(self, keycode):
        try:
            self.driver.press_keycode(keycode)
        except:
            pass

    def trigger_physical_button(self, button_name):
        try:
            self.driver.execute_script('mobile: pressButton', {'name': button_name})
        except:
            pass