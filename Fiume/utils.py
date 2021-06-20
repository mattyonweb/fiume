import random
import os

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
    reason: str = ""

@dataclass
class M_SCHEDULE(MasterMex):
    pieces_index: List[int]

@dataclass
class M_DESCHEDULE(MasterMex):
    pieces_index: List[int]

@dataclass
class M_PEER_HAS(MasterMex):
    pieces_index: List[int]
    sender: Tuple[str, int]
    # How many new pieces ought the master schedule for the PeerManager, if any
    schedule_new_pieces: int = 10
    
@dataclass
class M_NEW_HAVE(MasterMex):
    """ Master sends this to all the peers when it receives a new, completed block. """
    piece_index: int

@dataclass
class M_PEER_REQUEST(MasterMex):
    """ Master sends this to all the peers when it receives a new, completed block. """
    piece_index: int
    sender: Tuple[str, int]
    
@dataclass
class M_PIECE(MasterMex):
    """ When client finishes downloading a piece, then it sends this message
    to the master, who will proceed to write it to file. """
    piece_index: int
    data: bytes
    sender: Tuple[str, int]
    # How many new pieces ought the master schedule for the PeerManager
    schedule_new_pieces: int = 1

@dataclass
class M_DISCONNECTED(MasterMex):
    sender: Tuple[str, int]

@dataclass
class M_ERROR(MasterMex):
    on_service: MasterMex = None
    comment: str = None
    
@dataclass
class M_DEBUG(MasterMex):
    data: Any
    sender: Tuple[str, int] = None

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
    
