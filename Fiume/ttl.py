from typing import *
from queue import Queue
import time

class TTL_table:
    def __init__(self):
        self.ttl: Dict[Hashable, int] = dict()
        self.available: Queue = Queue()

    def add(self, obj: Hashable, ttl: int, starting_time: int=None):
        if starting_time is None:
            starting_time = int(time.time())

        self.ttl[obj] = starting_time + ttl

    def _update_available(self) -> bool:
        current_time = int(time.time())

        to_delete = list()
        for obj, expiration in self.ttl.items():
            if expiration <= current_time:
                self.available.put(obj)
                to_delete.append(obj)

        for obj in to_delete:
            del self.ttl[obj]

    def any_ready(self):
        self._update_available()
        return not self.available.empty()

    def extract(self, n=1, blocking=True, timeout=1) -> List[Any]:
        self._update_available()
        out = list()
        
        if blocking:
            for _ in range(n):
                out.append(self.available.get(timeout=1))
        else:
            for _ in range(n):
                if self.available.empty():
                    return out
                out.append(self.available.get())

        return out

