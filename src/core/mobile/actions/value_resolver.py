from string import Template
from src.utils.function_executor import exec_func
from src.utils.logger import LOGGER


class ValueResolver:
    def __init__(self, cache_pool, db_connection=None):
        self.cache = cache_pool
        self.db = db_connection

    def resolve(self, step):
        raw = step.get("value")
        retrieve = step.get("retrieve")

        param = self.cache.get(retrieve, "") if retrieve else ""

        if isinstance(raw, str) and raw.startswith("function:"):
            return exec_func(raw, param)

        return self.replace_str(raw)

    def replace_str(self, text):
        try:
            return Template(text).safe_substitute(self.cache)
        except:
            return text