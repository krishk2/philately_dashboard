"""Perceptual image hashing (dHash), written from scratch on top of Pillow.

A dHash turns an image into a 64-bit fingerprint that survives resizing, recompression and
small colour changes:

1. Crop a margin off every side (see BORDER_CROP).
2. Convert to greyscale and shrink to 9x8 pixels. This throws away detail and noise.
3. For each row, compare each pixel with its right-hand neighbour (8 comparisons x 8 rows).
4. Each comparison is one bit: 1 if the left pixel is brighter.

Two images are "the same picture" when the Hamming distance between their hashes (the number
of differing bits) is small. Identical images score 0; unrelated images score around 32.

Why crop: stamps share a perforated white border and frame, which dominate a 9x8 thumbnail.
Measured on 39 generated stamps (741 pairs), the closest pair of *different* stamps was only
3 bits apart without cropping and 13 bits apart with a 12% crop, while resized, recompressed
and brightened copies stayed at 0. Hashing just the central design makes the hash about the
stamp, not about the fact that it is a stamp.
"""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

from PIL import Image

HASH_SIZE = 8
HASH_BITS = HASH_SIZE * HASH_SIZE
BORDER_CROP = 0.12  # fraction trimmed from each side before hashing


def dhash(source: str | Path | BinaryIO | Image.Image) -> str:
    """Return the 64-bit difference hash of an image as a 16-character hex string."""
    if isinstance(source, Image.Image):
        image = source
    else:
        with Image.open(source) as opened:
            image = opened.copy()

    width, height = image.size
    dx, dy = int(width * BORDER_CROP), int(height * BORDER_CROP)
    image = image.crop((dx, dy, width - dx, height - dy))

    small = image.convert("L").resize((HASH_SIZE + 1, HASH_SIZE), Image.Resampling.LANCZOS)
    pixels = small.tobytes()  # one byte per pixel in "L" mode

    value = 0
    for row in range(HASH_SIZE):
        offset = row * (HASH_SIZE + 1)
        for col in range(HASH_SIZE):
            left = pixels[offset + col]
            right = pixels[offset + col + 1]
            value = (value << 1) | (1 if left > right else 0)
    return f"{value:016x}"


def hamming_distance(hash_a: str, hash_b: str) -> int:
    """Number of bits that differ between two hex hashes."""
    if len(hash_a) != len(hash_b):
        raise ValueError("hashes must be the same length")
    return (int(hash_a, 16) ^ int(hash_b, 16)).bit_count()


def similarity(hash_a: str, hash_b: str) -> float:
    """Similarity in [0, 1], where 1 means identical hashes."""
    return 1 - hamming_distance(hash_a, hash_b) / HASH_BITS
