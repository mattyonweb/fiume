import random
import os
import logging

from typing import *
from typing.io import *
from pathlib import Path
from dataclasses import dataclass

import enum
import Fiume.config as config

Address = Tuple[str, int]

#######################################à

class MasterMex:
    pass

@dataclass
class M_KILL(MasterMex):
    """ 
    - Master -> PM: PM must close the connection.
    - ? -> Master: Master must close (only debug or user request)
    """  
    reason: str = ""

@dataclass
class M_SCHEDULE(MasterMex):
    """
    Master -> PM. 
    
    Assigns a list of pieces to the PM, who will be the only PM 
    authorized to request and download them.
    """
    pieces_index: List[int]

@dataclass
class M_DESCHEDULE(MasterMex):
    pieces_index: List[int]

    
@dataclass
class M_PEER_HAS(MasterMex):
    """
    PM -> Master.

    Used at the beginning of a connections, as a BITFIELD message.
    Informs the master about the pieces that the peer has.
    Master answers by scheduling (a max. of) N pieces to that peer.
    """
    pieces_index: List[int]
    sender: Tuple[str, int]
    # How many new pieces ought the master schedule for the PeerManager, if any
    schedule_new_pieces: int = 10


@dataclass
class M_OUR_BITMAP(MasterMex):
    """
    Master -> PM.

    Sent at the very beginning of a Master/PM relationship.
    """
    bitmap: List[bool]
    
    
@dataclass
class M_NEW_HAVE(MasterMex):
    """ 
    Master -> PMs.
    Master sends this to all the peers when it receives a new, completed block.
    Used to update PMs bitmaps, keeping it synchronized with master's.
    """
    piece_index: int


@dataclass
class M_PEER_REQUEST(MasterMex):
    """ 
    PM -> Master.

    When a peer requests a piece, PM sends to Master this message. 
    Master's answer will be a M_PIECE.
    """
    piece_index: int
    sender: Tuple[str, int]

    
@dataclass
class M_PIECE(MasterMex):
    """ 
    PM -> Master or Master -> PM.

    When a PM finishes downloading a piece, it sends this message to the
    master, who will proceed to write it to file. 
    
    This message is also used when the Master answers to a M_PEER_REQUEST.
    """
    piece_index: int
    data: bytes
    sender: Tuple[str, int]
    # How many new pieces ought the master schedule for the PeerManager
    schedule_new_pieces: int = 1

    
@dataclass
class M_DISCONNECTED(MasterMex):
    """
    PM -> Master.

    Used when PM has disconnected gracefully (and not?).
    """
    sender: Tuple[str, int]

    
@dataclass
class M_ERROR(MasterMex):
    on_service: Union[MasterMex, None] = None
    comment: str = ""

    
@dataclass
class M_DEBUG(MasterMex):
    data: Any
    sender: Tuple[str, int]

###################################à
    
def bool_to_bitmap(bs: List[bool]) -> bytes:
    bitmap = bytearray()

    for byte_ in range(0, len(bs), 8):
        single_byte = 0
        for i, x in enumerate(bs[byte_:byte_+8]):
            single_byte += int(x) << (7-i)
        bitmap.append(single_byte)

    return bytes(bitmap)

def to_int(b: bytes) -> int:
    return int.from_bytes(b, byteorder="big", signed=False) 
def to_bytes(n: int, length=1) -> bytes:
    return int.to_bytes(n, length=length, byteorder="big")

HANDSHAKE_PREAMBLE = to_bytes(19) + b"BitTorrent protocol"

def split_in_chunks(l: List, length: int) -> List:
    out=list()
    for i in range(0, len(l), length):
        out.append(l[i:i+length])
    return out

def generate_random_data(total_length=2048, block_size=256) -> List[bytes]:
    bs = "".join([chr(random.randint(65, 90)) for _ in range(total_length)])

    out=list()
    for i in range(0, len(bs), block_size):
        out.append(bytes(bs[i:i+block_size], "ascii"))
    return out

def mask_data(data: List[bytes], seed: int, padding=b"") -> List[bytes]:
    random.seed(seed)
    
    data_out = list()
    for block in data:
        if random.random() < 0.5:
            data_out.append(padding)
        else:
            data_out.append(block)
    return data_out

def get_bitmap_file(download_fpath: Path) -> Path:
    return config.BITMAPS_DIR / download_fpath.name

def update_bitmap_file(download_fpath: Path, bitmap: List[bool]):
    with open(get_bitmap_file(download_fpath), "w") as f:
        f.write("".join([str(int(x)) for x in bitmap]))
        
def empty_bitmap(num_pieces) -> List[bool]:
    return [False for _ in range(num_pieces)]

def data_to_bitmap(download_fpath: Path, num_pieces=None) -> List[bool]:
    bitmap_fpath = get_bitmap_file(download_fpath)

    # Se il file bitmap relativo al torrent NON esiste, allora crealo
    # inserendo tutti 0. Idem se esiste il bitmap file ma non esiste
    # il file scaricato (magari perché è stato eliminato)
    print("BITMAP:", bitmap_fpath)
    
    if ((not bitmap_fpath.exists()) or
        (bitmap_fpath.exists() and not download_fpath.exists())):

        print("AOOOOOOOOOOOOOOOOOOo", download_fpath)
        assert num_pieces is not None
        bitmap_fpath.touch()
        
        with open(bitmap_fpath, "w") as f:
            f.write("0"*num_pieces)

        return empty_bitmap(num_pieces)

    with open(bitmap_fpath, "r") as f:
        return [bool(int(x)) for x in f.read().strip()]

def bitmap_to_bool(bs: bytes, num_pieces: int) -> List[bool]:
    bool_bitmap = list()

    for b in bs:
        for i in range(8):
            bool_bitmap.append(bool(b >> (7-i) & 1))

    return bool_bitmap[:num_pieces]


def generate_peer_id(seed=None) -> bytes:
    if seed is not None:
        random.seed(seed)

    return config.CLIENT_INFO + bytes([random.randint(65, 90) for _ in range(12)])

def determine_size_of(f: BinaryIO) -> int:
    old_file_position = f.tell()
    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(old_file_position, os.SEEK_SET)
    return size

def sha1(data: bytes) -> bytes:
    import hashlib

    sha = hashlib.sha1()
    sha.update(data)
    return sha.digest()

def already_started_download(download_fpath: Path):
    """ 
    Heuristic, not necessarily correct!
    """
    
    bitmap_fpath = get_bitmap_file(download_fpath)

    if not download_fpath.exists():
        return False
    if not bitmap_fpath.exists(): # TODO: ????
        return False
    
    return True

def already_completed_download(download_fpath: Path):
    """ 
    Heuristic, not necessarily correct!
    """
    
    bitmap_fpath = get_bitmap_file(download_fpath)

    if not bitmap_fpath.exists():
        return False
    
    with open(bitmap_fpath, "r") as f:
        return all([bool(x) for x in f.read().strip()])
    

def get_external_ip():
    import requests

    return requests.get("https://api.ipify.org").text

def int_to_loglevel(n):
    if n == 0:
        return logging.WARNING
    if n == 1:
        return logging.INFO

    return logging.ERROR
