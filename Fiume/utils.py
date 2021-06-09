import random
from typing import *
from textwrap import wrap #split string in chunks

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
    return int.to_bytes(mexType.value, length=length, byteorder="big")

from itertools import zip_longest

def grouper(iterable, n, fillvalue=None):
    "Collect data into fixed-length chunks or blocks "
    # grouper('ABCDEFG', 3, 'x') --> ABC DEF Gxx"
    args = [iter(iterable)] * n
    return zip_longest(*args, fillvalue=fillvalue)


def generate_random_data(total_length=2048, block_size=256):
    bs = bytes(random.randint(65, 90) for _ in range(total_length))
    return grouper(bs, block_size, bytes(1))

def data_to_bitmap(data: List[bytes]) -> List[bool]:
    bitmap = list()
    for block in data:
        bitmap.append(block is not None)
    return bitmap
