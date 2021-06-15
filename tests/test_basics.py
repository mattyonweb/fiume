import unittest
import random
import threading
import time
import random

import Fiume.utils as utils
# import Fiume.state_machine as sm

import logging
logging.disable(logging.WARNING)


class BasicsTestCase(unittest.TestCase):
    def test_bitmap_conversion(self):
        a = [True, False, False, True, False, True, True, False]
        self.assertTrue(utils.bool_to_bitmap(a)[0] == 150)

        b = [True, False, False, True, False, True, True]
        self.assertTrue(utils.bool_to_bitmap(b)[0] == 150)

        c = [True, False, False, True, False, True, True, False, True]
        self.assertTrue(utils.bool_to_bitmap(c)[0] == 150 and
                        utils.bool_to_bitmap(c)[1] == 128)
                        
    def test_bitmap_involutive(self):
        for _ in range(200):
            num_pieces = random.randint(0, 50)
            bitmap_bool = [bool(random.randint(0, 1)) for _ in range(num_pieces)]

            self.assertEqual(
                bitmap_bool,
                utils.bitmap_to_bool(
                    utils.bool_to_bitmap(bitmap_bool),
                    num_pieces=num_pieces
                )
            )
