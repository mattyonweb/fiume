
import unittest
from unittest.mock import Mock, MagicMock
from queue import Queue

import random

from Fiume.utils import *
import Fiume.master as master


def repeat(times):
    def repeatHelper(f):
        def callHelper(*args):
            for i in range(0, times):
                f(*args)

        return callHelper

    return repeatHelper


class SinglePeer(unittest.TestCase):

    def setUp(self):
        self.peer  = Mock(address=("localhost", 50154), queue_in=Queue())
        self.peer2 = Mock(address=("localhost", 50155), queue_in=Queue())
        self.peer3 = Mock(address=("localhost", 50157), queue_in=Queue())

        self.initial_bitmap = [False for _ in range(100)]
        
        self.mcu = master.MasterControlUnit(self.initial_bitmap)
        self.mcu.main()
        self.mcu.add_connection_to(self.peer)
        
    def tearDown(self):
        self.mcu.queue_in.put(M_KILL())

    def send_mcu(self, message):
        """ Helper """
        self.mcu.queue_in.put(message)

    def get_mex(self, peer, timeout=1):
        return peer.queue_in.get(timeout)
    
    ##############################
    
    def test_when_i_have_nothing_and_peer_everything(self):
        """ 
        When newly connected to a peer, the master should assign the 
        PeerManager 10 pieces to download. 
        """
        self.send_mcu(
            M_PEER_HAS(list(range(100)), self.peer.address, schedule_new_pieces=10)
        )

        mex_to_peer = self.peer.queue_in.get(timeout=1)

        # Schedula 10 pezzi
        self.assertEqual(len(mex_to_peer.pieces_index), 10)

        # Non schedula mai due volte lo stesso pezzo
        self.assertSequenceEqual(
            sorted(mex_to_peer.pieces_index),
            sorted(list(set(mex_to_peer.pieces_index)))
        )


    @repeat(9) # setUp() -> test (9 times) -> tearDown()
    def test_dont_reask_already_scheduled_pieces(self):
        """
        When a whole piece is received, this piece is sent to the master;
        who will:
        1) inform all the peerManagers of the new piece, with a
        HAVE message, so that everyone can update its bitmap; and 
        2) update its own global bitmap
        3) assign a new piece to download to the peerManager
        """
        self.send_mcu(
            M_PEER_HAS(list(range(100)), self.peer.address, schedule_new_pieces=10)
        )

        # Master assigns schedules these pieces for the PeerManager 
        scheduled_pieces = self.peer.queue_in.get().pieces_index

        # We pretend that one of them has been received
        random_downloaded_piece = random.choice(scheduled_pieces) 
        self.send_mcu(
            M_PIECE(random_downloaded_piece, b"", self.peer.address)
        )

        # We receive two messages, one is the HAVE and the next one is the
        # new scheduled piece to download
        new_have     = self.peer.queue_in.get(timeout=1)
        new_schedule = self.peer.queue_in.get(timeout=1)

        # Checks on HAVE
        self.assertIsInstance(new_have, M_NEW_HAVE)
        self.assertEqual(new_have.piece_index, random_downloaded_piece)

        # Checks on SCHEDULE
        self.assertIsInstance(new_schedule, M_SCHEDULE)
        self.assertEqual(len(new_schedule.pieces_index), 1)
        self.assertNotIn(
            random_downloaded_piece,
            new_schedule.pieces_index
        )
        self.assertNotIn(
            new_schedule.pieces_index,
            scheduled_pieces,
            new_schedule
        )
       

    def test_dont_ask_piece_already_scheduled_to_another_peer(self):
        """
        Peer1 has all pieces from [0..60];
        Peer2 has all pieces from [50..100];
        Master must not schedule, for peer2, any piece from 50..60
        """
        self.mcu.add_connection_to(self.peer2)
        self.send_mcu(
            M_PEER_HAS(list(range(60)),
                       self.peer.address,
                       schedule_new_pieces=60)
        )
        self.send_mcu(M_PEER_HAS(list(range(50, 100)), self.peer2.address))

        p1_scheduled = self.peer.queue_in.get()
        p2_scheduled = self.peer2.queue_in.get()

        print(p1_scheduled)
        
        self.assertFalse(
            any(x in range(50, 60) for x in p2_scheduled.pieces_index),
            p2_scheduled.pieces_index
        )

        
    def test_when_no_blocks_to_assign_assign_nothing(self):
        self.send_mcu(M_PEER_HAS([0], self.peer.address, schedule_new_pieces=3))

        scheduled = self.peer.queue_in.get().pieces_index[0]

        self.assertEqual(scheduled, 0)

        self.send_mcu(M_PIECE(0, b"", self.peer.address))

        _ = self.peer.queue_in.get() # HAVE message

        self.assertEqual(
            self.peer.queue_in.get().pieces_index, []
        )

        
    def test_graceful_disconnection_of_peer(self):
        print(self.mcu.already_scheduled())

        # A peer reserves for itself all the pieces
        self.send_mcu(M_PEER_HAS(list(range(100)), self.peer.address, schedule_new_pieces=100))

        print(self.get_mex(self.peer))
        print(self.mcu.already_scheduled())

        # A new peer tries to connect and ask for pieces, but
        # peer1 has already scheduled all the pieces; as a result,
        # no piece is scheduled for peer2
        self.mcu.add_connection_to(self.peer2)
        self.send_mcu(M_PEER_HAS(list(range(100)), self.peer2.address))
        self.assertEqual(
            self.get_mex(self.peer2).pieces_index,
            []
        )

        self.mcu.add_connection_to(self.peer3)
        self.send_mcu(M_PEER_HAS(list(range(50)), self.peer3.address))
        self.assertEqual(
            self.get_mex(self.peer3).pieces_index,
            []
        )

        # A new piece arrives from peer1
        self.send_mcu(M_PIECE(99, b"", self.peer.address))

        # But now, peer1 disconnects!
        self.send_mcu(M_DISCONNECTED(self.peer.address))

        # HAVE messages, who cares
        _, _ = self.get_mex(self.peer2), self.get_mex(self.peer3)

        # SCHEDULE messages
        mex_peer2 = self.get_mex(self.peer2)
        mex_peer3 = self.get_mex(self.peer3)

        self.assertIsInstance(mex_peer3, M_SCHEDULE)
        self.assertIsInstance(mex_peer3, M_SCHEDULE)

        # No piece is scheduled to both the peers
        self.assertEqual(
            set(mex_peer2.pieces_index) & set(mex_peer3.pieces_index),
            set()
        )

        # Peer 3, who has only pieces [0..50], is not scheduled a piece
        # greater than 50
        self.assertTrue(
            all(x <= 50 for x in mex_peer3.pieces_index)
        )

        # All together, the two SCHEDULEs cover the scheduled pieces for P1
        self.assertSetEqual(
            set(mex_peer2.pieces_index) | set(mex_peer3.pieces_index),
            set(range(99))
        )
                            
