import random
from typing import *
from typing.io import *
import enum
import os

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

def split_in_chunks(l, length):
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

def data_to_bitmap(file: BinaryIO, block_size) -> List[bool]:
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

def determine_size_of(f: BinaryIO):
    old_file_position = f.tell()
    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(old_file_position, os.SEEK_SET)
    return size
