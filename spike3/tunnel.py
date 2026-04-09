"""spike3.tunnel — Tunnel message layer for SPIKE 3 hub.

The Atlantis TunnelMessage (msg_id=50) carries bidirectional data between
host and hub.  For SPIKE 3 (Flipper), this carries either:

  1. **JSON-RPC text** — `scratch.*` commands and MicroPython responses, as
     NUL-terminated or CR-terminated JSON strings.
  2. **Binary struct data** — tunnel commands with packed struct payloads
     (motor feedback, sensor subscriptions, etc.).

The hub-side MicroPython runtime processes these messages via
``_tunnel.callback(data)`` and sends responses via ``_tunnel.send(data)``.

Usage::

    from spike3 import Hub
    from spike3.tunnel import scratch_request

    hub = Hub.connect_usb('COM3')
    # Send a motor start command via tunnel
    hub.send_tunnel(scratch_request("scratch.motor_start",
                                    port=0, speed=75, stall=True))
"""

from __future__ import annotations
import json
import struct
from dataclasses import dataclass, field
from typing import Any, Optional


# ── JSON-RPC helpers (scratch.* commands via tunnel) ───────────────────

def scratch_request(method: str, _id: Optional[str] = None, **params) -> bytes:
    """Build a JSON-RPC request for a scratch.* command.

    These are sent through the Atlantis TunnelMessage to the hub's
    MicroPython runtime.

    Args:
        method: RPC method name (e.g. 'scratch.motor_start').
        _id: Optional message ID (4-char hex).  Auto-generated if None.
        **params: Method parameters.

    Returns:
        UTF-8 encoded JSON bytes with CR terminator.
    """
    import secrets
    if _id is None:
        _id = secrets.token_hex(2)
    msg = {"i": _id, "m": method, "p": params}
    return (json.dumps(msg, separators=(",", ":")) + "\r").encode("utf-8")


def scratch_notification(method: str, **params) -> bytes:
    """Build a JSON-RPC notification (no response expected)."""
    msg = {"m": method, "p": params}
    return (json.dumps(msg, separators=(",", ":")) + "\r").encode("utf-8")


def parse_tunnel_json(data: bytes) -> Optional[dict]:
    """Try to parse tunnel data as JSON-RPC message.

    Returns parsed dict or None if data is not valid JSON.
    """
    try:
        text = data.rstrip(b"\r\n\x00").decode("utf-8", errors="replace")
        if text.startswith("{"):
            return json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    return None


def is_request(msg: dict) -> bool:
    return "i" in msg and "m" in msg and "p" in msg


def is_response(msg: dict) -> bool:
    return "i" in msg and "r" in msg


def is_error(msg: dict) -> bool:
    return "i" in msg and "e" in msg


def is_notification(msg: dict) -> bool:
    return "i" not in msg and "m" in msg and "p" in msg


# ── Scratch command builders ───────────────────────────────────────────

def motor_start(port: int, speed: int, stall: bool = True,
                acceleration: int = 10000, deceleration: int = 10000) -> bytes:
    """Start a motor at the given speed.

    Args:
        port: Port number (0=A, 1=B, ..., 5=F).
        speed: Speed in range [-100, 100].
        stall: Whether stall detection is active.
        acceleration: Acceleration rate (default 10000 = instant).
        deceleration: Deceleration rate (default 10000 = instant).
    """
    return scratch_request("scratch.motor_start",
                           port=port, speed=speed, stall=stall,
                           acceleration=acceleration,
                           deceleration=deceleration)


def motor_stop(port: int, stop: int = 1) -> bytes:
    """Stop a motor.

    Args:
        port: Port number.
        stop: Stop mode — 0=float, 1=brake, 2=hold.
    """
    return scratch_request("scratch.motor_stop", port=port, stop=stop)


def motor_run_for_degrees(port: int, speed: int, degrees: int,
                          stall: bool = True, stop: int = 1,
                          acceleration: int = 10000,
                          deceleration: int = 10000) -> bytes:
    """Run motor for a number of degrees.

    Args:
        port: Port number.
        speed: Speed [-100, 100].
        degrees: Degrees to rotate (negative = reverse).
        stall: Stall detection.
        stop: Stop mode after completion.
    """
    return scratch_request("scratch.motor_run_for_degrees",
                           port=port, speed=speed, degrees=degrees,
                           stall=stall, stop=stop,
                           acceleration=acceleration,
                           deceleration=deceleration)


def motor_run_timed(port: int, speed: int, time_ms: int,
                    stall: bool = True, stop: int = 1,
                    acceleration: int = 10000,
                    deceleration: int = 10000) -> bytes:
    """Run motor for a specified time.

    Args:
        port: Port number.
        speed: Speed [-100, 100].
        time_ms: Duration in milliseconds (max 60000).
        stall: Stall detection.
        stop: Stop mode after completion.
    """
    return scratch_request("scratch.motor_run_timed",
                           port=port, speed=speed, time=time_ms,
                           stall=stall, stop=stop,
                           acceleration=acceleration,
                           deceleration=deceleration)


