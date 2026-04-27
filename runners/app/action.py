# -*- coding:utf-8 -*-
import time
from runners.app.finder import Finder
from runners.app.actions.executor import ActionExecutor
from runners.app.actions.value_resolver import ValueResolver
from runners.app.assertions.assertion import AssertionEngine
from runners.app.parameter_cache import ParameterCache
from runners.app.device_action.device_action import DeviceAction
from utils.platform_utils import rep_expr
from utils.read_conf import read_conf


class AppAction:
    """
    统一协调 Finder → Executor → Assertion → Cache 的执行流程。
    """

    def __init__(self, driver, db_connection=None):
        self.driver = driver
        self.device = DeviceAction(driver)
        self.db = db_connection

        # 配置读取
        default_params = read_conf.get_dict("default_parameters")

        bl = read_conf.get_list("ui_element_list", "blacklist")
        wl = read_conf.get_list("ui_element_list", "whitelist")

        # (by, locator) 两两配对。奇数个时丢掉最后一项（配置少写一半比炸有用），
        # 并走 zip 天然短路，不靠 index 访问，避免 IndexError。
        # 历史坑：配置里 `whitelist = ` 是空值，read_conf.get_list 早期返回 [""]
        # 单元素列表，过了 `if wl` 判断，然后 wl[1] 越界。现在 get_list 已经
        # 过滤空串，这里再兜一次，等于双保险。
        def _pair(items):
            it = iter(items or [])
            return list(zip(it, it))

        blacklist = _pair(bl)
        whitelist = _pair(wl)

        # 核心模块实例化
        self.cache = ParameterCache(default_params)
        self.finder = Finder(driver, blacklist, whitelist, self.device)
        self.value_resolver = ValueResolver(self.cache.pool, db_connection)
        self.executor = ActionExecutor(driver, self.device)
        self.assert_engine = AssertionEngine(db_connection, self.device)

    def app_steps(self, step):
        """完整执行单步骤"""
        # --- Wait ---
        if step.get("wait"):
            time.sleep(float(step.get("wait")))

        # --- Find ---
        if step.get("sliding_location"):
            element = self.finder.swipe_find(step)
        else:
            element = self.finder.find(step.get("by"), step.get("locator"))

        # --- Value ---
        value = self.value_resolver.resolve(step)

        # --- Execute ---
        result = self.executor.execute(step, element, value)

        # --- Cache ---
        if step.get("deposit"):
            self.cache.set(step.get("deposit"), result)

        # --- Assert ---
        if step.get("expected"):
            expected = rep_expr(step["expected"], self.cache.pool)
            self.assert_engine.assert_value(result, expected, assert_type="equal")

        return result