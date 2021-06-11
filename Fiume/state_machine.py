import socketserver
import socket
import time
import threading
import logging
import enum
import queue
import os
import random
from typing import *

try:
    import Fiume.utils as utils
except:
    import utils as utils

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

    
random.seed(0)
BLOCK_SIZE = 256
DATA = utils.generate_random_data(total_length=2048, block_size=BLOCK_SIZE)

#############################################


class PeerManager:
    def __init__(self, socket,
                 metainfo, tracker_manager,
                 file: BufferedReader,
                 initiator: Initiator,
                 delayed=True, timeout=None,
                 data:List[bytes] = None):
        
        # Peer socket
        self.socket = socket
        self.peer_ip, self.peer_port = self.socket.getsockname()
        
        self.logger = logging.getLogger("TO " + str(self.peer_ip) + ":" + str(self.peer_port))
        self.logger.debug("__init__")
        self.delayed = delayed # aggiunge una sleep a sendmessage()
        self.timeout = timeout
            
        # Bitmaps of my/other pieces
        self.my_bitmap:   List[bool] = utils.data_to_bitmap(data)
        self.peer_bitmap: List[bool] = list()

        self.metainfo = metainfo
        self.tracker_manager = tracker_manager
        self.info_hash = info_hash #bytes(20)

        # Ok
        self.peer_chocking, self.am_choking = True, True
        self.peer_interested, self.am_interested = False, False

        # Needed for establishing who starts the handshake
        self.initiator: Initiator = initiator
        self.received_handshake, self.sent_handshake = False, False

        # Queues for inter-thread communication
        self.queue_to_elaborate = queue.Queue()
        self.queue_to_send_out  = queue.Queue()

        # Blocks that i posses
        self.data: List[bytes] = data
        self.file: BufferedReader = file
        
        # Blocks that I don't have but my peer has
        self.am_interested_in: List[int] = list()
        self.peer_interested_in: List[int] = list()
        
        self.my_progresses: Dict[int, Tuple[int, int]] = dict()
        self.peer_progresses: Dict[int, Tuple[int, int]] = dict()

        
    def main(self):
        self.logger.debug("main")

        # Stabilisce chi fra i due peers dovrà inviare il primo messaggio
        if self.initiator == Initiator.SELF:
            self.send_handshake()

        t1 = threading.Thread(target=self.message_receiver)
        t2 = threading.Thread(target=self.message_sender)
        t1.start()
        t2.start()
            
        t1.join(self.timeout)
        t2.join(self.timeout)


    def read_data(self, piece_index, piece_offset=0, piece_length=0) -> bytes:
        if piece_length == 0:
            piece_length = self.metainfo.piece_size

        if self.data:
            return self.data[piece_index][piece_offset:piece_offset+piece_length]

        f.seek(self.metainfo.piece_size * piece_index + piece_offset, 0)
        data = f.read(piece_length)
        f.seek(0,0)
        return data

    def write_data(self, piece_index, piece_offset, payload):
        if self.data:
            s = self.data[piece_idx]
            self.data[piece_idx] = s[:piece_offset] + payload + s[piece_offset+len(piece_payload):]
            return

        f.seek(self.metainfo.piece_size * piece_index + piece_offset, 0)
        f.write(payload)
        return
    
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
        return True # TODO

    
    # Thread a sé stante
    def message_receiver(self):
        handshake_mex = self.socket.recv(68)
        self.message_interpreter(handshake_mex)
        
        while True:
            raw_length = self.socket.recv(4)
            length = int.from_bytes(raw_length, byteorder="big", signed=False)
            raw_mex = self.socket.recv(length)

            self.message_interpreter(raw_length + raw_mex)

            
    # Thread a sé stante
    def message_sender(self):
        while True:
            mex = self.queue_to_send_out.get()
            if self.delayed:
                time.sleep((random.random() / 12) + self.delayed)
            self.socket.sendall(mex)


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

        
        mex_type = MexType(mex[4])

        self.logger.debug("Received message %s", str(mex_type))

        if mex_type == MexType.HANDSHAKE:
            self.receive_handshake(mex)
            
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
        if mexType == MexType.KEEP_ALIVE:
            return bytes([0,0,0,0])

        if mexType == MexType.HANDSHAKE:
            return (utils.to_bytes(19) +
                    b"BitTorrent protocol" +
                    bytes(8) +
                    self.metainfo.info_hash +
                    utils.generate_peer_id(seed=self.peer_port))
        
        if mexType.value in [0,1,2,3]:
            return (bytes([0,0,0,1]) +
                    utils.to_bytes(mexType.value, length=1))
        
        if mexType == MexType.HAVE:
            return (utils.to_bytes(5, length=4) +
                    utils.to_bytes(mexType.value) +
                    utils.to_bytes(kwargs["piece_index"], length=4))
        
        if mexType == MexType.BITFIELD:
            bitmap = utils.bool_to_bitmap(self.my_bitmap)

            #TODO non sono sicuro len(bitmap) ritorni il risultato giusto...
            return (utils.to_bytes(1 + len(bitmap), length=4) + 
                    utils.to_bytes(mexType.value) +
                    bitmap)

        if mexType == MexType.REQUEST:
            return (utils.to_bytes(13, length=4) + 
                    utils.to_bytes(mexType.value) +
                    utils.to_bytes(kwargs["piece_index"], length=4) +
                    utils.to_bytes(kwargs["piece_offset"], length=4) +
                    utils.to_bytes(kwargs["piece_length"], length=4))

        if mexType == MexType.PIECE:
            # block = self.data[kwargs["piece_index"]]
            # payload = block[kwargs["piece_offset"]:kwargs["piece_offset"]+kwargs["piece_length"]]
            payload = self.read_data(
                kwargs["piece_index"],
                kwargs["piece_offset"],
                kwargs["piece_length"]
            )
            
            return (utils.to_bytes(9 + len(payload), length=4) + 
                    utils.to_bytes(mexType.value) +
                    utils.to_bytes(kwargs["piece_index"], length=4) +
                    utils.to_bytes(kwargs["piece_offset"], length=4) +
                    payload)

        raise Exception("Messaggio impossibile da costruire")
                    
                    
    def interpret_received_bitfield(self, mex_payload: bytes):
        """ Analyzes a received bitmap """
        self.peer_bitmap = utils.bitmap_to_bool(mex_payload)

        # Stampo a video grafichino dei pezzi 
        print("my:   |", end="")
        for my in self.my_bitmap:
            print("x" if my else " ", end="")
        print("\npeer: |", end="")
        for peer in self.peer_bitmap:
            print("x" if peer else " ", end="")
        print()

        
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
            self.send_message(MexType.INTERESTED)
            self.ask_for_new_piece()
            return
            
    def ask_for_new_piece(self):
        """ Richiedo un pezzo completamente nuovo, cioè non già in self.progresses """
        
        if len(self.am_interested_in) == 0:
            self.logger.debug("Nothing to be interested in")
            if self.am_interested:
                self.am_interested = False
                self.send_message(MexType.NOT_INTERESTED)
            return
        
        if self.peer_chocking:
            self.logger.debug("Wanted to ask a new piece, but am choked")
            return

        not_yet_started = set(self.am_interested_in) - set(self.my_progresses.keys())
        if len(not_yet_started) == 0:
            self.logger.debug("No NEW pieces are richideibili")
            return
        
        random_piece = random.choice(list(not_yet_started)) #non si può fare random choice su set()
        
        self.logger.debug("Asking for new piece, number %d", random_piece)
        
        self.send_message(
            MexType.REQUEST,
            piece_index=random_piece,
            piece_offset=0,
            piece_length=min(BLOCK_SIZE,
                             random.randint(1, 2*BLOCK_SIZE)) #TODO: in produzione, sarà 2**14
        )

        self.my_progresses[random_piece] = (0, BLOCK_SIZE) #TODO: in produzione, sarà 2**14 (?)

    def try_ask_for_piece(self, suggestion=None):
        """ Differisce da ask_for_new_piece: mentre l'altro chiede un pezzo
        mai scaricato prima, questo potrebbe anche riprendere il download
        di un pezzo già iniziato. """
        if self.peer_chocking:
            self.logger.debug("Wanted to request a piece, but am choked")
            return
        
        if len(self.my_progresses) == 0: # se non ci sono pezzi incompleti
            return self.ask_for_new_piece()

        if suggestion is not None:
            piece_idx = suggestion
        else:
            piece_idx = random.choice(self.my_progresses.keys())

        (offset_start, total_len) = self.my_progresses[piece_idx]
        self.logger.debug("Will resume piece %d from offset %d", piece_idx, offset_start)
        
        self.send_message(
            MexType.REQUEST, 
            piece_index=piece_idx,
            piece_offset=offset_start,
            piece_length=min(total_len - offset_start,
                             random.randint(1, 2*(total_len - offset_start))) # TODO
        )

    def manage_received_have(self, piece_index: int):
        self.logger.debug("Acknowledging that peer has new piece %d", piece_index)
        self.peer_bitmap[piece_index] = True

        
    def manage_received_piece(self, piece_index, piece_offset, piece_payload):
        # Se è il primo frammento del pezzo XX che ricevo, crea una bytestring
        # fatta di soli caratteri NULL
        if self.my_bitmap[piece_index]:
            self.logger.warning("Received fragment of piece %d, but I have piece %d already",
                                piece_index, piece_index)
            breakpoint()
            return

        # Non più usato self.data
        # if self.data[piece_index] == b"":
        #     self.data[piece_index] = bytes(BLOCK_SIZE) #TODO dipendenza sbagliata da blocksize

        # Inserisco payload nella giusta posizione, all'interno della bytestring
        # s = self.data[piece_index]
        # s = s[:piece_offset] + piece_payload + s[piece_offset+len(piece_payload):]
        self.write_data(piece_index, piece_offset, piece_payload)
        
        # if not (len(s) == len(self.data[piece_index])):
        #     breakpoint()
        #     raise Exception("Lunghezze non coincidono")
                

        # self.data[piece_index] = s
        
        self.logger.debug("Received payload for piece %d offset %d length %d: %s...%s",
                          piece_index, piece_offset, len(piece_payload),
                          piece_payload[piece_offset:piece_offset+4],
                          piece_payload[piece_offset+len(piece_payload)-4:piece_offset+len(piece_payload)])

        # Aggiorna my_progersses
        old_partial, old_total = self.my_progresses[piece_index]
        if old_partial + len(piece_payload) == old_total:
            self.logger.debug("Completed download of piece %d", piece_index)
            
            del self.my_progresses[piece_index]
            self.verify_hash(piece_index)

            self.logger.debug("Setting my bitfield for piece %d as PRESENT", piece_index)
            self.my_bitmap[piece_index] = True
            self.am_interested_in.remove(piece_index)
            
            self.logger.debug("Sending HAVE for piece %d", piece_index)
            self.send_message(MexType.HAVE, piece_index=piece_index)

            self.try_ask_for_piece()
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
                          # TODO: bug se p_length < 4
                          self.data[p_index][p_offset:p_offset+4],
                          self.data[p_index][p_offset+p_length-4:p_offset+p_length])
        
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
            (old_partial, old_total) = (0, BLOCK_SIZE)
            
        if old_partial + p_length < BLOCK_SIZE:
            self.peer_progresses[p_index] = (old_partial + p_length, old_total)
        else:
            if p_index in self.peer_progresses:
                del self.peer_progresses[p_index]
            else:
                self.peer_progresses[p_index] = (old_partial + p_length, old_total)
        
    def verify_hash(self, piece_index):
        return True
            
