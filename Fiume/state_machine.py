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
import utils

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

class PeerManager(socketserver.StreamRequestHandler):
    def __init__(self, peer, data: List[bytes], initiator: Initiator):
        self.logger = logging.getLogger("PeerManager")
        self.logger.debug("__init__")
        
        self.peer = peer
        self.my_bitmap, self.peer_bitmap = utils.data_to_bitmap(data), list()

        self.peer_chocking, self.am_choking = True, True
        self.peer_interested, self.am_interested = False, False

        self.initiator = initiator

        self.queue_to_elaborate = queue.Queue()
        self.queue_to_send_out  = queue.Queue()

        self.data = data
        
    def handle(self):
        self.logger.debug("handle")
        
        if self.initiator == Initiator.SELF:
            self.send_handshake()
        else:
            self.receive_handshake()

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

    def message_interpreter(self, mex: bytes):
        """ Elabora un messaggio ricevuto, decidendo come rispondere e/o
        che cosa fare. """
        
        mex_type = MexType(mex[5])
            
        if mex_type == MexType.CHOKE:
            self.peer_chocking = True
        elif mex_type == MexType.UNCHOKE:
            self.peer_chocking = False
        elif mex_type == MexType.INTERESTED:
            self.peer_interested = True
        elif mex_type == MexType.NOT_INTERESTED:
            self.peer_interested = False
        elif mex_type == MexType.HAVE:
            self.interpret_received_have(mex[6:])

        elif mex_type == MexType.BITFIELD:
            self.interpret_received_bitfield(mex[6:])

        elif mex_type == MexType.REQUEST:
            piece_index  = utils.to_int(mex[6:10]) 
            piece_offset = utils.to_int(mex[10:14]) 
            piece_length = utils.to_int(mex[14:18]) 
            self.manage_request(piece_index, piece_offset, piece_length)

        elif mex_type == MexType.PIECE:
            piece_index  = utils.to_int(mex[6:10]) 
            piece_offset = utils.to_int(mex[10:14])
            piece_payload = mex[15:]
            self.manage_received_piece(piece_index, piece_offset, piece_payload)

        elif mex_type == MexType.CANCEL:
            print("CANCEL not implemented")

        elif mex_type == MexType.PORT:
            print("PORT not implemented")

        else:
            print("ricevuto messaggio sconosciuto")
            breakpoint()


    # Thread a sé stante
    def message_receiver(self):
        while True:
            mex = self.rfile.readline().strip() #TODO: strip qui va bene?

            print(mex)
            
            self.message_interpreter(mex)
    
    # Thread a sé stante
    def message_sender(self):
        while True:
            mex = self.queue_to_send_out.get()
            self.wfile.write(mex + "\n") #TODO. Serve l'a capo?

    #######
    
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
        pass

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

    
class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    pass

#####################################ÀÀ
    

class MyTCPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        # self.rfile is a file-like object created by the handler;
        # we can now use e.g. readline() instead of raw recv() calls

        print("INIZIO HANDLE")
        time.sleep(10)
        
        self.data = self.rfile.readline().strip()
        
        print("{} wrote:".format(self.client_address[0]))
        print(self.data)

        # Likewise, self.wfile is a file-like object used to write back
        # to the client
        self.wfile.write(self.data.upper())


# if __name__ == "__main__":
#     HOST, PORT = "localhost", 1111

#     # Create the server, binding to localhost on port 9999
#     socketserver.TCPServer.allow_reuse_address = True
    
#     with socketserver.TCPServer((HOST, PORT), MyTCPHandler) as server:
#         # Activate the server; this will keep running until you
#         # interrupt the program with Ctrl-C
#         server.serve_forever()

        
