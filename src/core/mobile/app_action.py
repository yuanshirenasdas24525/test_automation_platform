# -*- coding:utf-8 -*-
from src.core.mobile.finder.finder import Finder
from src.core.mobile.actions.executor import ActionExecutor
from src.core.mobile.actions.value_resolver import ValueResolver
from src.core.mobile.assertions.assertion import AssertionEngine
from src.core.mobile.cache.parameter_cache import ParameterCache
from src.core.mobile.device.device_action import DeviceAction
from src.utils.platform_utils import rep_expr
from src.utils.read_file import read_conf


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

        blacklist = [(bl[i], bl[i+1]) for i in range(0, len(bl), 2)] if bl else []
        whitelist = [(wl[i], wl[i+1]) for i in range(0, len(wl), 2)] if wl else []

        # 核心模块实例化
        self.cache = ParameterCache(default_params)
        self.finder = Finder(driver, blacklist, whitelist, self.device)
        self.value_resolver = ValueResolver(self.cache.pool, db_connection)
        self.executor = ActionExecutor(driver, self.device)
        self.assert_engine = AssertionEngine(db_connection, self.device)

    def app_steps(self, step):
        """完整执行单步骤"""
        # --- Find ---
        if step.get("sliding_location"):
            element = self.finder.swipe_find(step)
        else:
            element = self.finder.find(step["by"], step["finder"])

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
            self.assert_engine.assert_result(result, expected)

        return result