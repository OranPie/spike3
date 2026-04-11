"""CRC-32 utility for SPIKE 3 Atlantis protocol.

Matches the official LEGO reference implementation (spike-prime-docs/examples/python/crc.py):
- Standard reflected CRC-32 (same as zlib.crc32 / binascii.crc32)
- Data padded to 4-byte alignment before computation
- Running CRC: crc(chunk2, seed=crc(chunk1))
"""

import binascii


def crc32(data: bytes, seed: int = 0) -> int:
    """Compute CRC-32 with 4-byte alignment padding.

    Args:
        data: Input bytes.
        seed: Running CRC from previous chunk (0 for first chunk).

    Returns:
        Unsigned 32-bit CRC value.
    """
    # Pad to multiple of 4 bytes (official LEGO requirement)
    remainder = len(data) % 4
    if remainder:
        data = data + b"\x00" * (4 - remainder)
    return binascii.crc32(data, seed) & 0xFFFFFFFF


def file_crc(data: bytes) -> int:
    """Compute the full-file CRC for upload verification."""
    return crc32(data)
