from typing import *
from queue import Queue, Empty
import time
import threading

Timestamp = int
TTL = float

class AlreadyPresent(KeyError):
    pass
    
class TTL_table:
    def __init__(self, default_ttl: float):
        self.default_ttl = default_ttl

        # Objs che sono stati aggiunti ma non ancora estratti
        self.not_yet_extracted: Dict[Hashable, Tuple[TTL, threading.Thread]] = dict()
        # Objs già estratti ma ancora in fase di expiration
        self.recently_extracted: Dict[Hashable, Tuple[TTL, threading.Thread]] = dict()
        # Objs estratti
        self.available: Queue = Queue()

        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._not_empty = threading.Condition(self._lock)

        
    def add(self, obj: Hashable) -> int:
        """ 
        Adds an object to the TTL table. 

        Returns the TTL for the added object.
        """
        with self._lock:
            if obj in self.not_yet_extracted:
                raise AlreadyPresent()

            ttl = self.default_ttl
            
            if obj in self.recently_extracted:
                ttl = self.recently_extracted[obj][0] * 2

                # deactive timer thread for recently_extracted auto-destruction
                self.recently_extracted[obj][1].cancel()
                del self.recently_extracted[obj]

                
            t = threading.Timer(ttl, lambda: self.available.put(obj))    
            self.not_yet_extracted[obj] = (ttl, t)
            t.start()

        return ttl

    
    def any_ready(self) -> bool:
        """
        Returns whether there is any available (aka. expired) object.
        """
        return not self.available.empty()

    
    def extract(self, n=1, timeout=None, accontentati=False) -> List[Any]:
        """
        Extracts n objects from the expired set. 
        
        Timeout is the timeout for an object when our queue is empty. None means forever.

        Accontentati is used when you request the extraction of `n` objects, 
        but only `m` (`m` < `n`) are inside the queue. If Accontentati is True,
        you return only `m` objects; otherwise, wait for `n` objects.
        """
        out = list()
        
        for _ in range(n):
            try:
                obj = self.available.get() if timeout is None else self.available.get(timeout=timeout)
                out.append(obj)
            except Empty as e:
                if accontentati:
                    return out
                raise e

            # Add entry in recently_extracted
            self.recently_extracted[obj] = (
                self.not_yet_extracted[obj][0],
                threading.Timer(self.not_yet_extracted[obj][0],
                                lambda: self._del(self.recently_extracted, obj))
            )

            self.recently_extracted[obj][1].start()
            self._del(self.not_yet_extracted, obj)

        return out
    

    def _del(self, d, key):
        if key in d:
            del d[key]
        else:
            pass # maybe key was already extracted from d in other ways
