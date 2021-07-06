import bencodepy
import hashlib
import ipaddress
import random
import requests
import pathos.multiprocessing as mp

from requests.exceptions import Timeout
from math import log2
from queue import Queue
from typing import *
    
import Fiume.config as config
import Fiume.utils as utils

import logging
logging.getLogger("urllib3").setLevel(logging.WARNING)

Url = str
Address = Tuple[str, int] # (ip, port)

#################################

class MetaInfo(dict):
    """ 
    NB: solo per Single File Mode. 
    
    Classe che contiene le informazioni del file .torrent
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pieces_hash = utils.split_in_chunks(self[b"info"][b"pieces"], 20)

        sha = hashlib.sha1()
        sha.update(bencodepy.encode(self[b"info"]))
        self.info_hash = sha.digest()

        self.trackers: List[Url]  = self.__gather_trackers()

        self.piece_size: int = self[b"info"][b"piece length"]
        self.block_size: int = 16384 #16kb, standard
        self.total_size: int = self[b"info"][b"length"]
        self.num_pieces: int = len(self.pieces_hash)

        self.download_fpath: Path = self["output_file"]
        self.human_name: str = self.download_fpath.name[:4] + ".torr"

    def __gather_trackers(self) -> List[Url]:
        """ Unites all possible trackers in a single list, useless """
        trackers = [self[b"announce"]]
        
        if b"announce-list" in self:
            trackers += [l[0] for l in self[b"announce-list"]]

        return trackers

    
    
###################


class TrackerManager:
    def __init__(self, metainfo: MetaInfo, options: Dict[str, Any]):
        self.options = options
        self.bitmap_file = utils.get_bitmap_file(self.options["output_file"])
        self.metainfo: MetaInfo = metainfo
        
        self.my_ip = utils.get_external_ip()
        self.my_port = self.options["port"]
        
        self.logger = logging.getLogger(
            "TrackManager - {}".format(self.metainfo.human_name)
        )
        self.logger.setLevel(options.get("debug_level", logging.DEBUG))
        self.logger.debug("__init__")

        self.working_trackers: List[Tuple[str, utils.RepeatedTimer]] = list()

        self.peer_id = config.CLIENT_INFO + random.randbytes(12)
        self.tracker_ids: Dict[Url, bytes] = dict()
        
        self.peers: Set[Address] = set()
        # Will write here new peers discovered through regular contact
        # with trackers. THis will be red by ThreadedServer
        self.queue_for_new_peers: Queue = Queue()

        
        
    def tell_all_trackers(self, params) -> List[Tuple[Url, requests.Response]]:
        """ 
        Tells something to all trackers in the .torrent file.
        """        
        pool    = mp.Pool(16 * mp.cpu_count())
        results = pool.map(
            lambda url: self.__tell_tracker(url, params),
            self.metainfo.trackers
        )
        pool.close()
        return [r for r in results if r is not None]

    
    def __tell_tracker(self, url, params) -> Optional[Tuple[Url, requests.Response]]:
        """ 
        Single iterator for tell_all_trackers.
        """
        try:
            response = requests.get(url, params=self.base_params() | params, timeout=2.0)
            self.logger.debug("%s works!!!", url)
            return (url, response)
        except Timeout:
            self.logger.debug("%s has time-outed", url)
            return None
        except Exception as e:
            self.logger.debug("%s has failed for some generic reason: %s", url, e) 
            return None

        
    def base_params(self) -> Dict:
        """ 
        Tracker GET request parameters that are always the same. Calculates `downloaded`,
        `uploaded` and `left` by reading the BITMAP file.
        """

        if self.bitmap_file.exists():
            with open(self.bitmap_file, "r") as f:
                bitmap = [int(c) for c in f.read().strip()]
                
                if bitmap == []:
                    self.logger.error("Bitmap file is empty and corrupted.")
                    raise Exception("Bitmap file is empty and corrupted")

                downloaded = sum(bitmap[:-1]) * self.metainfo.piece_size
                if bitmap[-1]:
                    downloaded += self.metainfo.total_size % self.metainfo.piece_size

                uploaded   = 0 # TODO
                left       = self.metainfo.total_size - downloaded
        else: # First connection 
            downloaded = 0
            uploaded = 0
            left = self.metainfo.total_size

            
        return {
            "info_hash": self.metainfo.info_hash,
            "peer_id": self.peer_id,
            "port": self.my_port, #50146,
            "compact": "1",
            "ip": self.my_ip,
            "downloaded": str(downloaded),
            "uploaded": str(uploaded),
            "left": str(left),
        }

    
    def notify_start(self) -> Tuple[List[Address], Queue]:
        """ 
        Inform all the trackers that you are about to start downloading, and
        hence ask for peers.
        """
        self.logger.debug("Informing trackers I'm starting to download, asking for peers")
        self.logger.debug("%s", self.base_params())
        
        results = self.tell_all_trackers(
            {"event": "started"}
        )
        
        peers: Set[Address] = set()
        
        for tracker_url, __response in results:
            response = __response.content

            try:
                response_bencode = bencodepy.decode(response)                
            except bencodepy.BencodeDecodeError as e:
                self.logger.debug("%s has returned a non bencode object %s", tracker_url, e)            
                continue

            # Start regular requests to tracker
            repeat_notify = utils.RepeatedTimer(
                self.notify_nothing_important,
                response_bencode[b"interval"],
                tracker_url
            )
            self.working_trackers.append((tracker_url, repeat_notify))
            
            
            self.tracker_ids[tracker_url] = (
                response_bencode[b"tracker id"] if b"tracker id" in response_bencode else b""
            )

            peers = peers | self.__decode_peer(response_bencode)

        self.peers = peers
        return list(self.peers), self.queue_for_new_peers

    
    def notify_completion(self):
        """ 
        Inform all the trackers that you have finished downloading.
        In theory, you should call this /only/ when reaching 100%.
        """
        self.logger.debug("Notifying trackers of completion...")
        self.tell_all_trackers(
            {"event": "completed"}
        )

                
    def notify_stop(self):
        """
        Inform all the trackers that you are shutting down gracefully.
        """
        self.logger.debug("Notifying trackers of graceful shutdown...")
        self.tell_all_trackers(
            {"event": "stopped"}
        )

                
    def notify_nothing_important(self, url):
        """
        Inform a tracker, after time=interval (`interval` field found in
        tracker response after a event=started), about my current download
        status.

        IRC says: if you haven't completed the download yet, you simply
        send a request with no `event` field and with left=metainfo.total_size.
        """
        params = self.base_params()
        if params["left"] != 0:
            params["left"] = self.metainfo.total_size
        self.logger.info("Routine contact with tracker %s for new_peers", url)

        maybe_response = self.__tell_tracker(url, params)
        if maybe_response is None:
            return
        
        _, response = maybe_response

        try:
            response_bencode = bencodepy.decode(response.content)
            
            peers = self.__decode_peer(response_bencode) - self.peers #only new peers
            for addr in peers:
                self.queue_for_new_peers.put(addr)
                
        except bencodepy.BencodeDecodeError as e:
            self.logger.debug("Couldnt' decode peers list because of %s", e)

                
    def __decode_peer(self, response_bencode: bencodepy.Bencode) -> Set[Address]:
        """ 
        From a bencode bytestring to the list of peers' (ip, port).

        It automatically excludes my address.
        """
        
        peers = set()
        
        if not b"peers" in response_bencode:
            self.logger.debug("No peers in bencode answer from tracker")
            return set()
            
        for raw_address in utils.split_in_chunks(response_bencode[b"peers"], 6):
            ip = ipaddress.IPv4Address(raw_address[:4]).exploded
            port = utils.to_int(raw_address[4:6])

            # BUG: ~secondo me~ SICURAMENTE questo causerà problemi quando l'utente inserirà 0 come
            # my_port
            if utils.is_unwanted_addr((ip, port), (self.my_ip, self.my_port)):
                continue
            
            peers.add((ip, port))

        self.logger.debug("Found the following peers: %s", str(peers))

        return peers

        
# def temp(b):
#     peers = set()
#     for raw_address in utils.split_in_chunks(b, 6):
#         ip = ipaddress.IPv4Address(raw_address[:4]).exploded
#         port = utils.to_int(raw_address[4:6])
#         peers.add((ip, port))
#     return peers
