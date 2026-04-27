class ActionRegistry:
    _actions = {}

    @classmethod
    def register(cls, name, func):
        cls._actions[name] = func

    @classmethod
    def get(cls, name):
        return cls._actions.get(name)