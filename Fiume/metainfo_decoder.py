import bencodepy
import hashlib
import ipaddress

import Fiume.utils as utils

class MetaInfo(dict):
    """ NB: solo per Single File Mode """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pieces_hash = utils.split_in_chunks(self[b"info"][b"pieces"], 20)

        sha = hashlib.sha1()
        sha.update(bencodepy.encode(self[b"info"]))
        self.info_hash = sha.digest()

###################

import requests
from requests.exceptions import Timeout

temp = "/home/groucho/interscambio/fittone/Fiume/tests/torrent_examples/debian.torrent"
temp = "/home/groucho/interscambio/fittone/Fiume/tests/torrent_examples/Un gioco di specchi.mp4.torrent"

d = dict()
with open(temp, "rb") as f:
    d = MetaInfo(bencodepy.decode(f.read()))

d_info = d[b"info"]


trackers = [d[b"announce"]]
if b"announce-list" in d:
    trackers += [l[0] for l in d[b"announce-list"]]

for tracker_url in trackers:
    print(f"Trying: {tracker_url}")
    
    try:
        r = requests.get(
            tracker_url,
            params={
                "info_hash": d.info_hash,
                "peer_id": b"-PO2020-918277361230",
                "port": 6888,
                "uploaded": "0",
                "downloaded": "0",
                "left": str(d_info[b"length"]),
                "compact": "1",
                "event": "started",
            },
            timeout=3.0
        )

        break
    
    except Timeout:
        print(f"{tracker_url} has time-outed")
        continue
    except Exception as e:
        print(e)
        continue

if r.status_code != 200:
    breakpoint()
    raise Exception("status code: " + str(r.status_code))


response_bencode = bencodepy.decode(r.content)

peers = list()
for raw_address in utils.split_in_chunks(response_bencode[b"peers"], 6):
    ip = ipaddress.IPv4Address(raw_address[:4]).exploded
    port = utils.to_int(raw_address[4:6])
    peers.append((ip, port))
    