def motor_go_to_position(port: int, speed: int, position: int,
                         direction: str = "shortest",
                         stall: bool = True, stop: int = 1) -> bytes:
    """Move motor to an absolute position.

    Args:
        port: Port number.
        speed: Speed [-100, 100].
        position: Target position in degrees.
        direction: 'shortest', 'clockwise', or 'counterclockwise'.
    """
    return scratch_request("scratch.motor_go_direction_to_position",
                           port=port, speed=speed, position=position,
                           direction=direction, stall=stall, stop=stop)


def motor_set_position(port: int, offset: int) -> bytes:
    """Set motor position offset (reset encoder).

    Args:
        port: Port number.
        offset: New position value in degrees.
    """
    return scratch_request("scratch.motor_set_position",
                           port=port, offset=offset)


def display_image(image: str) -> bytes:
    """Display an image on the 5×5 LED matrix.

    Args:
        image: 25-character string of brightness values.
               Each char is '0'-'9' representing brightness level.
               Row-major order (top-left to bottom-right).
               Example: '9909999099000990099009900' = heart
    """
    return scratch_request("scratch.display_image", image=image)


def display_image_for(image: str, duration_ms: int) -> bytes:
    """Display an image for a specified time then clear.

    Args:
        image: 25-char brightness string (see display_image).
        duration_ms: Duration in milliseconds (max 60000).
    """
    return scratch_request("scratch.display_image_for",
                           image=image, duration=duration_ms)


def display_text(text: str) -> bytes:
    """Scroll text across the 5×5 LED matrix.

    Args:
        text: Text string to display.
    """
    return scratch_request("scratch.display_text", text=text)


def display_set_pixel(x: int, y: int, brightness: int) -> bytes:
    """Set a single pixel on the 5×5 matrix.

    Args:
        x: Column (0-4, left to right).
        y: Row (0-4, top to bottom).
        brightness: 0-100.
    """
    return scratch_request("scratch.display_set_pixel",
                           x=x, y=y, brightness=brightness)


def display_clear() -> bytes:
    """Clear the 5×5 LED matrix."""
    return scratch_request("scratch.display_clear", port="M1")


def sound_beep(volume: int, note: int) -> bytes:
    """Play a beep at the given note and volume.

    Args:
        volume: Volume 0-100.
        note: MIDI note number (60=middle C).
    """
    return scratch_request("scratch.sound_beep",
                           volume=volume, note=note)


def sound_beep_for_time(volume: int, note: int, duration_ms: int) -> bytes:
    """Play a beep for a specified time.

    Args:
        volume: Volume 0-100.
        note: MIDI note number.
        duration_ms: Duration in milliseconds.
    """
    return scratch_request("scratch.sound_beep_for_time",
                           volume=volume, note=note, duration=duration_ms)


def sound_off() -> bytes:
    """Stop all sounds."""
    return scratch_request("scratch.sound_off")


def ultrasonic_light_up(port: int, lights: list) -> bytes:
    """Control the ultrasonic sensor LEDs.

    Args:
        port: Port number.
        lights: List of 4 brightness values (0-100).
    """
    return scratch_request("scratch.ultrasonic_light_up",
                           port=port, lights=lights)


def program_terminate() -> bytes:
    """Terminate the currently running program on the hub."""
    return scratch_request("program_terminate")


# ── Motor pair (tank/steering) commands ────────────────────────────────

def move_tank_degrees(left_port: int, right_port: int,
                      left_speed: int, right_speed: int,
                      degrees: int, stall: bool = True,
                      stop: int = 1) -> bytes:
    """Move two motors (tank drive) for a number of degrees.

    Args:
        left_port: Left motor port number.
        right_port: Right motor port number.
        left_speed: Left speed [-100, 100].
        right_speed: Right speed [-100, 100].
        degrees: Degrees to rotate.
        stall: Stall detection.
        stop: Stop mode after completion.
    """
    return scratch_request("scratch.move_tank_degrees",
                           left_port=left_port, right_port=right_port,
                           lspeed=left_speed, rspeed=right_speed,
                           degrees=degrees, stall=stall, stop=stop)


def move_tank_timed(left_port: int, right_port: int,
                    left_speed: int, right_speed: int,
                    time_ms: int, stall: bool = True,
                    stop: int = 1) -> bytes:
    """Move two motors (tank drive) for a specified time."""
    return scratch_request("scratch.move_tank_time",
                           left_port=left_port, right_port=right_port,
                           lspeed=left_speed, rspeed=right_speed,
                           time=time_ms, stall=stall, stop=stop)


def move_start_powers(left_port: int, right_port: int,
                      left_power: int, right_power: int) -> bytes:
    """Start two motors at specified PWM power levels."""
    return scratch_request("scratch.move_start_powers",
                           left_port=left_port, right_port=right_port,
                           left_power=left_power, right_power=right_power)


