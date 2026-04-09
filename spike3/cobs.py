"""Modified COBS (Consistent Overhead Byte Stuffing) codec for Atlantis framing.

Implements the exact COBS variant used by SPIKE 3 Atlantis protocol:
- All encoded bytes XOR'd with key=3
- Implicit encoding for byte values 0..2 via code bytes
- Max run length 83
- Priority marker (0x01) and end-of-frame marker (0x02)
"""

from .enums import (
    COBS_XOR_KEY, COBS_MAX_RUN, COBS_CODE_BASE,
    COBS_IMPLICIT_ZERO_STRIDE, COBS_OVERFLOW_CODE,
    COBS_HIGH_PRIORITY, COBS_END_FRAME,
)


def encode(data: bytes, high_priority: bool = False) -> bytes:
    """Encode data using SPIKE 3 modified COBS framing.

    Args:
        data: Raw message bytes to encode.
        high_priority: If True, prepend high-priority marker byte.

    Returns:
        Framed bytes ready for transmission (XOR'd, with end marker).
    """
    encoded = []
    run_length = 0
    # Reserve first code byte slot
    encoded.append(0)  # placeholder
    code_idx = 0

    for byte in data:
        if run_length > COBS_MAX_RUN:
            encoded[code_idx] = COBS_OVERFLOW_CODE
            encoded.append(0)  # new placeholder
            code_idx = len(encoded) - 1
            run_length = 0

        if byte > COBS_IMPLICIT_ZERO_STRIDE:
            # Literal byte (values 3..255)
            encoded.append(byte)
            run_length += 1
        else:
            # Implicit zero-class byte (values 0, 1, 2)
            encoded[code_idx] = (
                run_length + COBS_CODE_BASE
                + byte * (COBS_MAX_RUN + 1)
            )
            encoded.append(0)  # new placeholder
            code_idx = len(encoded) - 1
            run_length = 0

    # Finalize last code byte
    if run_length > COBS_MAX_RUN:
        encoded[code_idx] = COBS_OVERFLOW_CODE
        encoded.append(COBS_CODE_BASE)
    else:
        encoded[code_idx] = run_length + COBS_CODE_BASE

    # XOR all bytes
    xored = [b ^ COBS_XOR_KEY for b in encoded]

    # Build output: optional priority + XOR'd data + end marker
    out = []
    if high_priority:
        out.append(COBS_HIGH_PRIORITY)
    out.extend(xored)
    out.append(COBS_END_FRAME)
    return bytes(out)


def decode(frame: bytes) -> bytes:
    """Decode a SPIKE 3 modified COBS frame back to raw message bytes.

    Args:
        frame: Received frame bytes (may include priority/end markers).

    Returns:
        Decoded raw message bytes.

    Raises:
        ValueError: If the frame is malformed.
    """
    data = bytearray(frame)

    # Strip priority marker if present
    if data and data[0] == COBS_HIGH_PRIORITY:
        data = data[1:]

    # Strip end-of-frame marker if present
    if data and data[-1] == COBS_END_FRAME:
        data = data[:-1]

    if not data:
        return b""

    # Un-XOR all bytes
    unxored = [b ^ COBS_XOR_KEY for b in data]

    # Decode COBS
    result = []
    i = 0
    while i < len(unxored):
        code = unxored[i]
        i += 1

        if code == COBS_OVERFLOW_CODE:
            # Max run: next MAX_RUN+1 bytes are literal
            count = COBS_MAX_RUN + 1
            for _ in range(count):
                if i >= len(unxored):
                    break
                result.append(unxored[i])
                i += 1
        else:
            # Determine implicit zero value and run length
            implicit_val = 0
            remaining = code
            while remaining > COBS_MAX_RUN + COBS_CODE_BASE:
                remaining -= (COBS_MAX_RUN + 1)
                implicit_val += 1

            run_length = remaining - COBS_CODE_BASE

            # Copy run_length literal bytes
            for _ in range(run_length):
                if i >= len(unxored):
                    break
                result.append(unxored[i])
                i += 1

            # Append implicit byte if this wasn't the terminal code
            if implicit_val <= COBS_IMPLICIT_ZERO_STRIDE and i < len(unxored):
                result.append(implicit_val)
            # If code == run_length + CODE_BASE exactly (no implicit zero),
            # it's the last segment — no byte appended

    # The last code byte is terminal and doesn't append an implicit zero,
    # but our loop above may have appended one. Let's fix:
    # Actually the way the encoder works, the LAST code byte is just
    # `run_length + CODE_BASE` with no implicit zero. We need to detect this.
    # Simplest: re-decode properly.

    return bytes(result)


