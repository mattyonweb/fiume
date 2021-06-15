import socketserver
import socket
import time
import threading
import logging
import enum
import queue
import os
import random
import pathlib 

from typing.io import *
from typing import *

try:
    import Fiume.config as config
    import Fiume.utils as utils
except:
    import utils as utils
    import config as config


logging.basicConfig(
    level=logging.DEBUG,
    format='[%(levelname)s] %(name)s %(asctime)s %(message)s',
    datefmt="%I:%M:%S"
)

class MexType(enum.Enum):
    HANDSHAKE = 84 # non cambiare
    KEEP_ALIVE = -1 #?
    CHOKE = 0
    UNCHOKE = 1
    INTERESTED = 2
    NOT_INTERESTED = 3
    HAVE = 4
    BITFIELD = 5
    REQUEST = 6
    PIECE = 7
    CANCEL = 8
    PORT = 9 # NOT USED

class Initiator(enum.Enum):
    SELF = 0
    OTHER = 1


#############################################


class PeerManager:
    def __init__(self, socket,
                 metainfo, tracker_manager,
                 out_fpath: pathlib.Path,
                 initiator: Initiator):
        
        # Peer socket
        self.socket = socket
        self.peer_ip, self.peer_port = self.socket.getsockname()
        
        self.logger = logging.getLogger("TO " + str(self.peer_ip) + ":" + str(self.peer_port))
        self.logger.debug("__init__")
        
        
        self.metainfo = metainfo
        self.tracker_manager = tracker_manager

        # Ok
        self.peer_chocking, self.am_choking = True, True
        self.peer_interested, self.am_interested = False, False

        # Needed for establishing who starts the handshake
        self.initiator: Initiator = initiator
        self.received_handshake, self.sent_handshake = False, False

        # Queues for inter-thread communication
        self.queue_to_elaborate = queue.Queue()
        self.queue_to_send_out  = queue.Queue()

        # Output file
        self.out_fpath: pathlib.Path = self.initialize_file(out_fpath)

        # Bitmaps of my/other pieces
        self.my_bitmap:   List[bool] = utils.data_to_bitmap(
            self.out_fpath,
            num_pieces=self.metainfo.num_pieces
        )
        self.peer_bitmap: List[bool] = utils.empty_bitmap(self.metainfo.num_pieces)

        # Blocks that I don't have but my peer has
        self.am_interested_in: List[int] = list()
        self.peer_interested_in: List[int] = list()
        
        self.my_progresses: Dict[int, Tuple[int, int]] = dict()
        self.peer_progresses: Dict[int, Tuple[int, int]] = dict()

        self.max_concurrent_pieces = 4
        
        self.old_messages = list()
        self.completed = False

        
    def main(self):
        self.logger.debug("main")

        # Stabilisce chi fra i due peers dovrà inviare il primo messaggio
        if self.initiator == Initiator.SELF:
            self.send_handshake()

        t1 = threading.Thread(target=self.message_receiver)
        t2 = threading.Thread(target=self.message_sender)
        t1.start()
        t2.start()
            
        t1.join(2)
        t2.join(2)


    def read_data(self, piece_index, piece_offset=0, piece_length=0) -> bytes:
        if piece_length == 0:
            piece_length = self.get_piece_size(piece_index)

        with open(self.out_fpath, "rb") as file:
            file.seek(self.metainfo.piece_size * piece_index + piece_offset, 0)
            data = file.read(piece_length)

        return data

    def write_data(self, piece_index, piece_offset, payload):
        with open(self.out_fpath, "rb+") as file:
            file.seek(self.metainfo.piece_size * piece_index + piece_offset, 0)
            return file.write(payload)

        
    def send_handshake(self):
        self.logger.debug("Sending HANDSHAKE")
        self.send_message(MexType.HANDSHAKE)
        self.sent_handshake = True
        if self.received_handshake:
            self.logger.debug("Sending BITFIELD")
            self.send_message(MexType.BITFIELD)

    def receive_handshake(self, mex):
        self.convalidate_handshake(mex)
        self.received_handshake = True
        self.logger.debug("Received HANDSHAKE")
        
        if self.sent_handshake:
            self.logger.debug("Sending BITFIELD")
            self.send_message(MexType.BITFIELD)
        else:
            self.send_handshake()
            
    def convalidate_handshake(self, mex:bytes):
        assert mex[28:48] == self.metainfo.info_hash


    def initialize_file(self, fpath: pathlib.Path):
        """ Initialize the download file """
        if not fpath.exists():
            # TODO: BUG quando ad es. il file è /a/b/c/d.txt ma
            # le cartelle b e c non esistono
            fpath.touch()
            with open(fpath, "wb") as f:
                f.write(bytes(self.metainfo.num_pieces))
            return fpath

        return fpath

    
    # Thread a sé stante
    def message_receiver(self):
        while (handshake_mex := self.socket.recv(68)) == b"":
            continue

        try:
            self.message_interpreter(handshake_mex)
        except Exception as e:
            breakpoint()
            raise e
        
        while True:
            raw_length = self.socket.recv(4)
            length = int.from_bytes(raw_length, byteorder="big", signed=False)
            # raw_mex = self.socket.recv(length)

            raw_mex = bytes()
            while length != 0:
                data = self.socket.recv(length)
                raw_mex += data
                length -= len(data)
                if length < 0:
                    breakpoint()
                    _ = 0
                if length != 0:
                    print(f"Still waiting for {length} bytes...")
                
            self.message_interpreter(raw_length + raw_mex)

            
    # Thread a sé stante
    def message_sender(self):
        while True:
            mex = self.queue_to_send_out.get()
            self.socket.sendall(mex)


    def shutdown(self):
        self.logger.debug("Shutdown after receiving empty message from peer")
        exit(0)
        # TODO
        
    def try_unchoke_peer(self):
        if not self.am_choking:
            self.logger.debug("Asked if could unchoke peer, but it is already unchoked")
            return

        if not self.peer_interested:
            self.logger.debug("Asked if could unchoke peer, but peer is not interested")
            return

        # TODO: scrivere una funzione per valutare oggettivamente se ci sono
        # contrindicazioni nell'unchokare il peer. Per ora assumo si possa
        # sempre fare.
        self.am_choking = False
        self.send_message(MexType.UNCHOKE)
    
    #######

    def message_interpreter(self, mex: bytes):
        """ Elabora un messaggio ricevuto, decidendo come rispondere e/o
        che cosa fare. """

        self.old_messages.append(("other", mex))

        # TODO: messaggio vuoto b"" = fine scambio o errore di rete
        if mex == b"":
            self.shutdown()
            
        try:
            mex_type = MexType(mex[4])
        except Exception as e:
            breakpoint()
            raise e
            
        self.logger.debug("Received message %s", str(mex_type))

        if mex_type == MexType.HANDSHAKE:
            self.receive_handshake(mex)

        elif mex_type == MexType.KEEP_ALIVE:
            self.send_message(MexType.KEEP_ALIVE)
            
        elif mex_type == MexType.CHOKE:
            self.peer_chocking = True
        elif mex_type == MexType.UNCHOKE:
            self.peer_chocking = False
            self.try_ask_for_piece()
        elif mex_type == MexType.INTERESTED:
            self.peer_interested = True
            self.try_unchoke_peer()
        elif mex_type == MexType.NOT_INTERESTED:
            self.peer_interested = False
            if not self.am_choking:
                self.send_message(MexType.CHOKE)
                
        elif mex_type == MexType.HAVE:
            self.manage_received_have(utils.to_int(mex[5:9]))

        elif mex_type == MexType.BITFIELD:
            self.interpret_received_bitfield(mex[5:])

        elif mex_type == MexType.REQUEST:
            piece_index  = utils.to_int(mex[5:9]) 
            piece_offset = utils.to_int(mex[9:13]) 
            piece_length = utils.to_int(mex[13:17]) 
            self.manage_request(piece_index, piece_offset, piece_length)

        elif mex_type == MexType.PIECE:
            piece_index  = utils.to_int(mex[5:9]) 
            piece_offset = utils.to_int(mex[9:13])
            piece_payload = mex[13:]
            self.manage_received_piece(piece_index, piece_offset, piece_payload)

        elif mex_type == MexType.CANCEL:
            print("CANCEL not implemented")

        elif mex_type == MexType.PORT:
            print("PORT not implemented")

        else:
            print("ricevuto messaggio sconosciuto")
            breakpoint()

            
    def send_message(self, mexType: MexType, **kwargs):
        self.queue_to_send_out.put(self.make_message(mexType, **kwargs))

    def make_message(self, mexType: MexType, **kwargs) -> bytes:
        mex = None
        
        if mexType == MexType.KEEP_ALIVE:
            mex = bytes([0,0,0,0])

        elif mexType == MexType.HANDSHAKE:
            mex = (utils.to_bytes(19) +
                    b"BitTorrent protocol" +
                    bytes(8) +
                    self.metainfo.info_hash +
                    utils.generate_peer_id(seed=self.peer_port))
        
        elif mexType.value in [0,1,2,3]:
            mex = (bytes([0,0,0,1]) +
                    utils.to_bytes(mexType.value, length=1))
        
        elif mexType == MexType.HAVE:
            mex = (utils.to_bytes(5, length=4) +
                    utils.to_bytes(mexType.value) +
                    utils.to_bytes(kwargs["piece_index"], length=4))
        
        elif mexType == MexType.BITFIELD:
            bitmap = utils.bool_to_bitmap(self.my_bitmap)

            mex = (utils.to_bytes(1 + len(bitmap), length=4) + 
                    utils.to_bytes(mexType.value) +
                    bitmap)

        elif mexType == MexType.REQUEST:
            mex = (utils.to_bytes(13, length=4) + 
                    utils.to_bytes(mexType.value) +
                    utils.to_bytes(kwargs["piece_index"], length=4) +
                    utils.to_bytes(kwargs["piece_offset"], length=4) +
                    utils.to_bytes(kwargs["piece_length"], length=4))

        elif mexType == MexType.PIECE:
            payload = self.read_data(
                kwargs["piece_index"],
                kwargs["piece_offset"],
                kwargs["piece_length"]
            )
            
            mex = (utils.to_bytes(9 + len(payload), length=4) + 
                    utils.to_bytes(mexType.value) +
                    utils.to_bytes(kwargs["piece_index"], length=4) +
                    utils.to_bytes(kwargs["piece_offset"], length=4) +
                    payload)

        else:
            raise Exception("Messaggio impossibile da costruire")

        self.old_messages.append(("self", mex))
        return mex
                    

    def interpret_received_bitfield(self, mex_payload: bytes):
        """ Analyzes a received bitmap """
        self.peer_bitmap = utils.bitmap_to_bool(
            mex_payload,
            num_pieces=self.metainfo.num_pieces
        )

        # Stampo a video grafichino dei pezzi 
        print("my:   |", end="")
        for my in self.my_bitmap:
            print("x" if my else " ", end="")
        print("\npeer: |", end="")
        for peer in self.peer_bitmap:
            print("x" if peer else " ", end="")
        print()

        assert len(self.my_bitmap) == len(self.peer_bitmap)
        
        # Idenitifico (if any) i pieces del mio peer che io non ho
        for i, (m,p) in enumerate(zip(self.my_bitmap, self.peer_bitmap)):
            if not m and p:
                self.am_interested_in.append(i)

                
        # Se, dal confronto fra la mia e l'altrui bitmap, scopro
        # che non mi interessa nulla di ciò che ha il peer, informalo che
        # sei NOT_INTERESTED
        if len(self.am_interested_in) == 0:
            self.logger.debug("Nothing to be interested in")
            if self.am_interested:
                self.am_interested = False
                self.send_message(MexType.NOT_INTERESTED)
            return
        
        elif (self.am_interested): # Se già mi interessava qualcosa, non fare nulla
            return

        else: # Altrimenti dichiara il tuo interesse
            self.am_interested = True
            self.logger.debug("Sending INTERESTED message")
            self.send_message(MexType.INTERESTED)
            self.ask_for_new_pieces()
            return

    def get_piece_size(self, piece_index):
        """ 
        Utile, perché l'ultimo piece da scaricare ha lunghezza 
        diversa rispetto agli altri; così viene gestito correttamente
        """
        
        if piece_index != self.metainfo.num_pieces - 1:
            return self.metainfo.piece_size

        last_piece_size = (
            self.metainfo.total_size - 
            (self.metainfo.num_pieces - 1) * self.metainfo.piece_size            
        )

        if last_piece_size != 0:
            return last_piece_size
        else:
            return self.metainfo.piece_size


        
    def ask_for_single_piece(self, piece_idx):
        self.logger.debug("Asking for new piece, number %d", piece_idx)

        piece_length = min(
            self.metainfo.block_size,
            self.get_piece_size(piece_idx)
        )
        
        try:    
            self.send_message(
                MexType.REQUEST,
                piece_index=piece_idx,
                piece_offset=0,
                # complicato, ma serve per gestire irregolarità ultimo piece: così
                # non richiedi più bytes di quelli di cui è composto l'ultimo piece
                piece_length=piece_length
            )
        except Exception as e:
            breakpoint()
            raise e
        
        # self.get_piece_size serve per gestire len irregolare dell'ultimo piece
        self.my_progresses[piece_idx] = (0, self.get_piece_size(piece_idx))

        
        
    def ask_for_new_pieces(self):
        """ Richiedo un pezzo completamente nuovo, cioè non già in self.progresses """
        
        if len(self.am_interested_in) == 0:
            self.logger.debug("Nothing to be interested in")
            self.logger.debug("Sending NOT-INTERESTED message")
            if self.am_interested:
                self.am_interested = False
                self.send_message(MexType.NOT_INTERESTED)
            return
        
        if self.peer_chocking:
            self.logger.debug("Wanted to ask a new piece, but am choked")
            return

        not_yet_started = set(self.am_interested_in) - set(self.my_progresses.keys())

        # Se tutti i pieces sono già stati avviati o completati
        if len(not_yet_started) == 0:
            self.logger.debug("No NEW pieces are requestable; abort")
            return

        # Se sto già scaricando il numero max di pieces contemporaneamente
        if len(self.my_progresses) >= self.max_concurrent_pieces:
            self.logger.debug("Already downloading at the fullest")
            return
                
        random_piece = random.choices(
            list(not_yet_started), #non si può fare random choice su set()
            k=min(self.max_concurrent_pieces - len(self.my_progresses),
                  len(not_yet_started))
        )

        for piece in random_piece:
            self.ask_for_single_piece(piece)

    
    def try_ask_for_piece(self, suggestion=None):
        """ Differisce da ask_for_new_piece: mentre l'altro chiede un pezzo
        mai scaricato prima, questo potrebbe anche riprendere il download
        di un pezzo già iniziato. """
        if self.peer_chocking:
            self.logger.debug("Wanted to request a piece, but am choked")
            return
        
        if len(self.my_progresses) == 0: # se non ci sono pezzi incompleti
            return self.ask_for_new_pieces()

        if len(self.my_progresses) >= self.max_concurrent_pieces:
            self.logger.debug("Already topping max concurrent requests")
            return
        
        if suggestion is not None:
            piece_idx = suggestion
        else:
            piece_idx = random.choice(list(self.my_progresses.keys()))

        (offset_start, total_len) = self.my_progresses[piece_idx]
        self.logger.debug("Will continue with piece %d from offset %d", piece_idx, offset_start)
        
        self.send_message(
            MexType.REQUEST, 
            piece_index=piece_idx,
            piece_offset=offset_start,
            piece_length=total_len - offset_start
        )
        

    def manage_received_have(self, piece_index: int):
        self.logger.debug("Acknowledging that peer has new piece %d", piece_index)
        self.peer_bitmap[piece_index] = True

        
    def manage_received_piece(self, piece_index, piece_offset, piece_payload):
        # Se è il primo frammento del pezzo XX che ricevo, crea una bytestring
        # fatta di soli caratteri NULL
        if self.my_bitmap[piece_index]:
            self.logger.warning("Received fragment of piece %d offset %d, but I have piece it already (len: %d)",
                                piece_index, piece_offset, len(piece_payload))
            assert (
                self.read_data(piece_index, piece_offset, len(piece_payload)) ==
                piece_payload
            )

            return

        num_written_bytes = self.write_data(piece_index, piece_offset, piece_payload)        
        
        self.logger.debug("Received payload for piece %d offset %d length %d: %s...%s, written %d: %s",
                          piece_index, piece_offset, len(piece_payload),
                          piece_payload[:4], piece_payload[-4:],
                          num_written_bytes,
                          self.read_data(piece_index, piece_offset, 4))
       
        # Aggiorna my_progersses
        old_partial, old_total = self.my_progresses[piece_index]
        
        if old_partial + len(piece_payload) == old_total:
            self.logger.debug("Completed download of piece %d", piece_index)
            
            del self.my_progresses[piece_index]

            if not self.verify_hash(piece_index):
                raise Exception("Hashes not matching") #TODO

            self.logger.debug("Setting my bitfield for piece %d as PRESENT", piece_index)
            self.update_my_bitmap(piece_index, True)
            self.am_interested_in.remove(piece_index)
            
            self.logger.debug("Sending HAVE for piece %d", piece_index)
            self.send_message(MexType.HAVE, piece_index=piece_index)

            # Finito un pezzo, iniziane uno NUOVO
            self.ask_for_new_pieces()
            return
                              
        self.my_progresses[piece_index] = (old_partial + len(piece_payload), old_total)
        self.try_ask_for_piece(suggestion=piece_index)


        
    def manage_request(self, p_index, p_offset, p_length):
        """ Responds to a REQUEST message from the peer. """
        if self.am_choking:
            self.logger.warning("Received REQUEST but am choking.")
            return

        self.logger.debug("Received REQUEST for piece %d offset %d length %d: will send %s...%s",
                          p_index, p_offset, p_length,
                          # TODO: bug se p_length < 4 (IRL non succederà mai)
                          self.read_data(p_index, p_offset, 4),
                          self.read_data(p_index, p_offset, 4))
        
        # self.logger.debug("Received REQUEST for piece %d starting from %d", p_index, p_offset)

        if not self.peer_interested:
            self.logger.warning("Was asked for piece %d, but to me peer is not interested", p_index)
            breakpoint()
            return
        
        if self.peer_chocking:
            self.logger.debug("Was asked for piece %d, but peer is chocking me", p_index)
            # breakpoint()
            # return

        if not self.my_bitmap[p_index]:
            self.logger.warning("Was asked for piece %d, but I don't have it", p_index)
            breakpoint()
            return

        self.send_message(
            MexType.PIECE,
            piece_index=p_index,
            piece_offset=p_offset,
            piece_length=p_length
        )

        # TODO: revisione di queste due righe
        if p_index in self.peer_progresses:
            (old_partial, old_total) = self.peer_progresses[p_index]
        else:
            (old_partial, old_total) = (0, self.get_piece_size(p_index))
            
        if old_partial + p_length < self.metainfo.block_size:
            self.peer_progresses[p_index] = (old_partial + p_length, old_total)
        else:
            if p_index in self.peer_progresses:
                del self.peer_progresses[p_index]
            else:
                self.peer_progresses[p_index] = (old_partial + p_length, old_total)


    def update_my_bitmap(self, piece_index, val: bool):
        self.my_bitmap[piece_index] = val
        with open(utils.get_bitmap_file(self.out_fpath), "w") as f:
            f.write("".join(["1" if piece else "0" for piece in self.my_bitmap]))

        if all(self.my_bitmap):
            self.logger.debug("Download completed!")
            self.completed = True
            # TODO: esci 

        
    def verify_hash(self, piece_index):
        import hashlib

        sha = hashlib.sha1()
        sha.update(self.read_data(piece_index))
        calculated_hash = sha.digest()

        are_equal = calculated_hash == self.metainfo.pieces_hash[piece_index]

        if are_equal:
            self.logger.debug("Calculated hash for piece %d matches with metainfo", piece_index)
        else:
            self.logger.warning("Hashes for piece %d DO NOT MATCH!", piece_index)
            breakpoint()

        return are_equal
            
