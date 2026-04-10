"""spike3.tunnel — Python REPL command builders for SPIKE 3 hub.

All functions return Python code strings (``str``) that are sent to the hub
via ConsoleNotification (Atlantis msg_id=33). The hub executes them in its
MicroPython REPL.

**Architecture note**: For Atlantis-firmware hubs (FW ≥ 1.8.149), the
``scratch.*`` JSON-RPC commands used in the official app are NOT sent to the
hub directly — they are routed via the PtConnection text protocol which is
only available on older FlipperPT firmware. On Atlantis (modern) firmware the
correct way to issue interactive commands is via the MicroPython Python API.

The SPIKE 3 MicroPython API (hub FW 1.8.149+):
  - ``import hub``  — LED matrix, sound, IMU, buttons, battery, status light
  - ``import motor``  — motor control (velocity in deg/s)
  - ``import motor_pair``  — synchronized pair drive
  - ``port.A`` … ``port.F``  — port references for motor/sensor

Usage::

    from spike3 import Hub
    from spike3 import tunnel

    with Hub.connect_usb('COM3') as hub:
        hub.exec_python(tunnel.display_text('Hello'))
        hub.exec_python(tunnel.motor_start(0, 50))   # port A, 50% speed
        hub.exec_python(tunnel.sound_beep(80, 60))   # vol=80, MIDI C4
        time.sleep(1)
        hub.exec_python(tunnel.motor_stop(0))
        hub.exec_python(tunnel.display_clear())
"""

from __future__ import annotations
import json
import math
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



# ── Helpers ────────────────────────────────────────────────────────────

def _port_name(port_num: int) -> str:
    """Convert 0-5 port number to A-F letter."""
    return chr(65 + max(0, min(5, int(port_num))))


def _speed_to_vel(speed_pct: int) -> int:
    """Convert -100..100% speed to deg/s velocity for motor.run()."""
    return int(max(-1000, min(1000, int(speed_pct) * 10)))


def _image_25_to_micropython(image: str) -> str:
    """Convert 25-char brightness string to hub.Image() literal.

    '9900099000...' -> "hub.Image('99000:99000:...')"
    Each char is 0-9 brightness, row-major (5 chars x 5 rows).
    """
    img = image[:25].ljust(25, '0')
    rows = [img[i * 5:(i + 1) * 5] for i in range(5)]
    return "hub.Image('" + ':'.join(rows) + "')"


def _midi_to_hz(note: int) -> int:
    """Convert MIDI note number to Hz frequency."""
    return int(440 * (2 ** ((int(note) - 69) / 12)))


# ── Motor commands ─────────────────────────────────────────────────────

def motor_start(port: int, speed: int, stall: bool = True,
                acceleration: int = 10000, deceleration: int = 10000) -> str:
    """Start a motor continuously at the given speed.

    Args:
        port: Port number (0=A, 1=B, ..., 5=F).
        speed: Speed in range [-100, 100] (percent).
    """
    p = _port_name(port)
    vel = _speed_to_vel(speed)
    return f"motor.run(port.{p}, {vel})"


def motor_stop(port: int, stop: int = 1) -> str:
    """Stop a motor.

    Args:
        port: Port number.
        stop: Stop mode - 0=COAST, 1=BRAKE, 2=HOLD.
    """
    p = _port_name(port)
    stop_map = {0: "COAST", 1: "BRAKE", 2: "HOLD"}
    mode = stop_map.get(int(stop), "BRAKE")
    return f"motor.stop(port.{p}, stop=motor.{mode})"


def motor_run_for_degrees(port: int, speed: int, degrees: int,
                          stall: bool = True, stop: int = 1,
                          acceleration: int = 10000,
                          deceleration: int = 10000) -> str:
    """Run motor for a number of degrees.

    Args:
        port: Port number.
        speed: Speed [-100, 100].
        degrees: Degrees to rotate.
    """
    p = _port_name(port)
    vel = _speed_to_vel(speed)
    return f"motor.run_for_degrees(port.{p}, {int(degrees)}, {vel})"


def motor_run_timed(port: int, speed: int, time_ms: int,
                    stall: bool = True, stop: int = 1,
                    acceleration: int = 10000,
                    deceleration: int = 10000) -> str:
    """Run motor for a specified time.

    Args:
        port: Port number.
        speed: Speed [-100, 100].
        time_ms: Duration in milliseconds.
    """
    p = _port_name(port)
    vel = _speed_to_vel(speed)
    return f"motor.run_for_time(port.{p}, {int(time_ms)}, {vel})"