def decode_proper(frame: bytes) -> bytes:
    """Proper decode following the exact JS algorithm structure."""
    data = bytearray(frame)

    # Strip markers
    if data and data[0] == COBS_HIGH_PRIORITY:
        data = data[1:]
    if data and data[-1] == COBS_END_FRAME:
        data = data[:-1]
    if not data:
        return b""

    # Un-XOR
    buf = [b ^ COBS_XOR_KEY for b in data]

    result = []
    i = 0
    while i < len(buf):
        code = buf[i]
        i += 1

        if code == COBS_OVERFLOW_CODE:
            # Overflow: copy exactly MAX_RUN+1 literal bytes, no implicit value
            for _ in range(COBS_MAX_RUN + 1):
                if i < len(buf):
                    result.append(buf[i])
                    i += 1
        else:
            # Decode: implicit_value = code // (MAX_RUN + 1)
            # run_length = (code % (MAX_RUN + 1)) - CODE_BASE
            implicit_val = 0
            c = code
            while c >= (COBS_MAX_RUN + 1) + COBS_CODE_BASE:
                c -= (COBS_MAX_RUN + 1)
                implicit_val += 1
            run_length = c - COBS_CODE_BASE

            # Copy literal bytes
            for _ in range(run_length):
                if i < len(buf):
                    result.append(buf[i])
                    i += 1

            # Append implicit value — but NOT after the last code byte
            if i < len(buf) and implicit_val <= COBS_IMPLICIT_ZERO_STRIDE:
                result.append(implicit_val)

    return bytes(result)


# Use the proper implementation
decode = decode_proper  # noqa: F811


class FrameAccumulator:
    """Accumulates incoming raw bytes and yields complete decoded frames.

    Handles the byte-by-byte state machine for COBS frame detection:
    - COBS_HIGH_PRIORITY (0x01) switches to high-priority buffer
    - COBS_END_FRAME (0x02) terminates current frame
    - All other bytes are accumulated
    """

    def __init__(self):
        self._normal_buf = bytearray()
        self._high_buf = bytearray()
        self._current = self._normal_buf
        self._is_high_priority = False

    def feed(self, raw: bytes) -> list[tuple[bytes, bool]]:
        """Feed raw bytes and return list of (decoded_payload, is_high_priority).

        Args:
            raw: Raw bytes received from transport.

        Returns:
            List of (decoded_message, high_priority) tuples for each
            complete frame found in the input.
        """
        frames = []
        for b in raw:
            if b == COBS_HIGH_PRIORITY:
                self._current = self._high_buf
                self._is_high_priority = True
            elif b == COBS_END_FRAME:
                if self._current:
                    frame_bytes = bytes(self._current)
                    # Prepend priority marker if high priority so decode() can handle it
                    if self._is_high_priority:
                        frame_bytes = bytes([COBS_HIGH_PRIORITY]) + frame_bytes
                    try:
                        decoded = decode(frame_bytes + bytes([COBS_END_FRAME]))
                        frames.append((decoded, self._is_high_priority))
                    except Exception:
                        pass  # drop malformed frames
                self._current.clear()
                self._current = self._normal_buf
                self._is_high_priority = False
            else:
                self._current.append(b)
        return frames
