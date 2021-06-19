import threading

from queue import Queue
from typing import *
from Fiume.utils import *

class ConnectionStatus:
    def __init__(self, peer):
        self.queue_in = peer.queue_in
        self.peer_has = list()
        self.already_suggested = set()

        
    def update_peer_has(self, pieces: List[int]):
        """
        Updates local list of pieces possessed by the peer.
        """
        self.peer_has += pieces

        
    def schedule_suggestions(self, n=10) -> List[int]:
        """
        Suggests n pieces to the peer.
        """
        candidates = set(self.peer_has) - self.already_suggested

        if len(candidates) == 0:
            print("No candidates found...")
            return []
        
        chosen = random.sample(
            list(candidates),
            k = min(n, len(candidates))
        )

        self.already_suggested |= set(chosen) #union
        return chosen
        

    
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
        """ 
        Sends a message to a peer manager, through Queues.
        """
        self.connections[address].queue_in.put(mex)

        
    def send_all(self, mex, exclude=lambda x: False):
        """
        Sends a message to every connected peer.
        """
        for p in self.connections:
            if not exclude(p):
                self.send_to(p, mex)

                
    def update_global_bitmap(self, new_piece: int):
        """
        When receiving PIECE message, updates the global bitmap.
        Must also inform all peers of this update!
        """
        self.bitmap[new_piece] = True
        self.send_all(M_NEW_HAVE(new_piece))

        
    def receiver_loop(self):
        while True:
            mex = self.queue_in.get()

            assert isinstance(mex, MasterMex)

            if isinstance(mex, M_PEER_HAS):
                status = self.connections[mex.sender]
                status.update_peer_has(mex.pieces_index)
                
                answer = M_SCHEDULE(status.schedule_suggestions())
                self.send_to(mex.sender, answer)

                
            if isinstance(mex, M_PIECE):
                status = self.connections[mex.sender]
                
                self.update_global_bitmap(mex.piece_index)
                self.send_to(mex.sender,
                             M_SCHEDULE(status.schedule_suggestions(n=1)))
                
            elif isinstance(mex, M_KILL):
                break


    def main(self):
        t = threading.Thread(target=self.receiver_loop)
        t.start()
        
