import random
from typing import *
from textwrap import wrap #split string in chunks
import enum

try:
    import Fiume.config as config
except:
    import config as config

def bool_to_bitmap(bs: List[bool]):
    bitmap = bytearray()

    for byte_ in range(0, len(bs), 8):
        single_byte = 0
        for i, x in enumerate(bs[byte_:byte_+8]):
            single_byte += int(x) << (7-i)
        bitmap.append(single_byte)

    return bitmap

def to_int(b: bytes):
    return int.from_bytes(b, byteorder="big", signed=False) 
def to_bytes(n: int, length=1):
    return int.to_bytes(n, length=length, byteorder="big")

HANDSHAKE_PREAMBLE = to_bytes(19) + b"BitTorrent protocol"

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

def data_to_bitmap(data: List[bytes]) -> List[bool]:
    bitmap = list()
    for block in data:
        bitmap.append(block not in [None, [], b""])
    return bitmap

def bitmap_to_bool(bs: bytes):
    bool_bitmap = list()
    
    for block in range(0, len(bs), 8):
        for i in range(8):
            bool_bitmap.append(((bs[block] >> (7-i)) & 1) == 1)
    return bool_bitmap

def generate_peer_id(seed=None) -> bytes:
    if seed is not None:
        random.seed(seed)

    return config.CLIENT_INFO + bytes([random.randint(65, 90) for _ in range(12)])
