import socket
import time
import threading
import logging
import enum
import random
import pathlib 
import sys

from queue import Queue
from typing.io import *
from typing import *

import Fiume.utils as utils
import Fiume.master as master
import Fiume.ttl as ttl
import Fiume.config as config

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
    """
    PeerManager manages the connection with a single peer. It embedds
    the logic for the peer-to-peer .torrent protocol. 

    It works under the directions of a MasterControlUnit instance which
    decides, among others, what pieces to download. This is needed, as
    a ThreadedServer will download files from many peers concurrently, and
    a central coordination is needed. Communications with the MasterControlUnit
    happen through a Queue.
    """
    
    def __init__(self, socket: Tuple,
                 metainfo, tracker_manager,
                 master_queues: Tuple[Queue, Queue],
                 initial_bitmap: List[bool],
                 options: Dict[str, Any],
                 initiator: Initiator):
        
        # Peer socket
        self.socket, self.address = socket
        self.peer_ip, self.peer_port = self.address
                
        self.metainfo = metainfo
        self.tracker_manager = tracker_manager

        self.logger = logging.getLogger(
            "[{}] {}:{}".format(self.metainfo.human_name, self.peer_ip, self.peer_port)
        )
        self.logger.setLevel(options.get("debug_level", logging.DEBUG))
        self.logger.debug("PeerManager __init__()")

        
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
        """
        The entry point for this class. It starts the interaction with
        a peer.
        """
        self.logger.debug("main")

        t1 = threading.Thread(target=self.message_socket_receiver)
        t1.start()
        
        # Depending on who initiated the connection, either me or my peer
        # will send the first message of the HANDSHAKE
        if self.initiator == Initiator.SELF:
            self.send_handshake()

        self.message_interpreter()

        
    def send_to_master(self, mex: utils.MasterMex):
        """
        Sends a message to the master's queue.
        """
        self.queue_to_master.put(mex)
   
        
    def send_handshake(self):
        """
        Sends the first message of the HANDSHAKE.
        """
        self.logger.info("Sending HANDSHAKE")
        self.send_message(MexType.HANDSHAKE)
        self.sent_handshake = True
        if self.received_handshake:
            self.logger.info("Sending BITFIELD")
            self.send_message(MexType.BITFIELD)


    def receive_handshake(self, mex):
        """
        Handles a HANDSHAKE message received. 
        """
        if not mex[28:48] == self.metainfo.info_hash: # TODO
            self.shutdown(reason="InfoHash received during HANDSHAKE different from expected!")
            return
        
        self.received_handshake = True
        self.logger.info("Received HANDSHAKE")
        
        if self.sent_handshake:
            self.logger.info("Sending BITFIELD")
            self.send_message(MexType.BITFIELD)
        else:
            self.send_handshake()        


    def initialize_file(self, fpath: pathlib.Path):
        """ Initialize the download file """
        if not fpath.exists():
            # Es. se path ?? /a/b/c/d.jpg ma b,c,d non
            # esistono, creale
            if not fpath.parent.exists():
                fpath.parent.mkdir(parents=True)

            fpath.touch()

            with open(fpath, "wb") as f:
                f.write(bytes(self.metainfo.num_pieces))

            return fpath

        return fpath

                                     
    # Thread a s?? stante
    def message_socket_receiver(self):        
        try:
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

                    # Otherwise, if a b"" happens while in this loop you have a terrible
                    # infinite loop!
                    if len(data) == 0:
                        self.queue_in.put(b"")
                        return
                
                    raw_mex += data
                    length -= len(data)
                    if length < 0:
                        breakpoint()
                        _ = 0
                    if length != 0 and self.debug:
                        print(f"Still waiting for {length} bytes...")

                self.queue_in.put(raw_length + raw_mex)
                
        except socket.timeout as e:
            self.logger.warning("%s", e)
            self.shutdown(reason="Socket time-outed while waiting for messages; disconnecting")
            return

        # If connection is abruptly terminated by peer
        except ConnectionError as e:
            self.logger.warning("%s", e)
            self.shutdown(reason="Generic socket error")
            return


            
    def shutdown(self, reason:Union[str, None] = None):
        self.logger.warning("Shutdown down for reason: %s", reason)
        self.send_to_master(utils.M_DISCONNECTED(self.address, reason))
        sys.exit(0)

    
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

    
    #######

    def message_interpreter(self):
        """ 
        Dispatches any kind of received message (either from Master or peer)
        to the correct handler function.
        """

        while True:
            mex = self.queue_in.get()

            if isinstance(mex, utils.MasterMex):
                
                # catch early a KILL message from MASTER
                if isinstance(mex, utils.M_KILL):
                    self.logger.debug("[MASTER] Received KILL")
                    self.send_to_master(utils.M_DEBUG("Got KILLED", self.address))
                    return

                # If otherwise is any other MASTER mex, dispatch to this function
                self.control_message_interpreter(mex)
                continue

            
            # Empty message from peer == peer has disconnected
            if mex == b"":
                self.shutdown(reason="Received empty message from peer!")
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
                self.logger.error("CANCEL not implemented")

            elif mex_type == MexType.PORT:
                self.logger.error("PORT message not implemented")

            else:
                self.logger.error("Received unknown message")
                breakpoint()

            
    def control_message_interpreter(self, mex: utils.MasterMex):
        """
        Reacts to any kind of message received from Master.
        """
            
        if isinstance(mex, utils.M_DEBUG):
            self.logger.debug("[MASTER] Received DEBUG message from master: %s", mex.data)
            self.send_to_master(utils.M_DEBUG("Got DEBUGGED", self.address))
            return
        
        if isinstance(mex, utils.M_OUR_BITMAP):
            self.logger.debug("[MASTER] Received OUR_BITMAP message from master")
            self.my_bitmap = mex.bitmap
            return
            
        if isinstance(mex, utils.M_SCHEDULE):
            self.logger.debug("[MASTER] Received SCHEDULE message from master: %s", mex.pieces_index)
            self.scheduled += mex.pieces_index
            # Avoids deadlock:
            # if no pieces are currently downloaded, try_ask_for_piece will
            # start downloading the received piece(s);
            # if we were downloading pieces already, simply start one of the
            # new pieces (if we are not > max_concurrent_pieces
            if self.scheduled != []: # TODO pensa se pu?? funzionare
                self.ask_for_new_pieces()

            return

        
        if isinstance(mex, utils.M_NEW_HAVE):
            self.logger.debug("[MASTER] Received NEW_HAVE message from master: %s", mex.piece_index)
            self.my_bitmap[mex.piece_index] = True
            self.send_message(MexType.HAVE, piece_index=mex.piece_index)
            return        

        # M_Piece has a bit complicated workflow.
        # If we receive a M_PIECE, it means that in the past we requested to the
        # master, on behalf of the peer, a piece N.
        # We put the entire piece N in a cache.
        # Then we resume the deferred request, 
        if isinstance(mex, utils.M_PIECE):
            self.logger.debug("Received piece %d from master", mex.piece_index)
            
            self.cache_pieces[mex.piece_index] = mex.data

            if mex.piece_index not in self.deferred_peer_requests:
                # Should not happen
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

        if isinstance(mex, utils.M_COMPLETED):
            self.logger.info("Received COMPLETED message from Master")
            return

        breakpoint()
        raise Exception("unknown message")
        
        
    def send_message(self, mexType: MexType, **kwargs):
        """
        Sends (via socket) a message to the peer. 

        The **kwargs are for custom parameters of the 
        message (eg. `piece_index` for a PIECE message, and so on).
        """
        if "delay" in self.options: # only for debug
            time.sleep(self.options["delay"])

        try:
            self.socket.sendall(self.make_message(mexType, **kwargs))
        except IOError as e:
            self.logger.warning("%s", e)
            self.logger.warning("Shutting down after peer abroupt disconnection")
            self.shutdown()
        
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

        if len(self.my_bitmap) < 120:
            utils.pprint_bitmap(self.my_bitmap, "self")
            utils.pprint_bitmap(self.peer_bitmap, "peer")

        # Sanity check
        assert len(self.my_bitmap) == len(self.peer_bitmap)

        # Identify (if any) pieces that my peer has but I have not.
        for i, (m,p) in enumerate(zip(self.my_bitmap, self.peer_bitmap)):
            if not m and p:
                self.am_interested_in.append(i)

        # Informs master of peer's bitmap
        self.logger.debug(
            "[MASTER] Sending M_PEER_HAS to master, requesting schedule of %d pieces",
            self.max_concurrent_pieces+1
        )
        self.send_to_master(
            utils.M_PEER_HAS(
                self.am_interested_in,
                self.address,
                self.max_concurrent_pieces+1, #TODO: migliorabile!
            )
        )
        
        # If, after confronting my and peer's bitmaps, it turns out that
        # I don't want any pieces of my peer, send NOT INTERESTED
        if len(self.am_interested_in) == 0 or len(self.scheduled) == 0:
            self.logger.debug("Nothing to be interested in")
            if self.am_interested:
                self.am_interested = False
                self.logger.debug("Sending NOT_INTERESTED")
                self.send_message(MexType.NOT_INTERESTED)
            return

        # Se ero gi?? interessato in precedenza, non fare nulla
        if self.am_interested:
            return

        # Otherwise, declare you are INTERESTED
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
        
        # self.get_piece_size serve per gestire len irregolare dell'ultimo piece
        self.my_progresses[piece_idx] = (b"", self.get_piece_size(piece_idx))

        
        
    def ask_for_new_pieces(self):
        """ 
        Richiedo un pezzo completamente nuovo, cio?? non gi?? in self.progresses.
        """

        if len(self.am_interested_in) == 0 or len(self.scheduled) == 0:
            if len(self.scheduled) == 0:
                self.logger.debug("No pieces pending!")
            self.logger.debug("Nothing to be interested in")
            if self.am_interested:
                self.logger.debug("Since am interested: sending NOT-INTERESTED message")
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
        
        # Se tutti i pieces sono gi?? stati avviati o completati
        if len(not_yet_started) == 0:
            self.logger.debug("No NEW pieces are requestable; abort")
            return

        # Se sto gi?? scaricando il numero max di pieces contemporaneamente
        if len(self.my_progresses) > self.max_concurrent_pieces:
            self.logger.debug("Already downloading at the fullest")
            return

        
        random_piece = random.sample(
            list(not_yet_started), #non si pu?? fare random choice su set()
            k=min(self.max_concurrent_pieces - len(self.my_progresses),
                  len(not_yet_started))
        )

        for piece in random_piece:
            self.ask_for_single_piece(piece)

    
    def try_ask_for_piece(self, suggestion=None):
        """ 
        Requests for an already-started-but-not-completely-downloaded
        piece.

        If no half-downloaded piece exists, then asks for a completely
        new piece (reverts to ask_for_new_pieces).
        """
        if self.peer_chocking:
            self.logger.debug("Wanted to request a piece, but am choked")
            return
        
        if len(self.my_progresses) == 0: # se non ci sono pezzi incompleti
            self.ask_for_new_pieces()
            return
            
        if len(self.my_progresses) > self.max_concurrent_pieces:
            self.logger.debug("Already topping max concurrent requests")
            return

        if not self.am_interested:
            self.logger.warning("Want to ask piece %d, but am not interested; sending INTERESTED")
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
            return
                                
        self.logger.debug("Received payload for piece %d offset %d length %d: %s...%s",
                          piece_index, piece_offset, len(piece_payload),
                          piece_payload[:4], piece_payload[-4:])
                                
        new_data = old_data + piece_payload
        
        if len(new_data) >= piece_size:
            if len(new_data) > piece_size:
                self.logger.error(
                    "Received more data then the expected size: %d, while expected %d",
                    len(new_data), piece_size
                )
                
            self.logger.info("Completed download of piece %d", piece_index)
            
            if not self.verify_hash(piece_index, new_data):
                self.logger.critical("Hashes for %d don't match", piece_index)
                raise Exception("Hashes not matching") #TODO

            self.logger.info("Downloaded: {:.1f}%".format(
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

        # 1. Controlla se il pezzo ?? in cache_pieces
        # 2. Se s??, fai come al solito ma leggendo dalla cache_pieces
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
            self.logger.info("Download completed!")
            self.completed = True
            # If peers has too all the pieces, shutdown
            if all(bool(int(x)) for x in self.peer_bitmap):
                self.shutdown()

        
    def verify_hash(self, piece_index: int, data: bytes):
        sha = utils.sha1(data)

        are_equal = sha == self.metainfo.pieces_hash[piece_index]

        if are_equal:
            self.logger.debug("Calculated hash for piece %d matches with metainfo", piece_index)
        else:
            self.logger.warning("Hashes for piece %d DO NOT MATCH!", piece_index)
            breakpoint()

        return are_equal


#################################????


# Questo oggetto gestisce le connessioni entrambi.
# Ogni nuova connessione viene assegnata ad un oggetto TorrentPeer,
# il quale si occuper?? di gestire lo scambio di messaggi
class ThreadedServer:
    def __init__(self, metainfo, tracker_manager, socket=None, **options):
        self.host, self.public_ip = "localhost", utils.get_external_ip()
        self.peer = None
        self.options = options

        self.metainfo = metainfo
        self.tracker_manager = tracker_manager

        self.logger = logging.getLogger("[{}] ThreadedServer".format(self.metainfo.human_name))
        self.logger.setLevel(options.get("debug_level", logging.DEBUG))
        self.logger.debug("__init__")

        
        if socket is None:
            port = options["port"]
            self.logger.info("Server is binding at %s", (self.host, port))

            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind((self.host, port))

            self.port = self.sock.getsockname()[1]
            if port == 0:
                self.logger.info("Self port: %d", self.port)
        else:
            self.sock = socket
            self.port = self.sock.getsockname()[1]
            self.logger.info("Received socket for %s", self.sock.getsockname())

            
        # self.peers = self.options.get("suggested_peers", [])
        self.peers, self.peers_queue_in = self.tracker_manager.notify_start()        

        # La bitmap iniziale, quando il programma viene avviato.
        # Viene letta da un file salvato in sessioni precedenti, oppure
        # creata ad hoc.
        # TODO: serve davvero, qui? Spostare in metainfo?
        self.initial_bitmap: List[bool] = utils.data_to_bitmap(
            self.options["output_file"],
            num_pieces=self.metainfo.num_pieces
        )

        self.timeout = self.options["timeout"]
        # self.sock.settimeout(self.timeout)

        self.max_peer_connections = self.options["max_peer_connections"]
        self.active_connections = set()
        self.is_completed = all(bool(int(x)) for x in self.initial_bitmap)

        self.ts_queue_in = Queue()
        self.mcu = master.MasterControlUnit(
            self.metainfo, self.initial_bitmap,
            self.ts_queue_in, self.tracker_manager,
            self.options
        )
        self.master_queue = self.mcu.get_master_queue()

        self.ttl_peer_table = ttl.TTL_table(self.timeout)

        
    def main(self):       
        socket_listen_t = threading.Thread(target=self.listen)
        socket_listen_t.daemon = True
        socket_listen_t.start()
        
        self.mcu.main()
        
        self.logger.debug("Available peers: %s", self.peers)
        if self.peers == set():
            self.logger.info("No peers currently available")

        # Connects to every peer who allows me to connect
        if not self.is_completed:
            for ip, port in self.peers:
                queues = Queue(), self.master_queue
                self.connect_as_client(ip, port, queues)
            
            
        while not self.is_completed:
            # Peek in the queue, to see if any peer has disconnected
            # or we have completed
            while not self.ts_queue_in.empty():
                mex = self.ts_queue_in.get()

                if isinstance(mex, utils.M_DISCONNECTED):
                    self.logger.info("Disconnected peer %s", mex.sender)
                    self.active_connections.remove(mex.sender)

                    new_ttl = self.ttl_peer_table.add(mex.sender) # TODO timeout
                    self.logger.info("Inserting %s in ttl table with ttl=%d",
                                      mex.sender, new_ttl)
                    
                    # try to connect to any known peer, which did not
                    # recently disconnected from us
                    if self.ttl_peer_table.any_ready():
                        [(new_ip, new_port)] = self.ttl_peer_table.extract(n=1)
                        self.connect_as_client(
                            new_ip, new_port,
                            (Queue(), self.master_queue)
                        )

                elif isinstance(mex, utils.M_COMPLETED):
                    self.logger.info("Completed download!")
                    self.is_completed = True
                    
                elif isinstance(mex, utils.M_KILL): #coming from fiume/cli
                    self.logger.info("Received KILL message, closing.")
                    sys.exit(0)


                        
            # If completed dowload, this loop ends; the thread listening
            # for new connections however will remain alive
            if self.is_completed:
                self.tracker_manager.notify_completion()
                break


            if not self.peers_queue_in.empty():
                while not self.peers_queue_in.empty():
                    (new_ip, new_port) = self.peers_queue_in.get()
                    self.logger.info("Received new peer from trackers, %s", (new_ip, new_port))
                    self.connect_as_client(
                        new_ip, new_port,
                        (Queue(), self.master_queue)
                    )
                time.sleep(2)
                continue

                
            if len(self.active_connections) == 0:
                
                if self.ttl_peer_table.any_ready():
                    [(new_ip, new_port)] = self.ttl_peer_table.extract(n=1)
                    self.logger.info("Found a peer to wake in TTL, %s", (new_ip, new_port))
                    self.connect_as_client(
                        new_ip, new_port,
                        (Queue(), self.master_queue)
                    )
                    time.sleep(1)
                    continue
                
                        
                else:
                    self.logger.debug("No active connections while download is incomplete; 5 second sleep")
                    time.sleep(5)
                    continue
                    
            else:
                time.sleep(1)
                    
        self.logger.info("Completed download, now in seed-listening phase") 

        # socket_listen_t.join()
        
            
    def connect_as_client(self, ip, port, queues: Tuple[Queue, Queue]):
        if (ip, port) in self.active_connections:
            self.logger.warning("%s:%s already in active_connections, bypass", ip, port)
            return
        
        try:
            new_socket = socket.create_connection((ip, port))
            new_socket.settimeout(self.timeout)
            
            new_peer = PeerManager(
                (new_socket, (ip, port)),
                self.metainfo, self.tracker_manager,
                queues,
                self.initial_bitmap, self.options,
                Initiator.SELF)
            
            self.logger.info("Connected to: %s:%s", ip, port)
            
        except socket.timeout:
            self.logger.debug("Cannot connect to %s after %d seconds, abort", (ip, port), self.timeout)
            new_ttl = self.ttl_peer_table.add((ip, port))
            self.logger.debug("Hybernating %s with TTL=%d", (ip, port), new_ttl)
            return
        
        except ConnectionRefusedError as e:
            self.logger.debug("%s: %s", (ip, port), e)
            new_ttl = self.ttl_peer_table.add((ip, port))
            self.logger.debug("Hybernating %s with TTL=%d", (ip, port), new_ttl)
            return
        
        except Exception as e:
            self.logger.error("%s: %s", (ip, port), e)
            new_ttl = self.ttl_peer_table.add((ip, port))
            self.logger.debug("Hybernating %s with TTL=%d", (ip, port), new_ttl)
            raise e

        t = threading.Thread(target = new_peer.main)
        t.daemon = True
        t.start()
        
        self.active_connections.add((ip, port))
        self.mcu.add_connection_to(new_peer)

    
    def listen(self):
        self.logger.info("Started listening on %s", (self.host, self.port))
        self.logger.debug("Max connections number: %d", self.max_peer_connections)
        
        self.sock.listen(self.max_peer_connections) # Numero massimo di connessioni in attesa (?)

        while True:
            self.logger.debug("Waiting for connections...")
            client_socket, address = self.sock.accept()
            self.logger.info("Received connection request from: %s", address)
            
            new_peer = PeerManager(
                (client_socket, address),
                self.metainfo,
                self.tracker_manager,
                (Queue(), self.master_queue),
                self.initial_bitmap,
                self.options,
                Initiator.OTHER
            )

            self.active_connections.add(address)
            self.mcu.add_connection_to(new_peer)
            
            t = threading.Thread(target = new_peer.main)
            t.daemon = True
            t.start()
            

###############################

import Fiume.metainfo_decoder as md
import bencodepy
import argparse

def parser(s=None):
    parser = argparse.ArgumentParser(
        description="A Bittorrent client for single-file torrent.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument("-f",
                        type=pathlib.Path,
                        default=config.IN_DOWNLOAD_FILE,
                        action="store",
                        dest="downloading_json",
                        help="path to downloading.json file, containing currently downloading files"
                        )

    parser.add_argument("-p", "--port",
                        action="store",
                        type=int,
                        default=50146,
                        help="port for this client")

    parser.add_argument("-v", "--verbose",
                        action="count",
                        default=0,
                        dest="debug_level",
                        help="increases verbosity of output")
    
    parser.add_argument("--max-peer-connections",
                        action="store",
                        default=2,
                        type=int,
                        dest="max_peer_connections",
                        help="max num of concurrent inbound connections")

    parser.add_argument("--max-concurrent-pieces",
                        action="store",
                        default=5,
                        type=int,
                        dest="max_concurrent_pieces",
                        help="max num of concurrent pieces downloaded from/to peer")
    
    parser.add_argument("-t", "--timeout",
                        action="store",
                        default=10,
                        type=int,
                        help="timeout for various components of the program (only debug)")
    
    parser.add_argument("--delay",
                        type=float,
                        default=0,
                        help="delay for every sent message (only debug)")

    if s:
        options = vars(parser.parse_args(s))
    else:
        options = vars(parser.parse_args())
        
    options["debug_level"] = utils.int_to_loglevel(options["debug_level"])
    options["max_peer_connections"] = int(options["max_peer_connections"])
    options["timeout"] = int(options["timeout"])
    options["max_concurrent_pieces"] = int(options["max_concurrent_pieces"])
    options["debug"] = False

    print(options)
    
    return options
