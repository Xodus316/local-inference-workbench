from collections import OrderedDict

class ExpiringLRU:
    def __init__(self, capacity, clock):
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        self._capacity = capacity
        self._clock = clock
        self._data = OrderedDict()

    def _purge(self):
        now = self._clock()
        expired = [k for k, (v, deadline) in self._data.items() if deadline is not None and now >= deadline]
        for k in expired:
            del self._data[k]

    def put(self, key, value, ttl=None):
        self._purge()
        if ttl is not None:
            if ttl <= 0:
                self._data.pop(key, None)
                return None
            deadline = self._clock() + ttl
        else:
            deadline = None
        if key in self._data:
            self._data[key] = (value, deadline)
            self._data.move_to_end(key)
        else:
            if len(self._data) >= self._capacity:
                self._data.popitem(last=False)
            self._data[key] = (value, deadline)
        return None

    def get(self, key, default=None):
        self._purge()
        if key in self._data:
            value, _ = self._data[key]
            self._data.move_to_end(key)
            return value
        return default

    def delete(self, key):
        self._purge()
        if key in self._data:
            del self._data[key]
            return True
        return False

    def __len__(self):
        self._purge()
        return len(self._data)