#################################ÀÀ

# Questo oggetto gestisce le connessioni entrambi.
# Ogni nuova connessione viene assegnata ad un oggetto TorrentPeer,
# il quale si occuperà di gestire lo scambio di messaggi
class ThreadedServer:
    def __init__(self, port, thread_timeout=None, thread_delay=0):
        self.host = "localhost"
        self.timeout = thread_timeout
        self.peer = None
        self.delay = thread_delay
        
        print("Binding della socket a", (self.host, port))
        
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, port))

        self.port = self.sock.getsockname()[1]
                
    def listen(self, console=True):
        self.sock.listen(5) # Numero massimo di connessioni in attesa (?)

        if console:
            print("Avvio console")        
            console_thread = threading.Thread(target=self.console)
            console_thread.start()
        
        while True:
            client, address = self.sock.accept()
            
            newPeer = PeerManager(
                client,
                utils.mask_data(DATA, self.port),
                bytes(20),
                Initiator.OTHER,
                delayed=self.delay,
                timeout=self.timeout
            )

            self.peer = newPeer
            
            t = threading.Thread(target = newPeer.main)
            t.start()
            t.join(1)
            return newPeer

            
    def connect_as_client(self, port):
        new_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM) 
        new_socket.connect(("localhost", port))

        newPeer = PeerManager(
            new_socket,
            utils.mask_data(DATA, self.port),
            bytes(20), #infohash
            Initiator.SELF,
            delayed=self.delay,
            timeout=self.timeout
        ) 

        self.peer = newPeer
        
        t = threading.Thread(target = newPeer.main)
        t.start()
        t.join(2)
        return newPeer

        
    def console(self):
        print("Console avviata")
        threads = list()
        
        while True:
            tokens = input(f"{self.host}:{self.port} >>> ").strip().split(" ")

            print(tokens)

            if tokens[0] == "con":
                port = int(tokens[1])
                t = self.connect_as_client(port)
                threads.append(t)

            elif tokens[0] == "deb":
                breakpoint()

            elif tokens[0].strip() == "q":
               break


import sys
import Fiume.metainfo_decoder as md

if __name__ == "__main__":
    self_port_num = int(sys.argv[1]) if len(sys.argv) > 1 else 0

    t = ThreadedServer(self_port_num, thread_timeout=3, thread_delay=0.5)
    t.listen()
