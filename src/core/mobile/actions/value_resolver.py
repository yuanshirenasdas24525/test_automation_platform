from src.utils.function_executor import exec_func
from src.utils.platform_utils import rep_expr
from src.utils.logger import LOGGER


class ValueResolver:
    def __init__(self, cache_pool, db_connection=None):
        self.cache = cache_pool
        self.db = db_connection

    def resolve(self, step):
        raw = step.get("value")
        retrieve = step.get("retrieve")

        if isinstance(retrieve, str) and retrieve.startswith("sql:"):
            retrieve = self.db.fetchone(retrieve[4:])

        param = self.cache.get(retrieve, "") if retrieve else ""

        if isinstance(raw, str) and raw.startswith("function:"):
            return exec_func(raw, param)

        return rep_expr(raw, self.cache.all())
