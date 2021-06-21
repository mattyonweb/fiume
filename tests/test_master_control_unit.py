from unittest.mock import Mock, MagicMock
from queue import Queue
from pathlib import * 
from hashlib import sha1

import unittest
import random
import tempfile

from Fiume.utils import *
import Fiume.master as master

def repeat(times):
    def repeatHelper(f):
        def callHelper(*args):
            for diocane in range(0, times):
                f(*args)

        return callHelper

    return repeatHelper


class PeerHasEverything(unittest.TestCase):

    def setUp(self):
        piece_size = 256
        piece_number = 100

        self.data = [random.randbytes(piece_size) for _ in range(piece_number)]
        self.hashes = [sha1(piece) for piece in self.data]
        
        self.file = tempfile.NamedTemporaryFile()
        self.file.close()

        self.metainfo = Mock(
            piece_size=piece_size,
            piece_number=piece_number,
            download_fpath=Path(self.file.name),
            pieces_hash = self.hashes
        )
        
        self.peer  = Mock(address=("localhost", 50154), queue_in=Queue())
        self.peer2 = Mock(address=("localhost", 50155), queue_in=Queue())
        self.peer3 = Mock(address=("localhost", 50157), queue_in=Queue())

        self.initial_bitmap = [False for _ in range(piece_number)]
        
        self.mcu = master.MasterControlUnit(
            self.metainfo,
            self.initial_bitmap,
            {"download_fpath": Path("/dev/null")}
        )
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
        self.get_mex(self.peer) # M_out_bitmap mex
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


    # @repeat(9) # setUp() -> test (9 times) -> tearDown()
    def test_dont_reask_already_scheduled_pieces(self):
        """
        When a whole piece is received, this piece is sent to the master;
        who will:
        1) inform all the peerManagers of the new piece, with a
        HAVE message, so that everyone can update its bitmap; and 
        2) update its own global bitmap
        3) assign a new piece to download to the peerManager
        """
        # Diocane, ignora queste righe
        m = self.get_mex(self.peer) # M_out_bitmap mex (maybe)

        self.send_mcu(
            M_PEER_HAS(list(range(100)), self.peer.address, schedule_new_pieces=10)
        )

        # Master assigns schedules these pieces for the PeerManager 
        scheduled_pieces = self.peer.queue_in.get().pieces_index

        # We pretend that one of them has been received
        random_piece = random.choice(scheduled_pieces) 
        self.send_mcu(
            M_PIECE(random_piece, self.data[random_piece], self.peer.address)
        )

        # We receive two messages, one is the HAVE and the next one is the
        # new scheduled piece to download
        new_have     = self.peer.queue_in.get(timeout=1)
        new_schedule = self.peer.queue_in.get(timeout=1)

        # Checks on HAVE
        self.assertIsInstance(new_have, M_NEW_HAVE)
        self.assertEqual(new_have.piece_index, random_piece)

        # Checks on SCHEDULE
        self.assertIsInstance(new_schedule, M_SCHEDULE)
        self.assertEqual(len(new_schedule.pieces_index), 1)
        self.assertNotIn(
            random_piece,
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
        self.get_mex(self.peer) # M_out_bitmap mex

        self.mcu.add_connection_to(self.peer2)
        self.get_mex(self.peer2) # M_out_bitmap mex

        self.send_mcu(
            M_PEER_HAS(list(range(60)),
                       self.peer.address,
                       schedule_new_pieces=60)
        )
        self.send_mcu(M_PEER_HAS(list(range(50, 100)), self.peer2.address))

        p1_scheduled = self.peer.queue_in.get()
        p2_scheduled = self.peer2.queue_in.get()

        self.assertFalse(
            any(x in range(50, 60) for x in p2_scheduled.pieces_index),
            p2_scheduled.pieces_index
        )


        
    def test_when_no_blocks_to_assign_assign_nothing(self):
        self.get_mex(self.peer) # M_out_bitmap mex
        self.send_mcu(M_PEER_HAS([0], self.peer.address, schedule_new_pieces=3))

        scheduled = self.peer.queue_in.get().pieces_index[0]

        self.assertEqual(scheduled, 0)

        self.send_mcu(M_PIECE(0, self.data[0], self.peer.address))

        _ = self.peer.queue_in.get() # HAVE message

        self.assertEqual(
            self.peer.queue_in.get().pieces_index, []
        )

        
    def test_graceful_disconnection_of_peer(self):
        """
        When a peer gracefully disconnects, the scheduled pieces that it had
        are redistributed to all the other pieces.
        """
        self.get_mex(self.peer) # M_out_bitmap mex

        # A peer reserves for itself all the pieces
        self.send_mcu(M_PEER_HAS(list(range(100)), self.peer.address, schedule_new_pieces=100))

        # A new peer tries to connect and ask for pieces, but
        # peer1 has already scheduled all the pieces; as a result,
        # no piece is scheduled for peer2
        self.mcu.add_connection_to(self.peer2)
        self.get_mex(self.peer2) # M_out_bitmap mex
        self.send_mcu(M_PEER_HAS(list(range(100)), self.peer2.address))
        self.assertEqual(
            self.get_mex(self.peer2).pieces_index,
            []
        )

        self.mcu.add_connection_to(self.peer3)
        self.get_mex(self.peer3) # M_out_bitmap mex
        self.send_mcu(M_PEER_HAS(list(range(50)), self.peer3.address))
        self.assertEqual(
            self.get_mex(self.peer3).pieces_index,
            []
        )

        # A new piece arrives from peer1
        self.send_mcu(M_PIECE(99, self.data[99], self.peer.address))

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

        
    def test_block_request(self):
        """ 
        Tests peer's requests for blocks.
        """
        self.get_mex(self.peer) # M_out_bitmap mex

        self.send_mcu(M_PEER_HAS(list(range(100)), self.peer.address, schedule_new_pieces=10))       
        scheduled = self.get_mex(self.peer).pieces_index
        random_piece = random.choice(scheduled)

        # A peer2 connects; it has no piece
        self.mcu.add_connection_to(self.peer2)
        self.get_mex(self.peer2) # M_out_bitmap mex
        self.send_mcu(M_PEER_HAS([], self.peer2.address))
        self.assertEqual(
            self.get_mex(self.peer2).pieces_index,
            []
        )

        # Peer2 asks for a piece that the master doesn't have;
        # an ERROR will be returned
        impossible_request = M_PEER_REQUEST(random_piece, self.peer2.address)
        self.send_mcu(impossible_request)
        
        error_p2 = self.get_mex(self.peer2)
        self.assertIsInstance(error_p2, M_ERROR)
        self.assertEqual(error_p2.on_service, impossible_request)

        
        # A piece arrives from peer1
        self.send_mcu(M_PIECE(random_piece,
                              self.data[random_piece],
                              self.peer.address))
        
        self.send_mcu(
            M_PEER_REQUEST(random_piece, self.peer2.address)
        )

        have_p2 = self.get_mex(self.peer2)
        self.assertIsInstance(have_p2, M_NEW_HAVE)
        self.assertEqual(have_p2.piece_index, random_piece)

        
        piece_p2 = self.get_mex(self.peer2)
        self.assertIsInstance(piece_p2, M_PIECE)
        self.assertEqual(piece_p2.piece_index, random_piece)
        self.assertEqual(piece_p2.data, self.data[random_piece])

        
    def test_completed(self):
        """ 
        When download is completed, master sends kill to all peers.
        """
        self.get_mex(self.peer) # M_out_bitmap mex

        self.send_mcu(M_PEER_HAS(list(range(100)), self.peer.address, schedule_new_pieces=100))       
        self.get_mex(self.peer)

        for i in range(100):
            self.send_mcu(M_PIECE(i, self.data[i], self.peer.address, schedule_new_pieces=0))
            have     = self.get_mex(self.peer) # scarta messaggio di risposta
            schedule = self.get_mex(self.peer)
            
        completed = self.get_mex(self.peer, timeout=1)
        self.assertIsInstance(completed, M_DEBUG)
        self.assertEqual(completed.data, "completed")

        
    def test_completed_but_exists_peer_with_missing_pieces(self):
        """ 
        When we complete, other peers should still be able to
        request pieces.
        """
        self.get_mex(self.peer) # M_out_bitmap mex

        self.send_mcu(M_PEER_HAS(list(range(100)), self.peer.address, schedule_new_pieces=100))       
        self.get_mex(self.peer)

        self.mcu.add_connection_to(self.peer2)
        self.get_mex(self.peer2) # M_out_bitmap mex
        self.send_mcu(M_PEER_HAS(list(range(25)), self.peer2.address))
        self.assertEqual(
            self.get_mex(self.peer2).pieces_index,
            []
        )
        
        for i in range(100):
            self.send_mcu(
                M_PIECE(i, self.data[i],
                        self.peer.address, schedule_new_pieces=0)
            )
            have     = self.get_mex(self.peer) # scarta messaggio di risposta
            schedule = self.get_mex(self.peer)

        for _ in range(100):
            self.get_mex(self.peer2) # HAVE messages

        self.send_mcu(M_PEER_REQUEST(99, self.peer2.address))
        m = self.get_mex(self.peer2)
        self.assertIsInstance(m, M_DEBUG)
        self.assertEqual(m.data, "completed")
        
        m = self.get_mex(self.peer2)
        self.assertIsInstance(m, M_PIECE)
        self.assertEqual(m.data, self.data[99])
