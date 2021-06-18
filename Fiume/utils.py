import random
import os

from typing import *
from typing.io import *
from pathlib import Path

import enum
import Fiume.config as config

class MasterMex(enum.Enum):
    KILL = 0
    
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
    
