from unittest.mock import Mock, MagicMock
from queue import Queue, Empty
from pathlib import * 
from hashlib import sha1

import unittest
import random
import tempfile
import time

# from Fiume.ttl import TTL_table
from Fiume.ttl_cond import TTL_table

class TTL_tests(unittest.TestCase):
    def setUp(self):
        self.ttl = TTL_table(1)

    def test_empty_ttl_has_nothing_ready(self):
        self.assertTrue(not self.ttl.any_ready())

    def test_simple_blocking_add(self):
        self.ttl.add("a")
        
        self.assertTrue(not self.ttl.any_ready())
        time.sleep(1)
        self.assertTrue(self.ttl.extract(n=1) == ["a"])

    def test_blocking_timeout(self):
        self.ttl.add("a")
        try:
            self.ttl.extract(n=2, timeout=1)
            self.assertTrue(False, "Should have raised Empty!")
        except Empty:
            pass

    def test_blocking_timeout(self):
        self.ttl.add("a")
        try:
            l = self.ttl.extract(n=1, timeout=0.5, accontentati=True)
            self.assertEqual(l, [])
        except Empty:
            self.assertTrue(False, "Should have returned empty list!")
        
    def test_non_blocking(self):
        self.ttl.add("a")
        self.ttl.add("b")
        time.sleep(0.5)
        self.ttl.add("c")

        time.sleep(0.5)
        self.assertEqual(
            self.ttl.extract(n=3, timeout=0, accontentati=True),
            ["a", "b"]
        )
        
    def test_exponential_backoff(self):
        self.ttl.add("A")
        time.sleep(1)
        self.ttl.extract(n=1)
        
        self.assertEqual(self.ttl.add("A"), 2)
