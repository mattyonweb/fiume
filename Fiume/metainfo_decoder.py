import bencodepy
import hashlib
import ipaddress
import random
import requests
import multiprocessing as mp

from requests.exceptions import Timeout
from math import log2
from typing import *
    
# try:
import Fiume.config as config
import Fiume.utils as utils
# except:
#     import utils as utils
#     import config as config

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
        self.logger.debug("__init__")

        self.options = options
        self.bitmap_file = utils.get_bitmap_file(self.options["output_file"])
        
        self.metainfo: MetaInfo = metainfo
        self.working_trackers: List[str] = list()

        self.peers: list = self.decode_peers(self.request_peers_to_trackers())
        self.peers = [x for x in self.peers if x[1] not in [6889, 50146]] # TODO sbagliato?

        
    def tell_all_trackers(self, params) -> List[Optional[Tuple[Url, requests.Response]]]:
        """ 
        Tells something to all trackers in the .torrent file.
        """
        
        pool    = mp.Pool(16 * mp.cpu_count())
        results = pool.map(
            lambda url: self.__tell_tracker(url, params),
            self.metainfo.trackers
        )
        pool.close()
        return results
    
    def __tell_tracker(self, url, params) -> Optional[Tuple[Url, requests.Response]]:
        """ 
        Single iterator for tell_all_trackers.
        """
        
        try:
            return (url, requests.get(url, params=params, timeout=2.0))
        except Timeout:
            self.logger.debug("%s has time-outed", url)
            return None
        except Exception as e:
            self.logger.debug("%s has failed for some generic reason", url) 
            return None

        
    def request_peers_to_trackers(self) -> List[bytes]:        
        results = self.tell_all_trackers({
            "info_hash": self.metainfo.info_hash,
            "peer_id": b"-PO2020-918277361230", # TODO: cambiare
            "port": 50146, 
            "uploaded": "0",
            "downloaded": "0",
            "left": str(self.metainfo[b"info"][b"length"]),
            "compact": "1",
            "event": "started",
            "ip": "78.14.24.41", # TODO: non va bene
        })

        raw_responses = list()
        
        for r in results:
            if r is None:
                continue
            
            tracker_url, __response = r
            response_bytes = __response.content
            
            if response_bytes[:2] != b"d8":
                self.logger.debug("%s has returned a non-bencode object", tracker_url)
                continue
            
            self.working_trackers.append(tracker_url)
            raw_responses.append(response_bytes)

        return raw_responses

    
    def notify_completion_to_trackers(self):
        self.tell_all_trackers({
            "info_hash": self.metainfo.info_hash,
            "peer_id": b"-PO2020-918277361230", # TODO: cambiare
            "port": 50146,
            "uploaded": "0", #TODO
            "downloaded": str(self.metainfo.total_size),
            "left": "0",
            "ip": "78.14.24.41", # TODO: non va bene
            "event": "completed",
        })

 
    def decode_peers(self, tracker_responses: List[bytes]) -> List[Tuple[str, int]]:
        """ 
        From a bencode bytestring to the list of peers' (ip, port).
        """
        
        peers = set()
        
        for tracker_response in tracker_responses: 
            response_bencode = bencodepy.decode(tracker_response)

            if not b"peers" in response_bencode:
                self.logger.debug("No peers in bencode answer from tracker")
                continue
            
            for raw_address in utils.split_in_chunks(response_bencode[b"peers"], 6):
                ip = ipaddress.IPv4Address(raw_address[:4]).exploded
                port = utils.to_int(raw_address[4:6])
                peers.add((ip, port))

        self.logger.debug("Found the following peers:")
        for (ip, port) in peers:
            self.logger.debug("%s:%d", ip, port)
            
        return list(peers)

    def return_a_peer(self) -> Tuple[str, int]:
        return random.choice(self.peers)


if __name__ == "__main__":
    temp = "/home/groucho/interscambio/fittone/Fiume/tests/torrent_examples/Un gioco di specchi.mp4.torrent"
    
    with open(temp, "rb") as f:
        metainfo = MetaInfo(bencodepy.decode(f.read()))
        
    tm = TrackerManager(metainfo)

    
