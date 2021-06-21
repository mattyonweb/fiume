import threading
import logging 

from queue import Queue
from typing import *

from Fiume.utils import *

logging.basicConfig(
    level=logging.DEBUG,
    format="[%(levelname)s] [%(name)s] %(message)s"
)

class ConnectionStatus:
    def __init__(self, peer):
        self.queue_in: Queue    = peer.queue_in
        self.peer_has: Set[int] = set()
        self.already_scheduled: Set[int] = set()

        
    def update_peer_has(self, pieces: List[int]):
        """
        Updates local list of pieces possessed by the peer.
        """
        self.peer_has |= set(pieces)

    def set_suggested(self, pieces: List[int]):
        self.already_scheduled |= set(pieces)
        
    def not_yet_scheduled(self) -> Set[int]:
        """
        Returns all the pieces that the peer has and that 
        were not already scheduled for request.
        """
        return self.peer_has - self.already_scheduled

    
    def completed_piece(self, piece_idx: int):
        """ 
        When we receive the full piece, remove it from the 
        already_scheduled set.
        """
        self.already_scheduled -= {piece_idx}


        
class MasterControlUnit:
    def __init__(self, metainfo, initial_bitmap, options):
        self.logger = logging.getLogger("Master")
        
        self.metainfo = metainfo
        self.options  = options
        self.bitmap: List[bool] = initial_bitmap
        
        self.connections: Dict[Address, ConnectionStatus] = dict()
        self.queue_in = Queue()

        # To prohbit concurrent access to the download file
        self.lock_download_file = threading.Lock()

        
    def get_master_queue(self) -> Queue():
        """
        Returns the queue with which communicate with master.
        """
        return self.queue_in
    
        
    def add_connection_to(self, peer: "PeerManager"):
        """
        Call this when you connect to a new peer.
        """
        self.connections[peer.address] = ConnectionStatus(peer)
        self.send_to(peer.address, M_OUR_BITMAP(self.bitmap))
        
        
    def send_to(self, address: Address, mex: MasterMex):
        """ 
        Sends a message to a peer manager, through the appropriate queue.
        """
        self.connections[address].queue_in.put(mex)

        
    def send_all(self, mex, exclude: Address=None):
        """
        Sends a message to every connected peer.
        """
        for p in self.connections:
            if p != exclude:
                self.send_to(p, mex)

                
    def update_global_bitmap(self, new_piece: int, peer_from: Address):
        """
        When receiving PIECE message, updates the global bitmap.
        Must also inform all peers of this update!
        """
        self.bitmap[new_piece] = True

        update_bitmap_file( # in utils.py
            # self.options["output_file"],
            self.metainfo.download_fpath,
            self.bitmap
        )

        self.send_all(M_NEW_HAVE(new_piece), peer_from)
        

    def bitmap_to_set(self) -> Set[int]:
        # TODO: sposta in utils
        out = set()
        for i in range(len(self.bitmap)):
            if self.bitmap[i]:
                out.add(i)
        return out

    
    def already_scheduled(self) -> Set[int]:
        """
        Returns all the pieces already scheduled by any peerManager.
        """
        return set.union(
            *[state.already_scheduled for state in self.connections.values()]
        )

    
    def schedule_for(self, address: Address, n=10) -> List[int]:
        """ 
        Schedules pieces to requests for a peer, taking into accounts
        the scheduled pieces for all other peers. 
        """
        state = self.connections[address]

        # Candidates pieces for peer P are those that:
        # 1. P owns
        # 2. Were not already assigned to PeerManager for P
        # 3. Were not already assigned to /any/ peerManager
        candidates_pieces = (
            state.not_yet_scheduled() -
            self.already_scheduled() -
            self.bitmap_to_set()
        )

        if len(candidates_pieces) == 0:
            # print("No candidates found...")
            return []
        
        chosen = random.sample(
            list(candidates_pieces),
            k = min(n, len(candidates_pieces))
        )

        state.set_suggested(chosen)
        
        return chosen


    def redistribute_pieces_of(self, address: Address) -> Dict[Address, List[int]]:
        """
        The goal is to redistribute the already scheduled pieces of
        peer P to all the other peers.
        
        We build a table of possible peers to which redistribute 
        the pieces; if more than one choice is possible, simply choose
        randomly.
        """
        state = self.connections[address]
        redistrib_pieces = state.already_scheduled
        
        # (piece_index da redistribuire) : (possibili peers che ce l'hanno) 
        table: Dict[int, List[Address]] = {
            p : [] for p in redistrib_pieces
        }
        
        for peer, p_state in self.connections.items():
            if peer == address:
                continue
            
            for common_piece in (p_state.peer_has & redistrib_pieces):
                table[common_piece].append(peer)

                
        new_assignments: Dict[Address, List[int]] = dict()
        
        for piece, candidate_peers in table.items():
            if candidate_peers == []:
                continue
            
            if len(candidate_peers) == 1:
                candidate = candidate_peers[0]
            else:
                candidate = random.choice(candidate_peers)

            new_assignments[candidate] = (
                new_assignments.get(candidate, []) + [piece]
            )

        return new_assignments

    
    def write_piece_to_file(self, piece_index: int, data: bytes):
        """
        Writes an entire piece, received from a peer, to the downloaded
        file. 

        It assumes that the data received where already hash-verified by
        the peer manager! 
        """
        with self.lock_download_file:

            if not self.metainfo.download_fpath.exists():
                self.metainfo.download_fpath.touch()

            with open(self.metainfo.download_fpath, "r+b") as f:
                # TODO: assert su lungehzza data
                f.seek(piece_index * self.metainfo.piece_size)
                f.write(data)

                
    def read_piece_from_file(self, piece_index) -> bytes:
        """
        Reads an entire piece from the downloaded file.
        """
        with self.lock_download_file:

            with open(self.metainfo.download_fpath, "r+b") as f:
                f.seek(piece_index * self.metainfo.piece_size)
                return f.read(self.metainfo.piece_size)


    
    
    def receiver_loop(self):
        while True:
            mex = self.queue_in.get()

            assert isinstance(mex, MasterMex)

            # When we are informed that a peer 
            if isinstance(mex, M_PEER_HAS):
                status = self.connections[mex.sender]
                status.update_peer_has(mex.pieces_index)

                answer = M_SCHEDULE(
                    self.schedule_for(mex.sender, n=mex.schedule_new_pieces)
                )
                self.send_to(mex.sender, answer)

                
            elif isinstance(mex, M_PIECE):
                status = self.connections[mex.sender]
                status.completed_piece(mex.piece_index)

                self.write_piece_to_file(mex.piece_index, mex.data)
                self.update_global_bitmap(mex.piece_index, mex.sender)                
                self.send_to(mex.sender,
                             M_SCHEDULE(self.schedule_for(mex.sender, n=mex.schedule_new_pieces)))

                # When completed (= we have all the pieces), inform all peers that we
                # have completed the download. The peers will decide if mantaining the
                # connection and seed, or to disconnect
                if all(self.bitmap):
                    self.send_all(M_DEBUG("completed", None))
                    print("Completed download!")

                    
            elif isinstance(mex, M_DISCONNECTED):
                mapping = self.redistribute_pieces_of(mex.sender)
                self.send_to(mex.sender, M_KILL())
                del self.connections[mex.sender]

                for peer_addr, new_scheduled in mapping.items():
                    self.connections[peer_addr].set_suggested(new_scheduled)                    
                    self.send_to(peer_addr, M_SCHEDULE(new_scheduled))

                    
            elif isinstance(mex, M_PEER_REQUEST):
                # TODO: un'idea. Al posto che inviare il pezzo intero sulla queue
                # (che è pesante, visto che un pezzo può essere anche 1Mb)
                # invia al peer il Lock usato per scrivere/leggere il file,
                # self.lock_download_file! 
                if not self.bitmap[mex.piece_index]:
                    self.send_to(mex.sender, M_ERROR(mex, "We don't have requested piece"))
                    continue
                
                data = self.read_piece_from_file(mex.piece_index)
                self.send_to(mex.sender,
                             M_PIECE(mex.piece_index, data, None, None))

                
            elif isinstance(mex, M_KILL):
                # Only debug, or user input
                break


    def main(self):
        t = threading.Thread(target=self.receiver_loop)
        t.start()
        