def motor_go_to_position(port: int, speed: int, position: int,
                         direction: str = "shortest",
                         stall: bool = True, stop: int = 1) -> str:
    """Move motor to an absolute position.

    Args:
        port: Port number.
        speed: Speed [-100, 100].
        position: Target position in degrees.
        direction: 'shortest', 'clockwise', or 'counterclockwise'.
    """
    p = _port_name(port)
    vel = _speed_to_vel(speed)
    dir_map = {"shortest": "SHORTEST_PATH", "clockwise": "CLOCKWISE",
               "counterclockwise": "COUNTERCLOCKWISE"}
    d = dir_map.get(str(direction).lower(), "SHORTEST_PATH")
    return f"motor.go_to_absolute_position(port.{p}, {int(position)}, {vel}, direction=motor.{d})"


def motor_set_position(port: int, offset: int) -> str:
    """Reset motor encoder to a specific position value.

    Args:
        port: Port number.
        offset: New position value in degrees.
    """
    p = _port_name(port)
    return f"motor.reset_relative_position(port.{p}, {int(offset)})"


# ── Tank / motor-pair commands ──────────────────────────────────────────

def move_tank_degrees(left_port: int, right_port: int,
                      left_speed: int, right_speed: int,
                      degrees: int, stall: bool = True, stop: int = 1) -> str:
    """Move two motors (tank drive) for a given number of degrees."""
    lp = _port_name(left_port)
    rp = _port_name(right_port)
    lv = _speed_to_vel(left_speed)
    rv = _speed_to_vel(right_speed)
    return (f"motor.run_for_degrees(port.{lp}, {int(degrees)}, {lv});"
            f"motor.run_for_degrees(port.{rp}, {int(degrees)}, {rv})")


def move_tank_timed(left_port: int, right_port: int,
                    left_speed: int, right_speed: int,
                    time_ms: int, stall: bool = True, stop: int = 1) -> str:
    """Move two motors (tank drive) for a specified time."""
    lp = _port_name(left_port)
    rp = _port_name(right_port)
    lv = _speed_to_vel(left_speed)
    rv = _speed_to_vel(right_speed)
    return (f"motor.run_for_time(port.{lp}, {int(time_ms)}, {lv});"
            f"motor.run_for_time(port.{rp}, {int(time_ms)}, {rv})")


def move_start_powers(left_port: int, right_port: int,
                      left_power: int, right_power: int) -> str:
    """Start two motors at specified power levels (treated as speed %)."""
    lp = _port_name(left_port)
    rp = _port_name(right_port)
    lv = _speed_to_vel(left_power)
    rv = _speed_to_vel(right_power)
    return f"motor.run(port.{lp}, {lv});motor.run(port.{rp}, {rv})"


def move_start_speeds(left_port: int, right_port: int,
                      left_speed: int, right_speed: int,
                      stall: bool = True) -> str:
    """Start two motors at specified speeds."""
    lp = _port_name(left_port)
    rp = _port_name(right_port)
    lv = _speed_to_vel(left_speed)
    rv = _speed_to_vel(right_speed)
    return f"motor.run(port.{lp}, {lv});motor.run(port.{rp}, {rv})"


def move_stop(left_port: int, right_port: int, stop: int = 1) -> str:
    """Stop two motors (tank pair)."""
    lp = _port_name(left_port)
    rp = _port_name(right_port)
    stop_map = {0: "COAST", 1: "BRAKE", 2: "HOLD"}
    mode = stop_map.get(int(stop), "BRAKE")
    return (f"motor.stop(port.{lp}, stop=motor.{mode});"
            f"motor.stop(port.{rp}, stop=motor.{mode})")


# ── Hub 5x5 LED display commands ───────────────────────────────────────

def display_image(image: str) -> str:
    """Display an image on the 5x5 LED matrix.

    Args:
        image: 25-character string of brightness values '0'-'9',
               row-major (top-left to bottom-right).
    """
    return f"hub.display.show({_image_25_to_micropython(image)})"


def display_image_for(image: str, duration_ms: int) -> str:
    """Display an image for a duration then clear.

    Args:
        image: 25-char brightness string.
        duration_ms: Duration in milliseconds.
    """
    return (f"hub.display.show({_image_25_to_micropython(image)});"
            f"import time;time.sleep_ms({int(duration_ms)});hub.display.off()")


def display_text(text: str) -> str:
    """Scroll text across the 5x5 LED matrix."""
    return f"hub.display.show({repr(str(text))})"


def display_set_pixel(x: int, y: int, brightness: int) -> str:
    """Set a single pixel on the 5x5 matrix.

    Args:
        x: Column (0-4, left to right).
        y: Row (0-4, top to bottom).
        brightness: 0-100 (mapped to 0-9 for MicroPython pixel()).
    """
    b = max(0, min(9, int(round(brightness * 9 / 100))))
    return f"hub.display.pixel({int(x)}, {int(y)}, {b})"


def display_clear() -> str:
    """Clear the 5x5 LED matrix."""
    return "hub.display.off()"


# ── Sound commands ─────────────────────────────────────────────────────

