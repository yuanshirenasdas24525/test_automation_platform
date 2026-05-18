# -*- coding:utf-8 -*-
import time
from runners.app.finder import Finder
from runners.app.actions.executor import ActionExecutor
from runners.app.actions.value_resolver import ValueResolver
from runners.app.assertions.assertion import AssertionEngine
from runners.app.parameter_cache import ParameterCache
from runners.app.device_action.device_action import DeviceAction
from utils.platform_utils import rep_expr
from utils.reload_config import config_center


class AppAction:
    """
    统一协调 Finder → Executor → Assertion → Cache 的执行流程。
    """

    def __init__(self, driver, db_connection=None):
        self.driver = driver
        self.device = DeviceAction(driver)
        self.db = db_connection

        # 配置读取（从 DB 配置中心）
        default_params = config_center.get("default_parameters", default={})

        ui_el = config_center.get("ui_element_list", default={})
        bl_raw = [x.strip() for x in ui_el.get("blacklist", "").split(",") if x.strip()]
        wl_raw = [x.strip() for x in ui_el.get("whitelist", "").split(",") if x.strip()]

        # (by, locator) 两两配对。奇数个时丢掉最后一项
        def _pair(items):
            it = iter(items or [])
            return list(zip(it, it))

        blacklist = _pair(bl_raw)
        whitelist = _pair(wl_raw)

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