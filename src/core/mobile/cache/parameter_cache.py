class ParameterCache:
    def __init__(self, default_pool=None):
        self.pool = default_pool or {}

    def get(self, key, default=None):
        return self.pool.get(key, default)

    def set(self, key, value):
        self.pool[key] = value

    def all(self):
        return self.pool