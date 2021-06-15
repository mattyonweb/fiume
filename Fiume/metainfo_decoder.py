import bencodepy
import hashlib
import ipaddress
import random
from math import log2

from typing import *
    
try:
    import Fiume.config as config
    import Fiume.utils as utils
except:
    import utils as utils
    import config as config
    
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

        self.trackers  = self.__gather_trackers()

        self.piece_size = self[b"info"][b"piece length"]
        self.block_size = 16384 #16kb, standard
        self.total_size = self[b"info"][b"length"]

        self.num_pieces = len(self.pieces_hash)
        
    def __gather_trackers(self):
        """ Unites all possible trackers in a single list, useless """
        trackers = [self[b"announce"]]
        
        if b"announce-list" in self:
            trackers += [l[0] for l in self[b"announce-list"]]

        return trackers

    
###################

import requests
from requests.exceptions import Timeout


class TrackerManager:
    def __init__(self, metainfo: MetaInfo):
        self.metainfo: MetaInfo = metainfo
        self.working_trackers: List[str] = list()

        responses = [r for r in self._request_peers_to_tracker() if r[:2] == b"d8"]
        
        self.peers: list = self.decode_peers(responses)
        self.peers = [x for x in self.peers if x[1] != 6889]


    def _request_peers_to_tracker(self):
        import multiprocessing as mp
        
        pool = mp.Pool(16*mp.cpu_count())
        results = pool.map(self._parallel_tracker_retriever, self.metainfo.trackers)
        pool.close()

        responses = list()
        for t, binary in results:
            if t is not None and binary is not None:
                self.working_trackers.append(t)
                responses.append(binary)

        return responses
            
        
    def _parallel_tracker_retriever(self, tracker_url):
        print(f"Trying: {tracker_url}")

        try:
            r = requests.get(
                tracker_url,
                params={
                    "info_hash": self.metainfo.info_hash,
                    "peer_id": b"-PO2020-918277361230", # TODO: cambiare
                    "port": 6889, # TODO: perché 6888?????
                    "uploaded": "0",
                    "downloaded": "0",
                    "left": str(self.metainfo[b"info"][b"length"]),
                    "compact": "1",
                    "event": "started",
                },
                timeout=2.0
            )

            if r.status_code == 200:
                return (tracker_url, r.content)

        except Timeout:
            print(f"{tracker_url} has time-outed")
        except Exception as e:
            print(e)

        return (None, None)
 
    def decode_peers(self, tracker_responses: List[bytes]):
        peers = set()
        
        for tracker_response in tracker_responses: 
            response_bencode = bencodepy.decode(tracker_response)

            if not b"peers" in response_bencode:
                print("No peers in answer")
                continue
            
            for raw_address in utils.split_in_chunks(response_bencode[b"peers"], 6):
                ip = ipaddress.IPv4Address(raw_address[:4]).exploded
                port = utils.to_int(raw_address[4:6])
                peers.add((ip, port))

        return list(peers)

    def return_a_peer(self) -> (str, int):
        return random.choice(self.peers)


if __name__ == "__main__":
    # temp = "/home/groucho/interscambio/fittone/Fiume/tests/torrent_examples/debian.torrent"
    temp = "/home/groucho/interscambio/fittone/Fiume/tests/torrent_examples/Un gioco di specchi.mp4.torrent"
    
    with open(temp, "rb") as f:
        metainfo = MetaInfo(bencodepy.decode(f.read()))
        
    tm = TrackerManager(metainfo)

    
