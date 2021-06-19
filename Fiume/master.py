import threading

from queue import Queue
from typing import *
from Fiume.utils import *

class ConnectionStatus:
    def __init__(self, peer):
        self.queue_in = peer.queue_in
        self.peer_has = set()
        self.already_suggested = set()

        
    def update_peer_has(self, pieces: List[int]):
        """
        Updates local list of pieces possessed by the peer.
        """
        self.peer_has |= set(pieces)

        
    def not_yet_scheduled(self) -> Set[int]:
        """
        Returns all the pieces that the peer has and that 
        were not already scheduled for request.
        """
        return self.peer_has - self.already_suggested


    
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
        Sends a message to a peer manager, through the appropriate queue.
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


    def bitmap_to_set(self):
        out = set()
        for i in range(len(self.bitmap)):
            if self.bitmap[i]:
                out.add(i)
        return out

    
    def yet_to_schedule(self) -> Set[int]:
        """
        Returns all the pieces not yet scheduled by any peerManager.
        """
        return set.union(
            *[state.already_suggested for state in self.connections.values()]
        )

    
    def schedule_for(self, address, n=10):
        """ 
        Schedules pieces to requests for a peer, taking into accounts
        the scheduled pieces for all other peers. 
        """
        state = self.connections[address]

        candidates_pieces = (
            state.not_yet_scheduled() -
            self.yet_to_schedule()
        )

        if len(candidates_pieces) == 0:
            print("No candidates found...")
            return []
        
        chosen = random.sample(
            list(candidates_pieces),
            k = min(n, len(candidates_pieces))
        )

        state.already_suggested |= set(chosen) #union
        return chosen

    
    def receiver_loop(self):
        while True:
            mex = self.queue_in.get()

            assert isinstance(mex, MasterMex)

            if isinstance(mex, M_PEER_HAS):
                status = self.connections[mex.sender]
                status.update_peer_has(mex.pieces_index)
                
                # answer = M_SCHEDULE(status.schedule_suggestions(n=mex.schedule_new_pieces))
                answer = M_SCHEDULE(
                    self.schedule_for(mex.sender, n=mex.schedule_new_pieces)
                )
                self.send_to(mex.sender, answer)

                
            if isinstance(mex, M_PIECE):
                status = self.connections[mex.sender]
                
                self.update_global_bitmap(mex.piece_index)
                self.send_to(mex.sender,
                             M_SCHEDULE(self.schedule_for(mex.sender, n=mex.schedule_new_pieces)))

                # self.send_to(mex.sender,
                #              M_SCHEDULE(status.schedule_suggestions(n=mex.schedule_new_pieces)))
                
            elif isinstance(mex, M_KILL):
                break


    def main(self):
        t = threading.Thread(target=self.receiver_loop)
        t.start()
        
