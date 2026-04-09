"""Simulated hub file storage — 20 program slots with upload/download.

Each slot can hold one file (name + data). Supports CRC32 validation.
"""

from __future__ import annotations

import struct
import threading
import zlib
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StoredFile:
    """A file stored in a program slot."""
    filename: str = ""
    data: bytes = b""
    crc32: int = 0
    slot: int = 0


class SlotStorage:
    """Thread-safe storage for hub program slots."""

    def __init__(self, num_slots: int = 20):
        self._lock = threading.Lock()
        self._slots: list[Optional[StoredFile]] = [None] * num_slots
        self._upload_in_progress: Optional[_UploadSession] = None
        self._download_in_progress: Optional[_DownloadSession] = None

    @property
    def num_slots(self) -> int:
        return len(self._slots)

    def has_file(self, slot: int) -> bool:
        with self._lock:
            return 0 <= slot < len(self._slots) and self._slots[slot] is not None

    def get_file(self, slot: int) -> Optional[StoredFile]:
        with self._lock:
            if 0 <= slot < len(self._slots):
                return self._slots[slot]
            return None

    def list_files(self, slot: int = 0) -> list[str]:
        """List filenames in a slot (or all slots if slot=-1)."""
        with self._lock:
            if slot == -1:
                return [f.filename for f in self._slots if f is not None]
            if 0 <= slot < len(self._slots) and self._slots[slot]:
                return [self._slots[slot].filename]
            return []

    def clear_slot(self, slot: int) -> bool:
        with self._lock:
            if 0 <= slot < len(self._slots):
                self._slots[slot] = None
                return True
            return False

    def move_slot(self, from_slot: int, to_slot: int) -> bool:
        with self._lock:
            if (0 <= from_slot < len(self._slots) and
                    0 <= to_slot < len(self._slots)):
                self._slots[to_slot] = self._slots[from_slot]
                self._slots[from_slot] = None
                if self._slots[to_slot]:
                    self._slots[to_slot].slot = to_slot
                return True
            return False

    def delete_path(self, path: str, slot: int = 0) -> bool:
        """Delete a file by name in the given slot."""
        with self._lock:
            if 0 <= slot < len(self._slots):
                f = self._slots[slot]
                if f and (not path or f.filename == path):
                    self._slots[slot] = None
                    return True
            return False

    # ── Upload flow ────────────────────────────────────────────────

    def begin_upload(self, filename: str, slot: int, expected_crc: int) -> bool:
        """Start an upload session."""
        with self._lock:
            if not (0 <= slot < len(self._slots)):
                return False
            self._upload_in_progress = _UploadSession(
                filename=filename, slot=slot, expected_crc=expected_crc
            )
            return True

    def append_chunk(self, running_crc: int, chunk_data: bytes) -> bool:
        """Append a chunk to the active upload. Returns True if CRC matches."""
        with self._lock:
            sess = self._upload_in_progress
            if sess is None:
                return False
            sess.data += chunk_data
            # Verify running CRC
            actual_crc = zlib.crc32(sess.data) & 0xFFFFFFFF
            if running_crc != 0 and running_crc != actual_crc:
                return False  # CRC mismatch
            return True

    def finish_upload(self) -> bool:
        """Complete upload, store the file."""
        with self._lock:
            sess = self._upload_in_progress
            if sess is None:
                return False
            crc = zlib.crc32(sess.data) & 0xFFFFFFFF
            self._slots[sess.slot] = StoredFile(
                filename=sess.filename,
                data=sess.data,
                crc32=crc,
                slot=sess.slot,
            )
            self._upload_in_progress = None
            return True

    def cancel_upload(self):
        with self._lock:
            self._upload_in_progress = None

    # ── Download flow ──────────────────────────────────────────────

    def begin_download(self, filename: str, slot: int) -> Optional[tuple[int, int]]:
        """Start a download session. Returns (status, crc) or None."""
        with self._lock:
            f = self._slots[slot] if 0 <= slot < len(self._slots) else None
            if f is None or (filename and f.filename != filename):
                return None
            self._download_in_progress = _DownloadSession(
                slot=slot, data=f.data, offset=0
            )
            return (0, f.crc32)  # status=ACK, crc

    def read_download_chunk(self, max_size: int = 512) -> Optional[tuple[int, bytes]]:
        """Read next download chunk. Returns (running_crc, data) or None if done."""
        with self._lock:
            sess = self._download_in_progress
            if sess is None:
                return None
            if sess.offset >= len(sess.data):
                self._download_in_progress = None
                return None
            end = min(sess.offset + max_size, len(sess.data))
            chunk = sess.data[sess.offset:end]
            sess.offset = end
            running_crc = zlib.crc32(sess.data[:end]) & 0xFFFFFFFF
            return (running_crc, chunk)

    def cancel_download(self):
        with self._lock:
            self._download_in_progress = None

    # ── Serialization ──────────────────────────────────────────────

    def list_path_response_data(self, path: str, slot: int) -> bytes:
        """Build ListPathResponse payload: u16(len) + NUL-terminated filenames."""
        names = []
        with self._lock:
            for f in self._slots:
                if f is not None:
                    names.append(f.filename)
        if not names:
            return struct.pack("<H", 0)
        name_bytes = b"\x00".join(n.encode("utf-8") for n in names) + b"\x00"
        return struct.pack("<H", len(name_bytes)) + name_bytes


@dataclass
class _UploadSession:
    filename: str
    slot: int
    expected_crc: int
    data: bytes = b""


@dataclass
class _DownloadSession:
    slot: int
    data: bytes
    offset: int = 0
