import bencodepy
import hashlib
import ipaddress
import random
import requests
# import multiprocessing as mp
import pathos.multiprocessing as mp

from requests.exceptions import Timeout
from math import log2
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
                
    def __gather_trackers(self) -> List[Url]:
        """ Unites all possible trackers in a single list, useless """
        trackers = [self[b"announce"]]
        
        if b"announce-list" in self:
            trackers += [l[0] for l in self[b"announce-list"]]

        return trackers

    
    
###################


class TrackerManager:
    def __init__(self, metainfo: MetaInfo, options: Dict[str, Any]):
        self.logger = logging.getLogger("TrackManager")
        self.logger.setLevel(options.get("debug_level", logging.DEBUG))
        self.logger.debug("__init__")

        self.options = options
        self.bitmap_file = utils.get_bitmap_file(self.options["output_file"])
        
        self.metainfo: MetaInfo = metainfo
        self.working_trackers: List[str] = list()

        self.peer_id = config.CLIENT_INFO + random.randbytes(12)
        self.tracker_ids: Dict[Url, bytes] = dict()
        
        self.peers: List[Address] = list()
        
        #self.notify_start()

        
    def tell_all_trackers(self, params) -> List[Optional[Tuple[Url, requests.Response]]]:
        """ 
        Tells something to all trackers in the .torrent file.
        """
        def __tell_tracker_curry(url):
            return self.__tell_tracker(url, params)
        
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
            return (url, requests.get(url, params=self.base_params() | params, timeout=2.0))
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
            "port": 50146,
            "compact": "1",
            "ip": "78.14.24.41",
            "downloaded": str(downloaded),
            "uploaded": str(uploaded),
            "left": str(left),
        }

    
    def notify_start(self, exclude_self=True) -> List[Address]:
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
            
            if response[:2] != b"d8":
                self.logger.debug("%s has returned a non-bencode object", tracker_url)
                continue
            
            self.working_trackers.append(tracker_url)

            response_bencode = bencodepy.decode(response)
            
            self.tracker_ids[tracker_url] = (
                response_bencode[b"tracker id"] if b"tracker id" in response_bencode else b""
            )

            peers = peers | self.__decode_peer(response_bencode)

        self.peers = list(peers)

        return list(peers)

    
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

 
    def __decode_peer(self, response_bencode: bencodepy.Bencode) -> Set[Address]:
        """ 
        From a bencode bytestring to the list of peers' (ip, port).
        """
        
        peers = set()
        
        if not b"peers" in response_bencode:
            self.logger.debug("No peers in bencode answer from tracker")
            return set()
            
        for raw_address in utils.split_in_chunks(response_bencode[b"peers"], 6):
            ip = ipaddress.IPv4Address(raw_address[:4]).exploded
            port = utils.to_int(raw_address[4:6])
            peers.add((ip, port))

        self.logger.debug("Found the following peers: %s", str(peers))

        return peers

    
    def return_a_peer(self, exclude:List[int]=[]) -> Tuple[str, int]:
        return random.choice([p for p in self.peers if p[1] not in exclude])

    
def temp(b):
    peers = set()
    for raw_address in utils.split_in_chunks(b, 6):
        ip = ipaddress.IPv4Address(raw_address[:4]).exploded
        port = utils.to_int(raw_address[4:6])
        peers.add((ip, port))
    return peers
