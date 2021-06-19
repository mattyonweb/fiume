
import unittest
from unittest.mock import Mock, MagicMock
from queue import Queue

import random

import Fiume.utils as utils
import Fiume.master as master

class SinglePeer(unittest.TestCase):

    def setUp(self):
        self.peer = Mock(address=("localhost", 50154), queue_in=Queue())
        self.initial_bitmap = [False for _ in range(100)]
        
        self.mcu = master.MasterControlUnit(self.initial_bitmap)
        self.mcu.main()
        self.mcu.add_connection_to(self.peer)

    def tearDown(self):
        self.mcu.queue_in.put(utils.M_KILL())

    ##############################
    
    def test_when_i_have_nothing_and_peer_everything(self):
        self.mcu.queue_in.put(
            utils.M_PEER_HAS(list(range(100)), self.peer.address)
        )

        self.assertEqual(
            self.peer.queue_in.get(timeout=1),
            utils.M_SCHEDULE(list(range(10)))
        )
