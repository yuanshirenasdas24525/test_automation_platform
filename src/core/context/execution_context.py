# src/core/context/execution_context.py

class ExecutionContext:

    def __init__(self, record_property=None):
        self.record_property = record_property
        self.vars = {}
        self.logs = []
        self.attachments = []

    def set_var(self, key, value):
        self.vars[key] = value

    def get_var(self, key):
        return self.vars.get(key)

    def log(self, msg):
        self.logs.append(msg)

    def attach(self, name, content):
        self.attachments.append((name, content))

    def record(self, key, value):
        """🔥 写入 pytest report"""
        if self.record_property:
            self.record_property(key, value)