def move_start_speeds(left_port: int, right_port: int,
                      left_speed: int, right_speed: int,
                      stall: bool = True) -> bytes:
    """Start two motors at specified speeds."""
    return scratch_request("scratch.move_start_speeds",
                           left_port=left_port, right_port=right_port,
                           left_speed=left_speed, right_speed=right_speed,
                           stall=stall)


def move_stop(left_port: int, right_port: int, stop: int = 1) -> bytes:
    """Stop two motors (tank pair)."""
    return scratch_request("scratch.move_stop",
                           left_port=left_port, right_port=right_port,
                           stop=stop)


# ── Color matrix (3×3) commands ────────────────────────────────────────

def color_matrix_set_image(port: int, image: list[list[int]]) -> bytes:
    """Set the 3×3 color matrix image.

    Args:
        port: Port number.
        image: 3x3 list of [color, brightness] pairs, flattened to
               9 elements of [color, brightness].
    """
    return scratch_request("scratch.color_matrix_set_image",
                           port=port, image=image)


def color_matrix_set_pixel(port: int, x: int, y: int,
                           color: int, brightness: int) -> bytes:
    """Set a single pixel on the 3×3 color matrix.

    Args:
        port: Port number.
        x: Column (0-2).
        y: Row (0-2).
        color: Color value (see enums.Color).
        brightness: 0-100.
    """
    return scratch_request("scratch.color_matrix_set_pixel",
                           port=port, x=x, y=y,
                           color=color, brightness=brightness)


def color_matrix_clear(port: int) -> bytes:
    """Clear the 3×3 color matrix."""
    return scratch_request("scratch.color_matrix_clear", port=port)


# ── Hub control commands ───────────────────────────────────────────────

def hub_light_on(color: int) -> bytes:
    """Set the hub status light color.

    Args:
        color: Color value (see enums.Color).
    """
    return scratch_request("scratch.hub_light_on", color=color)


def hub_light_off() -> bytes:
    """Turn off the hub status light."""
    return scratch_request("scratch.hub_light_off")


def reset_yaw() -> bytes:
    """Reset the hub IMU yaw angle to zero."""
    return scratch_request("scratch.reset_yaw")


def set_orientation(up: str = "top", front: str = "front") -> bytes:
    """Set the hub orientation reference.

    Args:
        up: Which hub face is "up" (top, front, right, bottom, back, left).
        front: Which hub face is "front".
    """
    return scratch_request("scratch.set_orientation", up=up, front=front)


def play_sound(path: str, volume: int = 100, freq: int = 100,
               wait: bool = False) -> bytes:
    """Play a sound file on the hub.

    Args:
        path: Sound file path on hub.
        volume: Volume 0-200 (mapped internally by hub).
        freq: Frequency/pitch 0-200 (mapped to actual Hz).
        wait: Whether to wait for sound to complete.
    """
    return scratch_request("scratch.play_sound",
                           path=path, volume=volume, freq=freq, wait=wait)


# ── Binary tunnel struct definitions ───────────────────────────────────

@dataclass
class TunnelCommand:
    """A binary tunnel command definition."""
    cmd_id: int
    name: str
    pack_format: str  # struct format string
    field_names: tuple


# Known binary tunnel command IDs (from JS TunnelMessageIdToDefinition)
# These are used when running Scratch-compiled MicroPython on the hub.
# The hub sends these via _tunnel.send(_pack(fmt, id, *values))
# and receives them via _tunnel.callback(data)
# The exact IDs and formats are extracted from module 88590.

# Placeholder — will be populated when explore agent completes
TUNNEL_COMMANDS: dict[int, TunnelCommand] = {}


def encode_tunnel_binary(cmd_id: int, *values) -> bytes:
    """Encode a binary tunnel command.

    Args:
        cmd_id: Command ID byte.
        *values: Values to pack according to the command's format.
    """
    if cmd_id in TUNNEL_COMMANDS:
        cmd = TUNNEL_COMMANDS[cmd_id]
        return struct.pack(cmd.pack_format, cmd_id, *values)
    # Fallback: just pack the ID + raw values as bytes
    return bytes([cmd_id]) + b"".join(
        v.to_bytes(1, "little") if isinstance(v, int) else v
        for v in values
    )


def decode_tunnel_binary(data: bytes) -> tuple[int, tuple]:
    """Decode a binary tunnel message.

    Returns:
        (cmd_id, values) tuple.
    """
    if not data:
        return (0, ())
    cmd_id = data[0]
    if cmd_id in TUNNEL_COMMANDS:
        cmd = TUNNEL_COMMANDS[cmd_id]
        size = struct.calcsize(cmd.pack_format)
        if len(data) >= size:
            values = struct.unpack(cmd.pack_format, data[:size])
            return (cmd_id, values[1:])  # skip cmd_id in values
    return (cmd_id, tuple(data[1:]))
