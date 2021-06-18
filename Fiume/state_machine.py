import socketserver
import socket
import time
import threading
import logging
import enum
import os
import random
import pathlib 

from queue import Queue
from typing.io import *
from typing import *

import Fiume.config as config
import Fiume.utils as utils


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
    def __init__(self, socket: Tuple,
                 metainfo, tracker_manager,
                 master_queues: Tuple[Queue, Queue],
                 initial_bitmap: List[bool],
                 options: Dict[str, Any],
                 initiator: Initiator):
        
        # Peer socket
        self.socket, self.address = socket
        self.peer_ip, self.peer_port = self.address
        # self.peer_ip, self.peer_port = self.socket.getsockname()
        # self.address = self.socket.getsockname() # TODO
        
        self.logger = logging.getLogger("TO " + str(self.peer_ip) + ":" + str(self.peer_port))
        self.logger.debug("__init__")
                
        self.metainfo = metainfo
        self.tracker_manager = tracker_manager

        self.options = options
        self.debug = False
        if "debug" in self.options:
            self.debug = self.options["debug"]
        
        # Ok
        self.peer_chocking, self.am_choking = True, True
        self.peer_interested, self.am_interested = False, False

        # Needed for establishing who starts the handshake
        self.initiator: Initiator = initiator
        self.received_handshake, self.sent_handshake = False, False

        # Queues for inter-thread communication
        self.queue_in, self.queue_to_master = master_queues

        # Bitmaps of my/other pieces
        self.my_bitmap = initial_bitmap
        self.peer_bitmap: List[bool] = utils.empty_bitmap(self.metainfo.num_pieces)

        # Output file
        self.out_fpath: pathlib.Path = self.initialize_file(self.options["output_file"])

        # Blocks that I don't have but my peer has
        self.am_interested_in: List[int] = list()
        self.peer_interested_in: List[int] = list()

        self.pending: List[int] = list()
        
        self.my_progresses: Dict[int, Tuple[int, int]] = dict()
        self.peer_progresses: Dict[int, Tuple[int, int]] = dict()

        self.max_concurrent_pieces = 4
        
        self.old_messages: List[Tuple[str, bytes]] = list()
        self.completed = False

        
    def main(self):
        self.logger.debug("main")

        t1 = threading.Thread(target=self.message_socket_receiver)
        t1.start()
        
        # Stabilisce chi fra i due peers dovrà inviare il primo messaggio
        if self.initiator == Initiator.SELF:
            self.send_handshake()

        self.message_interpreter()

        
    def send_to_master(self, mex: utils.MasterMex):
        """
        Sends a message to the master's queue.
        """
        self.queue_to_master.put(mex)

        
    def read_data(self, piece_index, piece_offset=0, piece_length=0) -> bytes:
        """
        Reads data at a given offset from the downloaded file. Used when peer
        asks me for a piece.
        """
        if piece_length == 0:
            piece_length = self.get_piece_size(piece_index)

        with open(self.out_fpath, "rb") as file:
            file.seek(self.metainfo.piece_size * piece_index + piece_offset, 0)
            data = file.read(piece_length)

        return data

    
    def write_data(self, piece_index, piece_offset, payload):
        """
        Writes data at a given offset on the downloaded file.
        """
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
        # Handshake ricevuta è corretta (info_hash matchano)
        assert mex[28:48] == self.metainfo.info_hash
        
        self.received_handshake = True
        self.logger.debug("Received HANDSHAKE")
        
        if self.sent_handshake:
            self.logger.debug("Sending BITFIELD")
            self.send_message(MexType.BITFIELD)
        else:
            self.send_handshake()        


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
    def message_socket_receiver(self):
        handshake_mex = self.socket.recv(68)

        self.queue_in.put(handshake_mex)
        
        while True:
            raw_length = self.socket.recv(4)
            length = int.from_bytes(raw_length, byteorder="big", signed=False)

            if length == 0:
                self.queue_in.put(b"")
                break
            
            raw_mex = bytes()
            while length != 0:
                data = self.socket.recv(length)
                raw_mex += data
                length -= len(data)
                if length < 0:
                    breakpoint()
                    _ = 0
                if length != 0 and self.debug:
                    print(f"Still waiting for {length} bytes...")

            self.queue_in.put(raw_length + raw_mex)

    
    def shutdown(self):
        self.logger.debug("Shutdown after receiving empty message from peer")
        self.tracker_manager.notify_completion()
        exit(0)

    
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

    def message_interpreter(self):
        """ Elabora un messaggio ricevuto, decidendo come rispondere e/o
        che cosa fare. """

        while True:
            mex = self.queue_in.get()

            # Messagi di controllo (ie. da Master) vengono intoltrati a questa funzione
            if isinstance(mex, utils.MasterMex):
                self.control_message_interpreter(mex)
                continue

            # TODO: messaggio vuoto b"" = fine scambio o errore di rete
            if mex == b"":
                print("Shutdown")
                self.shutdown()
                break
            
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

            
    def control_message_interpreter(self, mex: utils.MasterMex):
        if isinstance(mex, utils.M_KILL):
            self.logger.debug("Received KILL from master")
            self.send_to_master(utils.M_DEBUG("Got KILLED"))
            pass
        
        elif isinstance(mex, utils.M_DEBUG):
            self.logger.debug("Received DEBUG message from master: %s", mex.data)
            self.send_to_master(utils.M_DEBUG("Got DEBUGGED"))

        elif isinstance(mex, utils.M_SCHEDULE):
            self.pending += mex.pieces_index
            self.ask_for_new_pieces() # TODO?
            
        
        
    def send_message(self, mexType: MexType, **kwargs):
        if "delay" in self.options:
            time.sleep(self.options["delay"])
        self.socket.sendall(self.make_message(mexType, **kwargs))

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

        if mex is None:
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
                
        self.send_to_master(utils.M_PEER_HAS(self.am_interested_in, self.address))
        
        # Se, dal confronto fra la mia e l'altrui bitmap, scopro
        # che non mi interessa nulla di ciò che ha il peer, informalo che
        # sei NOT_INTERESTED
        if len(self.am_interested_in) == 0:
            self.logger.debug("Nothing to be interested in")
            if self.am_interested:
                self.am_interested = False
                self.send_message(MexType.NOT_INTERESTED)
            return
        
        if self.am_interested: # Se già mi interessava qualcosa, non fare nulla
            return

        # Altrimenti dichiara il tuo interesse
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
                piece_length=piece_length
            )
        except Exception as e:
            breakpoint()
            raise e

        # Inform the master that I have requested a new piece"
        self.send_to_master(
            utils.M_DEBUG("Requested new piece {piece_idx}", (self.peer_ip, self.peer_port))
        )
        
        # self.get_piece_size serve per gestire len irregolare dell'ultimo piece
        self.my_progresses[piece_idx] = (b"", self.get_piece_size(piece_idx))

        
        
    def ask_for_new_pieces(self):
        """ 
        Richiedo un pezzo completamente nuovo, cioè non già in self.progresses
        """

        if len(self.am_interested_in) == 0 or len(self.pending) == 0:
            if len(self.pending) == 0:
                self.logger.debug("No pieces pending!")
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
        # breakpoint()
        not_yet_started = not_yet_started.intersection(set(self.pending))
        
        # Se tutti i pieces sono già stati avviati o completati
        if len(not_yet_started) == 0:
            self.logger.debug("No NEW pieces are requestable; abort")
            return

        # Se sto già scaricando il numero max di pieces contemporaneamente
        if len(self.my_progresses) >= self.max_concurrent_pieces:
            self.logger.debug("Already downloading at the fullest")
            return
                
        random_piece = random.sample(
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

        (data_already_downloaded, total_len) = self.my_progresses[piece_idx]
        offset_start = len(data_already_downloaded)
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
        # TODO informa master

        
    def manage_received_piece(self, piece_index, piece_offset, piece_payload):
        if self.my_bitmap[piece_index]:
            self.logger.warning(
                "Received fragment of piece %d offset %d, but I have piece it already (len: %d)",
                piece_index, piece_offset, len(piece_payload)
            )
            assert (
                self.read_data(piece_index, piece_offset, len(piece_payload)) ==
                piece_payload
            )
            return

        
        # self.write_data(piece_index, piece_offset, piece_payload)        
        
        self.logger.debug("Received payload for piece %d offset %d length %d: %s...%s",
                          piece_index, piece_offset, len(piece_payload),
                          piece_payload[:4], piece_payload[-4:])
       
        # Aggiorna my_progersses
        old_data, piece_size = self.my_progresses[piece_index]
        new_data = old_data + piece_payload
        
        if len(new_data) == piece_size:
            self.logger.debug("Completed download of piece %d", piece_index)
            
            del self.my_progresses[piece_index]

            if not self.verify_hash(piece_index):
                raise Exception("Hashes not matching") #TODO

            self.logger.debug("Setting my bitfield for piece %d as PRESENT", piece_index)
            self.update_my_bitmap(piece_index, True)
            self.am_interested_in.remove(piece_index)

            self.pending.remove(piece_index)
            self.send_to_master(utils.M_PIECE(piece_index, new_data, self.address))
            
            self.logger.debug("Sending HAVE for piece %d", piece_index)
            self.send_message(MexType.HAVE, piece_index=piece_index)

            # Finito un pezzo, iniziane uno NUOVO
            self.ask_for_new_pieces()
            return
                              
        self.my_progresses[piece_index] = (new_data, piece_size)
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
        return True # BUG # TODO
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

class ConnectionStatus:
    def __init__(self, address, queue_to):
        self.address = address
        self.peer_has = list()
        self.assigned = list()
        self.queue = queue_to
        
# Questo oggetto gestisce le connessioni entrambi.
# Ogni nuova connessione viene assegnata ad un oggetto TorrentPeer,
# il quale si occuperà di gestire lo scambio di messaggi
class ThreadedServer:
    def __init__(self, port, metainfo, tracker_manager, **options):
        self.host = "localhost"
        self.peer = None
        self.options = options

        self.logger = logging.getLogger("ThreadedServer")
        self.logger.debug("__init__")
        
        self.metainfo = metainfo
        self.tracker_manager = tracker_manager

        self.logger.debug("Server is binding at %s", (self.host, port))
        
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, port))

        self.port = self.sock.getsockname()[1]
        self.logger.debug("Self port: %d", self.port)

        self.options = options
        self.peers = self.tracker_manager.notify_start()

        # La bitmap iniziale, quando il programma viene avviato.
        # Viene letta da un file salvato in sessioni precedenti, oppure
        # creata ad hoc.
        self.global_bitmap: List[bool] = utils.data_to_bitmap(
            self.options["output_file"],
            num_pieces=self.metainfo.num_pieces
        )
        
        self.max_peer_connections = 1
        self.active_connections = dict()
        self.queue_master_in = Queue()

        
    def main(self):        
        master_listen_t = threading.Thread(target=self.master_queue_receiver)
        master_listen_t.start()
        
        socket_listen_t = threading.Thread(target=self.listen)
        socket_listen_t.start()        

        i = 0
        while i < self.max_peer_connections:
            ip, port = random.choice(self.peers)

            if (ip, port) in self.active_connections:
                self.logger.warning("Chosen an already connected peer")
                time.sleep(1)
                continue
            
            if ip == self.host or port == self.port: #TODO: sbagliato, peer può usare mia stessa porta
                self.logger.debug("Attempting to connect to myself (%s): abort", (ip, port))
                time.sleep(2)
                continue

            try:
                q_to = Queue()

                connection = ConnectionStatus((ip, port), q_to)
                self.active_connections[(ip, port)] = connection
                self.connect_as_client(ip, port, (q_to, self.queue_master_in))
                i += 1
                
            except Exception as e:
                self.logger.debug("%s", e)
                time.sleep(2)
                continue

        
    def master_queue_receiver(self):
        """ 
        Infinite loop for handling messages coming from PeerManagers.
        """
        
        while True:
            mex = self.queue_master_in.get()

            if isinstance(mex, utils.M_DEBUG):
                self.logger.debug("%s", mex.data)

            elif isinstance(mex, utils.M_PEER_HAS):
                state = self.active_connections[mex.sender]

                # Aggiorna stato connessione, il peer ha un nuovo pezzo
                state.peer_has += mex.pieces_index

                schedule_pieces = self.assign_pieces(mex.sender)

                state.queue.put(utils.M_SCHEDULE(schedule_pieces))

            elif isinstance(mex, utils.M_PIECE):
                state = self.active_connections[mex.sender]
                state.peer_has.remove(mex.piece_index)
                
            else:
                self.logger.warning("Message not implemented: %s", mex)

                
    def assign_pieces(self, address:Tuple[str, int]):
        """ 
        Choose which pieces to assign a peer. The peer will receive these pieces,
        and proceeds to download them.
        """
        
        # queue_out  = self.active_connections[address]
        # assignable = set(range(self.metainfo.num_pieces)) - set(self.assigned_pieces[address])
        # to_assign  = random.sample(list(assignable),
        #                            k=min(10, len(assignable)))
        # queue_out.put(utils.M_SCHEDULE(to_assign))

        # Ritorna tutti i pezzi che il peer ha ma noi no
        return self.active_connections[address].peer_has
        
    ##############################
    
    def listen(self):
        self.logger.debug("Started listening on %s", (self.host, self.port))
        self.logger.debug("Max connections number: %d", self.max_peer_connections)
        
        self.sock.listen(self.max_peer_connections) # Numero massimo di connessioni in attesa (?)

        while True:
            self.logger.debug("Waiting for connections...")
            client_socket, address = self.sock.accept()
            self.logger.debug("Received connection request from: %s", address)
            
            newPeer = PeerManager(
                (client_socket, address),
                self.metainfo,
                self.tracker_manager,
                (None, None), #TODO queues
                self.global_bitmap,
                self.options,
                Initiator.OTHER
            )

            self.peer = newPeer
            
            t = threading.Thread(target = newPeer.main)
            t.start()

            
    def connect_as_client(self, ip, port, queues: Tuple[Queue]):
        new_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM) 
        new_socket.connect((ip, port))

        self.logger.debug("Actively connecting to %s", (ip, port))
        
        newPeer = PeerManager(
            (new_socket, (ip, port)),
            self.metainfo,
            self.tracker_manager,
            queues,
            self.global_bitmap,
            self.options,
            Initiator.SELF
        )

        t = threading.Thread(target = newPeer.main)
        t.start()            
        return newPeer

###############################

import Fiume.metainfo_decoder as md
import bencodepy

options = {
    "torrent_path": pathlib.Path("/home/groucho/torrent/image.jpg.torrent"),
    "output_file":  pathlib.Path("/home/groucho/torrent/downloads/image.jpg"),
    "delay": 0,
    "debug": False,
}

with open(options["torrent_path"], "rb") as f:
    metainfo = md.MetaInfo(bencodepy.decode(f.read()))

tm = md.TrackerManager(metainfo, options)

t = ThreadedServer(
    50146,
    metainfo, tm,
    **options
)

t.main()
# th = threading.Thread(target=t.listen)
# th.start()

# peer = ("78.14.24.41", 50144)
# pm = t.connect_as_client(*peer, (Queue(), Queue())

# try:
#     peer = tm.peers[0]
#     print(peer)
#     pm = t.connect_as_client(*peer)
# except Exception as e:
#     print(e)
#     breakpoint()
#     raise(e)
