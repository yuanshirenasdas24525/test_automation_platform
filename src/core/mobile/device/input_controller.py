# -*- coding:utf-8 -*-
class InputController:
    """
    控制输入类操作：点击、返回、滑动等
    """

    def __init__(self, driver):
        self.driver = driver

    def back(self):
        # Android：先尝试 driver.back()
        try:
            return self.driver.back()
        except:
            # 再尝试 keyevent 4
            try:
                self.driver.press_keycode(4)
            except:
                pass

    def tap(self, positions):
        """
        positions 示例: [(100,200), (300,400)]
        """
        try:
            return self.driver.tap(positions)
        except:
            # 兜底：使用 TouchAction
            try:
                for x, y in positions:
                    self.driver.execute_script("mobile: tap", {"x": x, "y": y})
            except:
                pass

    def swipe(self, start_x, start_y, end_x, end_y, duration=500):
        try:
            return self.driver.swipe(start_x, start_y, end_x, end_y, duration)
        except:
            # TouchAction 兜底
            try:
                action = self.driver.create_touch_action()
                action.press(x=start_x, y=start_y).wait(ms=duration).move_to(x=end_x, y=end_y).release().perform()
            except:
                pass