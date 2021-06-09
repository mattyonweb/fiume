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
    format='%(name)s: %(message)s',
)

class MexType(enum.Enum):
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

class PeerManager:
    def __init__(self, socket, data: List[bytes], initiator: Initiator):
        self.logger = logging.getLogger("PeerManager")
        self.logger.debug("__init__")
        
        self.socket = socket
        self.my_bitmap, self.peer_bitmap = utils.data_to_bitmap(data), list()

        self.peer_chocking, self.am_choking = True, True
        self.peer_interested, self.am_interested = False, False

        self.initiator = initiator

        # breakpoint()
        
        self.queue_to_elaborate = queue.Queue()
        self.queue_to_send_out  = queue.Queue()

        self.data = data

        
    def main(self):
        self.logger.debug("main")
        
        if self.initiator == Initiator.SELF:
            self.send_handshake()
        else:
            self.receive_handshake()

        self.queue_to_send_out.put(self.make_message(MexType.BITFIELD))
        
        t1 = threading.Thread(target=self.message_receiver)
        t2 = threading.Thread(target=self.message_sender)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        
    def send_handshake(self):
        pass

    def receive_handshake(self):
        pass

    # Thread a sé stante
    def message_receiver(self):
        while True:
            raw_length = self.socket.recv(4)
            print("RAWLENGTH", raw_length)
            length = int.from_bytes(raw_length, byteorder="big", signed=False)
            raw_mex = self.socket.recv(length)

            self.message_interpreter(raw_length + raw_mex)

            
    # Thread a sé stante
    def message_sender(self):
        while True:
            mex = self.queue_to_send_out.get()
            self.socket.sendall(mex)

    #######

    def message_interpreter(self, mex: bytes):
        """ Elabora un messaggio ricevuto, decidendo come rispondere e/o
        che cosa fare. """
        
        mex_type = MexType(mex[4])

        # self.logger.debug("Received message", str(mex_type))
        self.logger.debug("Received message" + str(mex_type))
        
        if mex_type == MexType.CHOKE:
            self.peer_chocking = True
        elif mex_type == MexType.UNCHOKE:
            self.peer_chocking = False
        elif mex_type == MexType.INTERESTED:
            self.peer_interested = True
        elif mex_type == MexType.NOT_INTERESTED:
            self.peer_interested = False
        elif mex_type == MexType.HAVE:
            self.interpret_received_have(mex[5:])

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
            piece_payload = mex[15:]
            self.manage_received_piece(piece_index, piece_offset, piece_payload)

        elif mex_type == MexType.CANCEL:
            print("CANCEL not implemented")

        elif mex_type == MexType.PORT:
            print("PORT not implemented")

        else:
            print("ricevuto messaggio sconosciuto")
            breakpoint()

            
    def send_message(self, mex: bytes):
        self.queue_to_send_out.put(mex)

    def make_message(self, mexType: MexType, **kwargs) -> bytes:
        if mexType == MexType.KEEP_ALIVE:
            return bytes([0,0,0,0])
        
        if mexType.value in [0,1,2,3]:
            return (bytes([0,0,0,1]) +
                    utils.to_bytes(mexType.value, length=1))
        
        if mexType == MexType.HAVE:
            return (utils.to_bytes(5, length=4) +
                    utils.to_bytes(mexType.value) +
                    utils.to_bytes(kwargs["piece_index"]))
        
        if mexType == MexType.BITFIELD:
            bitmap = utils.bool_to_bitmap(self.my_bitmap)

            #TODO non sono sicuro len(bitmap) ritorni il risultato giusto...
            return (utils.to_bytes(1 + len(bitmap), length=4) + 
                    utils.to_bytes(mexType.value) +
                    bitmap)

        if mexType == MexType.REQUEST:
            return (utils.to_bytes(13, length=4) + 
                    utils.to_bytes(mexType.value) +
                    utils.to_bytes(kwargs["piece_index"]) +
                    utils.to_bytes(kwargs["piece_offset"]) +
                    utils.to_bytes(kwargs["piece_length"]))

        if mexType == MexType.PIECE:
            block = self.data[kwargs["piece_index"]]
            payload = block[kwargs["piece_offset"]:kwargs["piece_offset"]+kwargs["piece_length"]]

            return (utils.to_bytes(9 + len(payload), length=4) + 
                    utils.to_bytes(mexType.value) +
                    payload)

        raise Exception("Messaggio impossibile da costruire")
                    
                    
    def interpret_received_bitfield(self, mex_payload: bytes):
        self.peer_bitmap = utils.bitmap_to_bool(mex_payload)

        for i, (my, other) in enumerate(zip(self.my_bitmap, self.peer_bitmap)):
            print(f"Block {i}: ", end="")
            if my and other: print("both")
            elif my and not other: print("only me")
            elif not my and other: print("only peer")
            else: print("no-one")
            

    def interpret_received_have(self, mex_payload: bytes):
        pass

    def manage_received_piece(self, piece_index, piece_offset, piece_payload):
        pass
    
    def manage_request(self, p_index, p_offset, p_length):
        """ Responds to a REQUEST message from the peer. """
        if self.am_choking:
            self.logger.debug("Received REQUEST but am choking.")
            return

        if self.my_bitmap[p_index]:
            pass

#################################ÀÀ

# Questo oggetto gestisce le connessioni entrambi.
# Ogni nuova connessione viene assegnata ad un oggetto TorrentPeer,
# il quale si occuperà di gestire lo scambio di messaggi
class ThreadedServer(object):
    def __init__(self, port):
        self.host = "localhost"
        self.port = port

        print("Binding della socket a", (self.host, self.port))
        
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))

    def listen(self):
        self.sock.listen(5) # Numero massimo di connessioni in attesa (?)

        print("Avvio console")        
        threading.Thread(target=self.console).start()

        
        while True:
            client, address = self.sock.accept()
            print("RICEVUTA CONNESSIONE DA", address)
            newPeer = PeerManager(client, utils.mask_data(DATA, self.port), Initiator.OTHER)
            threading.Thread(target = newPeer.main).start()

    def console(self):
        print("Console avviata")

        while True:
            tokens = input(f"{self.host}:{self.port} >>> ").strip().split(" ")

            print(tokens)

            if tokens[0] == "con":
                port = int(tokens[1])

                # with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                new_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM) 
                new_socket.connect(("localhost", port))

                newPeer = PeerManager(new_socket, utils.mask_data(DATA, self.port), Initiator.SELF) 
                threading.Thread(target = newPeer.main).start()
                
                break

port_num = int(input("Port number: "))
ThreadedServer(port_num).listen()

