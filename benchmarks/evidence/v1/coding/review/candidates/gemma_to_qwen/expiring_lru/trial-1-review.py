from collections import OrderedDict

class ExpiringLRU:
    def __init__(self, capacity, clock):
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity <= 0:
            raise ValueError("capacity must be a positive int")
        self.capacity = capacity
        self.clock = clock
        self.cache = OrderedDict()  # key -> (value, deadline)

    def _purge(self):
        now = self.clock()
        expired_keys = [k for k, v in self.cache.items() if v[1] is not None and now >= v[1]]
        for k in expired_keys:
            del self.cache[k]

    def put(self, key, value, ttl=None):
        self._purge()
        now = self.clock()
        
        if ttl is not None and ttl <= 0:
            if key in self.cache:
                del self.cache[key]
            return

        deadline = (now + ttl) if ttl is not None else None
        
        if key in self.cache:
            self.cache.move_to_end(key)
        else:
            if len(self.cache) >= self.capacity:
                self.cache.popitem(last=False)
        
        self.cache[key] = (value, deadline)

    def get(self, key, default=None):
        self._purge()
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key][0]
        return default

    def delete(self, key):
        self._purge()
        if key in self.cache:
            del self.cache[key]
            return True
        return False

    def __len__(self):
        self._purge()
        return len(self.cache)