#################################ÀÀ

# Questo oggetto gestisce le connessioni entrambi.
# Ogni nuova connessione viene assegnata ad un oggetto TorrentPeer,
# il quale si occuperà di gestire lo scambio di messaggi
class ThreadedServer:
    def __init__(self, port, metainfo, tracker_manager,
                 thread_timeout=None, thread_delay=0, **options):
        self.host = "localhost"
        self.peer = None
        self.options = options

        self.metainfo = metainfo
        self.tracker_manager = tracker_manager
        
        print("Binding della socket a", (self.host, port))
        
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, port))

        self.port = self.sock.getsockname()[1]
                
    def listen(self):
        self.sock.listen(5) # Numero massimo di connessioni in attesa (?)

        while True:
            client_socket, address = self.sock.accept()
            
            newPeer = PeerManager(
                client_socket,
                self.metainfo,
                self.tracker_manager,
                open(self.options["output_file"], "rb+"),
                Initiator.OTHER
            )

            self.peer = newPeer
            
            t = threading.Thread(target = newPeer.main)
            t.start()
            t.join(1)
            # return newPeer

            
    def connect_as_client(self, ip, port):
        new_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM) 
        new_socket.connect((ip, port))
                
        newPeer = PeerManager(
            new_socket,
            self.metainfo,
            self.tracker_manager,
            pathlib.Path(self.options["output_file"]),
            Initiator.SELF
        )

        self.peer = newPeer
        
        t = threading.Thread(target = newPeer.main)
        t.start()
        t.join(2)
        return newPeer


import sys
try:
    import Fiume.metainfo_decoder as md
except:
    import metainfo_decoder as md
import bencodepy

options = {
    # "torrent_path": "/home/groucho/Luca rantolo.mp3.torrent",
    # "output_file": "/home/groucho/torrent/asd/downloaded.mp3",
    "torrent_path": "/home/groucho/torrent/image.jpg.torrent",
    "output_file": "/home/groucho/torrent/asd/image.jpg",
}

with open(options["torrent_path"], "rb") as f:
    metainfo = md.MetaInfo(bencodepy.decode(f.read()))
            
# if __name__ == "__main__":
self_port_num = int(sys.argv[1]) if len(sys.argv) > 1 else 0

# try:
with open(options["torrent_path"], "rb") as f:
    metainfo = md.MetaInfo(bencodepy.decode(f.read()))

tm = md.TrackerManager(metainfo)

t = ThreadedServer(
    self_port_num,
    metainfo, tm,
    **options
)

peer = tm.peers[0]
print(peer)
pm = t.connect_as_client(*peer)

