import bencodepy
import hashlib
import ipaddress
import random

import Fiume.utils as utils

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

        self.trackers  = self.__get_trackers()
        
    def __get_trackers(self):
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
        self.my_tracker: str = None
        self.peers: list = self.decode_peers(self._request_peers_to_tracker())
        
    def _request_peers_to_tracker(self):
        # TODO: far diventare questo un multiprocessing
        
        for tracker_url in self.metainfo.trackers:
            print(f"Trying: {tracker_url}")

            try:
                r = requests.get(
                    tracker_url,
                    params={
                        "info_hash": self.metainfo.info_hash,
                        "peer_id": b"-PO2020-918277361230", # TODO: cambiare
                        "port": 6888,
                        "uploaded": "0",
                        "downloaded": "0",
                        "left": str(self.metainfo[b"info"][b"length"]),
                        "compact": "1",
                        "event": "started",
                    },
                    timeout=3.0
                )

                if r.status_code == 200:
                    break

                # Vuol dire che ho ottenuto response != 200
                breakpoint()
                _ = 0
                    
            except Timeout:
                print(f"{tracker_url} has time-outed")
                continue
            except Exception as e:
                print(e)
                continue

        else:
            # TODO print tutti i trackers
            raise Exception("Couldn't find a working tracker")
 
        self.my_tracker = tracker_url

        return r.content

    def decode_peers(self, tracker_response: bytes):
        response_bencode = bencodepy.decode(tracker_response)

        peers = list()
        for raw_address in utils.split_in_chunks(response_bencode[b"peers"], 6):
            ip = ipaddress.IPv4Address(raw_address[:4]).exploded
            port = utils.to_int(raw_address[4:6])
            peers.append((ip, port))

        return peers

    def return_a_peer(self) -> (str, int):
        return random.choice(self.peers)


# temp = "/home/groucho/interscambio/fittone/Fiume/tests/torrent_examples/debian.torrent"
temp = "/home/groucho/interscambio/fittone/Fiume/tests/torrent_examples/Un gioco di specchi.mp4.torrent"

with open(temp, "rb") as f:
    metainfo = MetaInfo(bencodepy.decode(f.read()))

tm = TrackerManager(metainfo)

