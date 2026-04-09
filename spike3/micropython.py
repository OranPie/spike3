"""MicroPython JSON-RPC protocol for legacy SPIKE hubs.

Used by FlipperPT (SPIKE Prime v1) and Gecko (SPIKE Essential)
over USB serial and Bluetooth Classic.

Wire format: JSON terminated by \\r (carriage return).
  Request:      {"i": id, "m": method, "p": params}\\r
  Response:     {"i": id, "r": result}\\r
  Error:        {"i": id, "e": error}\\r
  Notification: {"m": notif_id, "p": params}\\r
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class RpcRequest:
    """JSON-RPC request (host → hub)."""
    id: int
    method: str
    params: Any = None

    def to_bytes(self) -> bytes:
        obj = {"i": self.id, "m": self.method, "p": self.params}
        return json.dumps(obj, separators=(",", ":")).encode("utf-8") + b"\r"


@dataclass
class RpcResponse:
    """JSON-RPC response (hub → host)."""
    id: int
    result: Any = None


@dataclass
class RpcError:
    """JSON-RPC error response (hub → host)."""
    id: int
    error: Any = None


@dataclass
class RpcNotification:
    """JSON-RPC notification (hub → host, unsolicited)."""
    method: int
    params: Any = None


def parse_message(data: bytes) -> Optional[RpcRequest | RpcResponse | RpcError | RpcNotification]:
    """Parse a JSON-RPC message from raw bytes.

    Args:
        data: Raw bytes (may include trailing \\r).

    Returns:
        Parsed message object, or None if unparseable.
    """
    text = data.strip(b"\r\n\x04").decode("utf-8", errors="replace").strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None

    if not isinstance(obj, dict):
        return None

    has_i = "i" in obj
    has_m = "m" in obj
    has_p = "p" in obj
    has_r = "r" in obj
    has_e = "e" in obj

    if has_i and has_m and has_p:
        return RpcRequest(id=obj["i"], method=obj["m"], params=obj["p"])
    elif has_i and has_r:
        return RpcResponse(id=obj["i"], result=obj["r"])
    elif has_i and has_e:
        return RpcError(id=obj["i"], error=obj["e"])
    elif has_m and has_p and not has_i:
        return RpcNotification(method=obj["m"], params=obj["p"])
    return None


class MessageAccumulator:
    """Accumulates serial bytes and yields complete JSON-RPC messages.

    Messages are delimited by \\r or \\x04 (Ctrl-D).
    """

    def __init__(self):
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[RpcRequest | RpcResponse | RpcError | RpcNotification]:
        """Feed raw bytes and return list of parsed messages."""
        results = []
        self._buf.extend(data)

        while True:
            # Find delimiter
            cr_idx = self._buf.find(b"\r")
            eof_idx = self._buf.find(b"\x04")

            if cr_idx < 0 and eof_idx < 0:
                break

            # Pick the earliest delimiter
            if cr_idx < 0:
                idx = eof_idx
            elif eof_idx < 0:
                idx = cr_idx
            else:
                idx = min(cr_idx, eof_idx)

            chunk = bytes(self._buf[:idx])
            self._buf = self._buf[idx + 1:]

            msg = parse_message(chunk)
            if msg is not None:
                results.append(msg)

        return results
