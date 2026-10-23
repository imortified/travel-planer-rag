from time import monotonic


class MemoryRedis:
    """Unit-test Redis double; production always uses redis-py."""

    def __init__(self):
        self.values = {}

    def get(self, key):
        value = self.values.get(key)
        if value is None or monotonic() >= value[1]:
            return None
        return value[0]

    def set(self, key, value, ex):
        self.values[key] = (value, monotonic() + ex)
        return True
