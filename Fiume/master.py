import Fiume.utils as utils
import threading

from queue import Queue
from typing import *

class ConnectionStatus:
    def __init__(self, peer):
        self.queue_in = peer.queue_in
        self.peer_has = list()

    def update_peer_has(self, pieces: List[int]):
        """
        Updates local list of pieces possessed by the peer.
        """
        self.peer_has += pieces
        sorted(self.peer_has)
        
    def schedule_suggestions(self, n=10):
        """
        Suggests n pieces to the peer
        """
        return self.peer_has[:n]

    
class MasterControlUnit:
    def __init__(self, initial_bitmap):
        self.bitmap = initial_bitmap
        self.connections = dict()
        self.queue_in = Queue()
        
    def add_connection_to(self, peer):
        """
        Call this when you connect to a new peer.
        """
        self.connections[peer.address] = ConnectionStatus(peer)

    def send_to(self, address, mex):
        self.connections[address].queue_in.put(mex)

    def receiver_loop(self):
        while True:
            mex = self.queue_in.get()

            assert isinstance(mex, utils.MasterMex)

            if isinstance(mex, utils.M_PEER_HAS):
                status = self.connections[mex.sender]
                status.update_peer_has(mex.pieces_index)
                
                answer = utils.M_SCHEDULE(status.schedule_suggestions())
                self.send_to(mex.sender, answer)

            elif isinstance(mex, utils.M_KILL):
                break

    def main(self):
        t = threading.Thread(target=self.receiver_loop)
        t.start()
        