def sound_beep(volume: int, note: int) -> str:
    """Play a short beep at the given MIDI note and volume.

    Args:
        volume: Volume 0-100.
        note: MIDI note number (60=middle C, 69=A4=440Hz).
    """
    freq = _midi_to_hz(note)
    vol = max(0, min(100, int(volume)))
    return f"hub.sound.beep({freq}, 200, {vol})"


def sound_beep_for_time(volume: int, note: int, duration_ms: int) -> str:
    """Play a beep for a specified duration.

    Args:
        volume: Volume 0-100.
        note: MIDI note number.
        duration_ms: Duration in milliseconds.
    """
    freq = _midi_to_hz(note)
    vol = max(0, min(100, int(volume)))
    return f"hub.sound.beep({freq}, {int(duration_ms)}, {vol})"


def sound_off() -> str:
    """Stop all sounds on the hub."""
    return "hub.sound.stop()"


# ── Hub status light ────────────────────────────────────────────────────

def hub_light_on(color: int) -> str:
    """Set the hub status light color.

    Args:
        color: Color value. Hub color constants: BLACK=0, VIOLET=1,
               BLUE=3, CYAN=6, GREEN=5, YELLOW=7, RED=9, WHITE=10.
    """
    return f"hub.light.color({int(color)})"


def hub_light_off() -> str:
    """Turn off the hub status light."""
    return "hub.light.off()"


# ── IMU / orientation commands ─────────────────────────────────────────

def reset_yaw() -> str:
    """Reset the hub IMU yaw angle to zero."""
    return "hub.motion_sensor.reset_yaw_angle()"


def set_orientation(up: str = "top", front: str = "front") -> str:
    """Placeholder - SPIKE 3 handles orientation via motion_sensor automatically."""
    return "pass  # set_orientation: use hub.motion_sensor directly"


# ── Sound file playback ─────────────────────────────────────────────────

def play_sound(path: str, volume: int = 100, freq: int = 100,
               wait: bool = False) -> str:
    """Play a sound file stored on the hub.

    Args:
        path: Sound file path on the hub (e.g. '/extra_files/Tada').
        volume: Volume 0-100.
        freq: Pitch percentage (100=normal).
        wait: Whether to await completion.
    """
    vol = max(0, min(100, int(volume)))
    return f"hub.sound.play({repr(str(path))}, volume={vol})"


# ── Program control ─────────────────────────────────────────────────────

def program_terminate() -> str:
    """Terminate the currently running program."""
    return "raise SystemExit"


# ── Ultrasonic sensor LEDs ──────────────────────────────────────────────

def ultrasonic_light_up(port: int, lights: list) -> str:
    """Control the ultrasonic sensor LEDs.

    Args:
        port: Port number.
        lights: List of 4 brightness values (0-100).
    """
    p = _port_name(port)
    vals = [max(0, min(100, int(v))) for v in lights[:4]]
    vals_str = ', '.join(str(v) for v in vals)
    return f"port.{p}.device.write([{vals_str}])"


# ── Color matrix (3x3) commands ────────────────────────────────────────

def color_matrix_set_image(port: int, image: list) -> str:
    """Set a 3x3 color matrix image.

    Args:
        port: Port number.
        image: Flat list of [color, brightness] pairs (9 pairs for 3x3).
    """
    p = _port_name(port)
    return f"port.{p}.device.write({repr(list(image))})"


def color_matrix_set_pixel(port: int, x: int, y: int,
                           color: int, brightness: int) -> str:
    """Set a pixel on the 3x3 color matrix."""
    p = _port_name(port)
    idx = int(y) * 3 + int(x)
    return (f"_cm=port.{p}.device;_d=list(_cm.get() or [0]*18);"
            f"_d[{idx*2}]={int(color)};_d[{idx*2+1}]={int(brightness)};_cm.write(_d)")


def color_matrix_clear(port: int) -> str:
    """Clear the 3x3 color matrix (all black)."""
    p = _port_name(port)
    return f"port.{p}.device.write([0]*18)"


# ── JSON-RPC legacy helpers (kept for compatibility) ───────────────────

def scratch_request(method: str, _id: Optional[str] = None, **params) -> bytes:
    """Build a JSON-RPC request (legacy FlipperPT protocol only).

    Not used for Atlantis hubs. Kept for compatibility.
    """
    import secrets
    if _id is None:
        _id = secrets.token_hex(2)
    msg = {"i": _id, "m": method, "p": params}
    return (json.dumps(msg, separators=(",", ":")) + "\r").encode("utf-8")


def scratch_notification(method: str, **params) -> bytes:
    """Build a JSON-RPC notification (legacy FlipperPT protocol only)."""
    msg = {"m": method, "p": params}
    return (json.dumps(msg, separators=(",", ":")) + "\r").encode("utf-8")


def parse_tunnel_json(data: bytes) -> Optional[dict]:
    """Try to parse data as a JSON-RPC message."""
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
