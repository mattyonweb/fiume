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
import Fiume.master as master

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
        self.logger.setLevel(options.get("debug_level", logging.DEBUG))
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

        self.scheduled: List[int] = list()
        
        self.my_progresses: Dict[int, Tuple[bytes, int]] = dict()
        self.peer_progresses: Dict[int, Tuple[int, int]] = dict()

        self.cache_pieces: Dict[int, bytes] = dict()
        self.deferred_peer_requests: Dict[int, Tuple[int, int]] = dict()
        
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
        Reads data at a given offset from the cache. Used when peer
        asks me for a piece.

        If piece is not in cache, raise an exception (it should never happen! 
        every time you call read_data, data must be entered in cache!)
        """
        if piece_length == 0:
            piece_length = self.get_piece_size(piece_index)

        if piece_index not in self.cache_pieces:
            breakpoint()
            raise Exception(f"Don't have piece {piece_index} in cache!")

        piece = self.cache_pieces[piece_index]
        return piece[piece_offset:piece_offset+piece_length]

    
        
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
        self.send_to_master(utils.M_DISCONNECTED(self.address))
        # self.tracker_manager.notify_completion()
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
                if self.am_interested:
                    # TODO: ricevuto unchoke, questa istruzione che segue
                    # richiede UN solo pezzo, invece che max_concurrent_pieces
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
        self.logger.debug("[MASTER] Received message %s", str(type(mex)))
        
        if isinstance(mex, utils.M_KILL):
            self.logger.debug("[MASTER] Received KILL from master")
            self.send_to_master(utils.M_DEBUG("Got KILLED", self.address))
            return # TODO
        
        elif isinstance(mex, utils.M_DEBUG):
            self.logger.debug("[MASTER] Received DEBUG message from master: %s", mex.data)
            self.send_to_master(utils.M_DEBUG("Got DEBUGGED", self.address))
            return
        
        elif isinstance(mex, utils.M_OUR_BITMAP):
            self.logger.debug("[MASTER] Received OUR_BITMAP message from master")
            self.my_bitmap = mex.bitmap
            return
            
        elif isinstance(mex, utils.M_SCHEDULE):
            self.logger.debug("[MASTER] Received SCHEDULE message from master: %s", mex.pieces_index)
            self.scheduled += mex.pieces_index
            # Avoids deadlock:
            # if no pieces are currently downloaded, try_ask_for_piece will
            # start downloading the received piece(s);
            # if we were downloading pieces already, simply start one of the
            # new pieces (if we are not > max_concurrent_pieces
            self.ask_for_new_pieces()
            return

        
        elif isinstance(mex, utils.M_NEW_HAVE):
            self.logger.debug("[MASTER] Received NEW_HAVE message from master: %s", mex.piece_index)
            self.my_bitmap[mex.piece_index] = True
            return        

        # M_Piece has a bit complicated workflow.
        # If we receive a M_PIECE, it means that in the past we requested to the
        # master, on behalf of the peer, a piece N.
        # We put the entire piece N in a cache.
        # Then we resume the deferred request, 
        elif isinstance(mex, utils.M_PIECE):
            self.logger.debug("Received piece %d from master", mex.piece_index)
            
            self.cache_pieces[mex.piece_index] = mex.data

            if mex.piece_index not in self.deferred_peer_requests:
                # Should not happend
                self.logger.warning(
                    "Received piece %d from master, but we never deferred its sending!",
                    mex.piece_index
                )
                return

            # Resume request worfflow
            return self.manage_request(
                mex.piece_index,
                *self.deferred_peer_requests[mex.piece_index],
                deferred=True #important!
            )
                
        else:
            breakpoint()
            raise Exception("unknown message")
        
        
    def send_message(self, mexType: MexType, **kwargs):
        """
        Sends (via socket) a message to the peer.
        """
        if "delay" in self.options:
            time.sleep(self.options["delay"])
        self.socket.sendall(self.make_message(mexType, **kwargs))

        
    def make_message(self, mexType: MexType, **kwargs) -> bytes:
        """
        Builds a peer message of a given type (without sending it!)
        """
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

        return mex
                    

    def interpret_received_bitfield(self, mex_payload: bytes):
        """ 
        Analyzes and reacts to a received bitmap.
        """

        # Sets peer_bitmap according to the mex received
        self.peer_bitmap = utils.bitmap_to_bool(
            mex_payload,
            num_pieces=self.metainfo.num_pieces
        )

        if len(self.my_bitmap) < 80:
            # Pretty view of my/peer bitmaps
            # TODO: rendilo funzione a se stante
            print("my:   |", end="")
            for my in self.my_bitmap:
                print("x" if my else " ", end="")
            print("\npeer: |", end="")
            for peer in self.peer_bitmap:
                print("x" if peer else " ", end="")
            print()

        # Sanity check
        assert len(self.my_bitmap) == len(self.peer_bitmap)

        
        # Idenitifico (if any) i pieces del mio peer che io non ho
        for i, (m,p) in enumerate(zip(self.my_bitmap, self.peer_bitmap)):
            if not m and p:
                self.am_interested_in.append(i)

        self.logger.debug("[MASTER] Sending M_PEER_HAS to master")
        # Informs master of peer's bitmap
        self.send_to_master(
            utils.M_PEER_HAS(
                self.am_interested_in,
                self.address,
                self.max_concurrent_pieces+1, #TODO: migliorabile!
            )
        )
        
        # Se, dal confronto fra la mia e l'altrui bitmap, scopro
        # che non mi interessa nulla di ciò che ha il peer, informalo che
        # sei NOT_INTERESTED
        if len(self.am_interested_in) == 0 or len(self.scheduled) == 0:
            self.logger.debug("Nothing to be interested in")
            if self.am_interested:
                self.am_interested = False
                self.logger.debug("Sending NOT_INTERESTED")
                self.send_message(MexType.NOT_INTERESTED)
            return
        
        if self.am_interested: # Se ero già interessato in precedenza, non fare nulla
            return

        # Altrimenti dichiara il tuo interesse
        self.am_interested = True
        self.logger.debug("Sending INTERESTED message")
        self.send_message(MexType.INTERESTED)

        self.ask_for_new_pieces()

        return

    
    def get_piece_size(self, piece_index: int) -> int:
        """ 
        Returns the length of a piece. 
        
        This is needed, because the last pieces of a torrent probably
        has an irregular size. We want to catch corner cases, so...
        this is it.
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


    def ask_for_single_piece(self, piece_idx: int):
        """
        Low-level routine that sends the request for a piece to the peer.
        """
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
        Richiedo un pezzo completamente nuovo, cioè non già in self.progresses.
        """

        if len(self.am_interested_in) == 0 or len(self.scheduled) == 0:
            if len(self.scheduled) == 0:
                self.logger.debug("No pieces pending!")
            self.logger.debug("Nothing to be interested in")
            self.logger.debug("Sending NOT-INTERESTED message")
            if self.am_interested:
                self.am_interested = False
                self.send_message(MexType.NOT_INTERESTED)
            return

        if not self.am_interested:
            self.logger.warning("Want to ask piece, but I'm not interested? Sending INTERESTED", )
            self.am_interested = True
            self.send_message(MexType.INTERESTED)
            if self.peer_chocking:
                return # wait for unchoke

        if self.peer_chocking:
            self.logger.debug("Wanted to ask a new piece, but am choked")    
            return

        not_yet_started = set(self.am_interested_in) - set(self.my_progresses.keys())
        not_yet_started = not_yet_started & set(self.scheduled)
        
        # Se tutti i pieces sono già stati avviati o completati
        if len(not_yet_started) == 0:
            self.logger.debug("No NEW pieces are requestable; abort")
            return

        # Se sto già scaricando il numero max di pieces contemporaneamente
        if len(self.my_progresses) > self.max_concurrent_pieces:
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

        if len(self.my_progresses) > self.max_concurrent_pieces:
            self.logger.debug("Already topping max concurrent requests")
            return

        if not self.am_interested:
            self.logger.warning("Want to ask piece %d, but not interested; sending INTERESTED")
            self.am_interested = True
            self.send_message(MexType.INTERESTED)
            if self.peer_chocking:
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
            piece_length=min(self.metainfo.block_size, total_len - offset_start)
        )
        

    def manage_received_have(self, piece_index: int):
        self.logger.debug("Acknowledging that peer has new piece %d", piece_index)
        self.peer_bitmap[piece_index] = True
        self.logger.debug(
            "[MASTER] Sending M_PEER_HAS %d to master, ask schedule %d pieces",
            piece_index, 1
        )
        
        self.send_to_master(
            utils.M_PEER_HAS(
                [piece_index],
                self.address,
                schedule_new_pieces=1
            )
        )

        
    def manage_received_piece(self, piece_index, piece_offset, piece_payload):
        if self.my_bitmap[piece_index]:
            self.logger.warning(
                "Received fragment of piece %d offset %d, but I have piece it already (len: %d)",
                piece_index, piece_offset, len(piece_payload)
            )
            return

        
        # Aggiorna my_progersses
        old_data, piece_size = self.my_progresses[piece_index]

        if piece_offset < len(old_data):
            self.logger.warning("Duplicate block, received offset %d but expecting %d",
                                piece_offset, len(old_data))
            return # TODO: ?
                                
        self.logger.debug("Received payload for piece %d offset %d length %d: %s...%s",
                          piece_index, piece_offset, len(piece_payload),
                          piece_payload[:4], piece_payload[-4:])
                                
        new_data = old_data + piece_payload
        
        if len(new_data) == piece_size:
            self.logger.debug("Completed download of piece %d", piece_index)
            
            if not self.verify_hash(piece_index, new_data):
                raise Exception("Hashes not matching") #TODO

            print("Completed: {:.1f}%".format(
                100 * (1 + sum(int(x) for x in self.my_bitmap)) / len(self.my_bitmap)
            ))
                  
            del self.my_progresses[piece_index]

            self.logger.debug("Sending HAVE for piece %d to peer", piece_index)
            self.send_message(MexType.HAVE, piece_index=piece_index)
            
            self.logger.debug("Setting my bitfield for piece %d as PRESENT", piece_index)
            self.update_my_bitmap(piece_index, True)
            self.am_interested_in.remove(piece_index)

            # M_PIECE richiede anche un nuovo pezzo al Master
            self.scheduled.remove(piece_index)
            self.logger.debug("[MASTER] Sending M_PIECE for %d", piece_index)
            self.send_to_master(utils.M_PIECE(piece_index, new_data, self.address))

            # Finito un pezzo, iniziane uno NUOVO
            self.ask_for_new_pieces()
            return
                              
        self.my_progresses[piece_index] = (new_data, piece_size)
        self.try_ask_for_piece(suggestion=piece_index)


        
    def manage_request(self, p_index, p_offset, p_length, deferred=False):
        """ Responds to a REQUEST message from the peer. """
        if self.am_choking:
            self.logger.warning("Received REQUEST but am choking.")
            return

        log_str = "Resuming deferred " if deferred else "Received "
        self.logger.debug(log_str + "REQUEST for piece %d offset %d length %d",
                          p_index, p_offset, p_length)

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

        # 1. Controlla se il pezzo è in cache_pieces
        # 2. Se sì, fai come al solito ma leggendo dalla cache_pieces
        # 3. Se no, chiedi pezzo al master; archivia il messaggio di REQUEST
        # in self.deferred_requests, quindi quando dal master arriva un messaggio
        # M_PIECE, finalmente rispondi al Peer

        if not p_index in self.cache_pieces:
            self.debug("[MASTER] Deferred response to REQUEST piece %d", p_index)
            self.send_to_master(
                utils.M_PEER_REQUEST(p_index, self.address)
            )
            self.deferred_peer_requests[p_index] = (p_offset, p_length)
            return

        if deferred:
            offs, length = self.deferred_peer_requests[p_index]
            if offs != p_offset or length != p_length:
                self.logger.error("Resuming deferred piece %d, but couldn't find deferred request!",
                                  p_index)
                breakpoint()
                raise Exception("Deferentiationalitation error")

            del self.deferred_peer_requests[p_index]
            
                
        self.send_message(
            MexType.PIECE,
            piece_index=p_index,
            piece_offset=p_offset,
            piece_length=p_length
        )

        
        # TODO: revisione di queste due righe
        # TODO: rendile una funzione, da chiamare ad ogni invio di piece
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

        
    def verify_hash(self, piece_index: int, data: bytes):
        sha = utils.sha1(data)

        are_equal = sha == self.metainfo.pieces_hash[piece_index]

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
    def __init__(self, metainfo, tracker_manager, **options):
        self.host = "localhost"
        self.peer = None
        self.options = options

        self.logger = logging.getLogger("ThreadedServer")
        self.logger.debug("__init__")
        
        self.metainfo = metainfo
        self.tracker_manager = tracker_manager

        port = options["port"]
        self.logger.debug("Server is binding at %s", (self.host, port))
        
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, port))

        self.port = self.sock.getsockname()[1]
        if port == 0:
            self.logger.debug("Self port: %d", self.port)

        self.peers = self.options.get("suggested_peers", [])
        peers = self.tracker_manager.notify_start()
        for ip, pport in peers: #filters myself
            if ip in ["localhost", utils.get_external_ip()]:
                if self.port == pport:
                    continue
            self.peers.append((ip, pport))
        
        # La bitmap iniziale, quando il programma viene avviato.
        # Viene letta da un file salvato in sessioni precedenti, oppure
        # creata ad hoc.
        # TODO: serve davvero, qui? Spostare in metainfo?
        self.global_bitmap: List[bool] = utils.data_to_bitmap(
            self.options["output_file"],
            num_pieces=self.metainfo.num_pieces
        )
        
        self.max_peer_connections = 1
        self.active_connections = set()
        
        self.mcu = master.MasterControlUnit(self.metainfo, self.global_bitmap, self.options)
        self.master_queue = self.mcu.get_master_queue()

        self.pms = list()

        
    def main(self):       
        socket_listen_t = threading.Thread(target=self.listen)
        socket_listen_t.start()        

        self.mcu.main()
        
        print("Available peers:", self.peers)
        if self.peers == []:
            self.logger.debug("No peers currently available")
            return
        
        i = 0
        while i < min(self.max_peer_connections, len(self.peers)):
            ip, port = random.choice(self.peers)
            self.logger.debug("Chosen peer: %s:%s", ip, port)
            
            if (ip, port) in self.active_connections:
                self.logger.warning("Chosen an already connected peer")
                time.sleep(1)
                continue
            
            if ip == self.host or port == self.port: #TODO: sbagliato, peer può usare mia stessa porta
                self.logger.debug("Attempting to connect to myself (%s): abort", (ip, port))
                time.sleep(2)
                continue

            try:
                queues = (Queue(), self.master_queue)
                peer_manager = self.connect_as_client(ip, port, queues)
                self.active_connections.add((ip, port))
                self.mcu.add_connection_to(peer_manager)
                self.pms.append(peer_manager)
                i += 1
                
            except Exception as e:
                self.logger.error("%s", e)
                time.sleep(2)
                continue
            
    
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
                (Queue(), self.master_queue),
                self.global_bitmap,
                self.options,
                Initiator.OTHER
            )

            self.active_connections.add(address)
            self.mcu.add_connection_to(newPeer)
            self.pms.append(new_peer)
            
            t = threading.Thread(target = newPeer.main)
            t.start()

            
    def connect_as_client(self, ip, port, queues: Tuple[Queue, Queue]):
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
import argparse

def parser(s=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("torrent_path",
                        type=pathlib.Path,
                        help="path to .torrent file")

    parser.add_argument("output_file",
                        type=pathlib.Path,
                        help="where to download the file")

    parser.add_argument("-p", "--port",
                        action="store",
                        type=int,
                        default=50146,
                        help="port for this client")

    parser.add_argument("-v", "--verbosity",
                        action="count",
                        default=0,
                        dest="debug_level",
                        help="debug level")

    parser.add_argument("--suggested_peers",
                        action="store",
                        default=[],
                        help="suggested_peers")

    parser.add_argument("--delay",
                        type=float,
                        default=0,
                        help="delay for every sent message (only debug)")

    if s:
        options = vars(parser.parse_args(s))
    else:
        options = vars(parser.parse_args())
        
    options["debug_level"] = utils.int_to_loglevel(options["debug_level"])
    options["debug"] = False

    return options


if __name__ == "__main__":
    options = {
        "torrent_path": pathlib.Path("/home/groucho/torrent/image.jpg.torrent"),
        "output_file":  pathlib.Path("/home/groucho/torrent/downloads/image.jpg"),
        "delay": 0,
        "debug": False,
        "debug-level": logging.WARNING,
        "port": 50146,
        "suggested_peers": [("78.14.24.41", 50144)]
    }

    with open(options["torrent_path"], "rb") as f:
        metainfo = md.MetaInfo(
            bencodepy.decode(f.read()) | options
        )

    tm = md.TrackerManager(metainfo, options)

    t = ThreadedServer(
        metainfo, tm,
        **options
    )

    t.main()
