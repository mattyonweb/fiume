from unittest.mock import Mock, MagicMock
from queue import Queue, Empty
from pathlib import * 
from hashlib import sha1

import unittest
import random
import tempfile
import time

from Fiume.ttl import TTL_table

class TTL_tests(unittest.TestCase):
    def setUp(self):
        self.ttl = TTL_table()

    def test_empty_ttl_has_nothing_ready(self):
        self.assertTrue(not self.ttl.any_ready())

    def test_simple_blocking_add(self):
        self.ttl.add("a", 0)
        self.assertTrue(self.ttl.any_ready())
        self.assertTrue(self.ttl.extract(n=1, blocking=True) == ["a"])
        self.assertTrue(not self.ttl.any_ready())

    def test_simple_blocking_add(self):
        self.ttl.add("a", 1)
        self.assertTrue(not self.ttl.any_ready())
        time.sleep(1)
        self.assertTrue(self.ttl.extract(n=1, blocking=True) == ["a"])

    def test_blocking_timeout(self):
        self.ttl.add("a", 10)
        try:
            self.ttl.extract(n=2, blocking=True)
            self.assertTrue(False, "Should have raised Empty!")
        except Empty:
            pass

    def test_non_blocking(self):
        self.ttl.add("a", 0)
        self.ttl.add("b", 0.5)
        self.ttl.add("c", 100)

        time.sleep(1)
        self.assertEqual(self.ttl.extract(n=3, blocking=False), ["a", "b"])
        
