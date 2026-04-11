"""ConsoleNotification (Python REPL) handler for the SPIKE 3 simulator.

Receives Python REPL code strings from hub.py (via ConsoleNotification msg_id=33)
and dispatches them to the appropriate simulated devices, updating HubState.

The SPIKE 3 Atlantis hub accepts MicroPython via ConsoleNotification and echoes
output back as ConsoleNotification responses. We simulate that here.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from .hub_state import HubState
from .responder import ProtocolResponder

logger = logging.getLogger("spike3.simulator.console")

# Port letter → index (A=0 … F=5)
_PORT_LETTERS = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4, "F": 5}

# Stop-mode constant names
_STOP_MODES = {"motor.COAST": 0, "motor.BRAKE": 1, "motor.HOLD": 2, "motor.SMART_COAST": 0}


def _port(name: str) -> int:
    """'port.A' → 0, etc."""
    m = re.match(r"port\.([A-F])", name.strip())
    if m:
        return _PORT_LETTERS[m.group(1)]
    return 0


def _stop(name: str) -> int:
    """'motor.COAST' → 0, etc., or raw int string."""
    name = name.strip()
    if name in _STOP_MODES:
        return _STOP_MODES[name]
    try:
        return int(name)
    except ValueError:
        return 0


def _num(s: str) -> float:
    try:
        return float(s.strip())
    except ValueError:
        return 0.0


def _extract_args(body: str) -> list[str]:
    """Split 'a, b, c=d' into ['a', 'b', 'c=d'] accounting for nested brackets."""
    depth = 0
    parts: list[str] = []
    cur: list[str] = []
    for ch in body:
        if ch in "([{":
            depth += 1
            cur.append(ch)
        elif ch in ")]}":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur).strip())
    return [p for p in parts if p]


class ConsoleHandler:
    """Handles ConsoleNotification messages: parses Python REPL code → hub state."""

    def __init__(self, hub: HubState, responder: ProtocolResponder):
        self.hub = hub
        self.responder = responder
        responder.on_console = self.on_console_data

    def on_console_data(self, code: str):
        """Called when a ConsoleNotification (Python REPL code) arrives from the host."""
        code = code.rstrip("\r\n\x00")
        if not code:
            return
        logger.debug(f"Console REPL: {code!r}")

        output = self._dispatch(code)
        # Echo the code line back (REPL behaviour) + any output
        response = code + "\r\n"
        if output is not None:
            response += str(output) + "\r\n"
        response += ">>> "
        self.responder.send_console_text(response)

    # ── Dispatcher ─────────────────────────────────────────────────

    def _dispatch(self, code: str) -> Optional[str]:
        """Return value string to echo, or None."""
        # Handle multiple semicolon-separated statements
        if ";" in code:
            results = []
            for stmt in code.split(";"):
                stmt = stmt.strip()
                if stmt:
                    r = self._dispatch(stmt)
                    if r is not None:
                        results.append(r)
            return "\r\n".join(results) if results else None

        code = code.strip()

        # ── motor.run(port.X, vel) ─────────────────────────────────
        m = re.match(r"motor\.run\(([^,]+),\s*(-?\d+)\)", code)
        if m:
            port_idx = _port(m.group(1))
            vel = int(m.group(2))
            motor = self.hub.get_motor(port_idx)
            if motor:
                motor.start(vel // 10)  # vel deg/s → speed %
                logger.debug(f"motor.run port={port_idx} vel={vel}")
            return None

        # ── motor.stop(port.X, stop=motor.MODE) ───────────────────
        m = re.match(r"motor\.stop\(([^,)]+)(?:,\s*stop=(.+))?\)", code)
        if m:
            port_idx = _port(m.group(1))
            stop_mode = _stop(m.group(2)) if m.group(2) else 0
            motor = self.hub.get_motor(port_idx)
            if motor:
                motor.stop(stop_mode)
                logger.debug(f"motor.stop port={port_idx} mode={stop_mode}")
            return None

        # ── motor.run_for_degrees(port.X, deg, vel) ───────────────
        m = re.match(r"motor\.run_for_degrees\(([^,]+),\s*(-?\d+),\s*(-?\d+)", code)
        if m:
            port_idx = _port(m.group(1))
            degrees = int(m.group(2))
            vel = int(m.group(3))
            motor = self.hub.get_motor(port_idx)
            if motor:
                motor.run_degrees(vel // 10, degrees, 0)
                logger.debug(f"motor.run_for_degrees port={port_idx} deg={degrees}")
            return None

        # ── motor.run_for_time(port.X, ms, vel) ───────────────────
        m = re.match(r"motor\.run_for_time\(([^,]+),\s*(\d+),\s*(-?\d+)", code)
        if m:
            port_idx = _port(m.group(1))
            time_ms = int(m.group(2))
            vel = int(m.group(3))
            motor = self.hub.get_motor(port_idx)
            if motor:
                motor.run_timed(vel // 10, time_ms, 0)
                logger.debug(f"motor.run_for_time port={port_idx} ms={time_ms}")
            return None

        # ── motor.go_to_absolute_position(port.X, pos, vel, ...) ──
        m = re.match(r"motor\.go_to_absolute_position\(([^,]+),\s*(-?\d+),\s*(-?\d+)", code)
        if m:
            port_idx = _port(m.group(1))
            pos = int(m.group(2))
            vel = int(m.group(3))
            motor = self.hub.get_motor(port_idx)
            if motor:
                motor.go_to_position(vel // 10, pos, 0)
                logger.debug(f"motor.go_to_absolute_position port={port_idx} pos={pos}")
            return None

        # ── motor.reset_relative_position(port.X, offset) ─────────
        m = re.match(r"motor\.reset_relative_position\(([^,]+),\s*(-?\d+)\)", code)
        if m:
            port_idx = _port(m.group(1))
            offset = int(m.group(2))
            motor = self.hub.get_motor(port_idx)
            if motor:
                motor.set_position_offset(offset)
            return None

        # ── hub.display.show(hub.Image('...')) ────────────────────────
        m = re.match(r"hub\.display\.show\((?:hub\.)?Image\('([0-9:]{29})'\)\)", code)
        if m:
            img_str = m.group(1)
            digits = img_str.replace(":", "")
            # Scale 0-9 → 0-100 brightness
            pixels = [int(c) * 11 for c in digits]
            self.hub.matrix.pixels = pixels
            logger.debug("display.show image set")
            return None

        # ── hub.display.off() ──────────────────────────────────────
        if code == "hub.display.off()":
            self.hub.matrix.clear()
            return None

        # ── hub.display.pixel(x, y, b) ─────────────────────────────
        m = re.match(r"hub\.display\.pixel\((\d+),\s*(\d+),\s*(\d+)\)", code)
        if m:
            x, y, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
            self.hub.matrix.set_pixel(x, y, b * 11)
            return None

        # ── hub.sound.beep(freq, dur, vol) ─────────────────────────
        m = re.match(r"hub\.sound\.beep\((\d+(?:\.\d+)?),\s*(\d+),\s*(\d+)\)", code)
        if m:
            freq = float(m.group(1))
            dur = int(m.group(2))
            vol = int(m.group(3))
            self.hub.sound_playing = True
            self.hub.sound_volume = vol
            logger.debug(f"sound.beep freq={freq}Hz dur={dur}ms vol={vol}")
            return None

        # ── hub.sound.stop() ───────────────────────────────────────
        if code == "hub.sound.stop()":
            self.hub.sound_playing = False
            return None

        # ── hub.motion_sensor.reset_yaw_angle() ────────────────────
        if code == "hub.motion_sensor.reset_yaw_angle()":
            self.hub.imu.yaw = 0
            logger.debug("reset_yaw_angle → 0")
            return None

        # ── hub.light.color(n) ─────────────────────────────────────
        m = re.match(r"hub\.light\.color\((\d+)\)", code)
        if m:
            logger.debug(f"hub light color={m.group(1)}")
            return None

        # ── hub.light.off() ────────────────────────────────────────
        if code == "hub.light.off()":
            return None

        # ── port.X.device.write([...]) — color matrix ──────────────
        m = re.match(r"port\.([A-F])\.device\.write\(\[(.+)\]\)", code)
        if m:
            logger.debug(f"color_matrix write port={m.group(1)}")
            return None

        # ── hub.battery.capacity_left() ────────────────────────────
        if "hub.battery.capacity_left()" in code:
            return str(self.hub.battery_level)

        # ── hub.battery.charger_connected() / battery.charger_detect() ──
        if "hub.battery.charger_connected()" in code or "battery.charger_detect()" in code:
            return "False"

        # ── hub.motion_sensor.tilt_angles() ───────────────────────
        if "hub.motion_sensor.tilt_angles()" in code:
            imu = self.hub.imu
            return f"({imu.yaw}, {imu.pitch}, {imu.roll})"

        # ── hub.motion_sensor.acceleration() ──────────────────────
        if "hub.motion_sensor.acceleration()" in code:
            imu = self.hub.imu
            return f"({imu.accel_x}, {imu.accel_y}, {imu.accel_z})"

        # ── hub.motion_sensor.gyroscope() ─────────────────────────
        if "hub.motion_sensor.gyroscope()" in code:
            return "(0, 0, 0)"

        # ── motor.get_position / motor.relative_position(port.X) ────
        m = re.match(r"motor\.(?:get_position|relative_position)\(([^)]+)\)", code)
        if m:
            port_idx = _port(m.group(1))
            motor = self.hub.get_motor(port_idx)
            return str(motor.position if motor else 0)

        # ── motor.absolute_position(port.X) ──────────────────────────
        m = re.match(r"motor\.absolute_position\(([^)]+)\)", code)
        if m:
            port_idx = _port(m.group(1))
            motor = self.hub.get_motor(port_idx)
            pos = motor.position if motor else 0
            # Wrap to -180..179
            return str(((pos + 180) % 360) - 180)

        # ── motor.get_speed / motor.velocity(port.X) ─────────────────
        m = re.match(r"motor\.(?:get_speed|velocity)\(([^)]+)\)", code)
        if m:
            port_idx = _port(m.group(1))
            motor = self.hub.get_motor(port_idx)
            return str(motor.speed if motor else 0)

        # ── motor.get_duty_cycle(port.X) ───────────────────────────
        m = re.match(r"motor\.get_duty_cycle\(([^)]+)\)", code)
        if m:
            return "0"

        # ── color_sensor.color(port.X) ─────────────────────────────
        m = re.match(r"color_sensor\.color\(([^)]+)\)", code)
        if m:
            port_idx = _port(m.group(1))
            dev = self.hub.get_device(port_idx)
            from .devices import ColorSensor
            if isinstance(dev, ColorSensor):
                return str(int(dev.color))  # int() converts Color enum → int
            return "-1"

        # ── color_sensor.reflection(port.X) ────────────────────────
        m = re.match(r"color_sensor\.reflection\(([^)]+)\)", code)
        if m:
            port_idx = _port(m.group(1))
            dev = self.hub.get_device(port_idx)
            from .devices import ColorSensor
            if isinstance(dev, ColorSensor):
                return str(int(dev.reflection))
            return "0"

        # ── color_sensor.ambient(port.X) ───────────────────────────
        m = re.match(r"color_sensor\.ambient\(([^)]+)\)", code)
        if m:
            return "0"

        # ── color_sensor.get_red/green/blue(port.X) ────────────────
        m = re.match(r"color_sensor\.get_(red|green|blue)\(([^)]+)\)", code)
        if m:
            channel = m.group(1)
            port_idx = _port(m.group(2))
            dev = self.hub.get_device(port_idx)
            from .devices import ColorSensor
            if isinstance(dev, ColorSensor):
                return str(getattr(dev, f"raw_{channel}", 0))
            return "0"

        # ── color_sensor.rgbi(port.X) [JS-verified name] ──────────
        m = re.match(r"color_sensor\.rgbi\(([^)]+)\)", code)
        if m:
            port_idx = _port(m.group(1))
            dev = self.hub.get_device(port_idx)
            from .devices import ColorSensor
            if isinstance(dev, ColorSensor):
                return f"({dev.raw_red}, {dev.raw_green}, {dev.raw_blue}, 0)"
            return "(0, 0, 0, 0)"

        # ── force_sensor.force(port.X) ─────────────────────────────
        m = re.match(r"force_sensor\.force\(([^)]+)\)", code)
        if m:
            port_idx = _port(m.group(1))
            dev = self.hub.get_device(port_idx)
            from .devices import ForceSensor
            if isinstance(dev, ForceSensor):
                return str(dev.force)
            return "0"

        # ── force_sensor.pressed(port.X) ───────────────────────────
        m = re.match(r"force_sensor\.pressed\(([^)]+)\)", code)
        if m:
            port_idx = _port(m.group(1))
            dev = self.hub.get_device(port_idx)
            from .devices import ForceSensor
            if isinstance(dev, ForceSensor):
                return "True" if dev.touch else "False"
            return "False"

        # ── force_sensor.raw_force(port.X) ─────────────────────────
        m = re.match(r"force_sensor\.raw_force\(([^)]+)\)", code)
        if m:
            return "0"

        # ── distance_sensor.distance(port.X) ───────────────────────
        m = re.match(r"distance_sensor\.distance\(([^)]+)\)", code)
        if m:
            port_idx = _port(m.group(1))
            dev = self.hub.get_device(port_idx)
            from .devices import DistanceSensor
            if isinstance(dev, DistanceSensor):
                return str(dev.distance)
            return "-1"

        # ── motor.was_interrupted(port.X) ──────────────────────────
        m = re.match(r"motor\.was_interrupted\(([^)]+)\)", code)
        if m:
            return "False"

        # ── motor.is_stalled(port.X) ───────────────────────────────
        m = re.match(r"motor\.is_stalled\(([^)]+)\)", code)
        if m:
            return "False"

        # ── hub.left_button / hub.right_button ─────────────────────
        if code == "hub.left_button.is_pressed()":
            return "True" if self.hub.button_left else "False"
        if code == "hub.left_button.was_pressed()":
            v = self.hub.button_left_was_pressed
            self.hub.button_left_was_pressed = False
            return "True" if v else "False"
        if code == "hub.right_button.is_pressed()":
            return "True" if self.hub.button_right else "False"
        if code == "hub.right_button.was_pressed()":
            v = self.hub.button_right_was_pressed
            self.hub.button_right_was_pressed = False
            return "True" if v else "False"

        # ── hub.motion_sensor.get_gesture() / gesture() / was_gesture(n) ───
        if code in ("hub.motion_sensor.get_gesture()", "hub.motion_sensor.gesture()"):
            return str(self.hub.gesture)
        m = re.match(r"hub\.motion_sensor\.was_gesture\((\d+)\)", code)
        if m:
            n = int(m.group(1))
            return "True" if self.hub.gesture == n else "False"

        # ── hub.temperature() ──────────────────────────────────────
        if code == "hub.temperature()":
            return str(self.hub.hub_temperature)

        # ── hub.display.show(n) — numeric ──────────────────────────
        m = re.match(r"hub\.display\.show\((-?\d+)\)", code)
        if m:
            logger.debug(f"display.show number={m.group(1)}")
            return None

        # ── hub.display.scroll(text, speed) ────────────────────────
        m = re.match(r"hub\.display\.scroll\('([^']*)',\s*(\d+)\)", code)
        if m:
            logger.debug(f"display.scroll text={m.group(1)} speed={m.group(2)}")
            return None

        # ── hub.display.set_brightness(n) ──────────────────────────
        m = re.match(r"hub\.display\.set_brightness\((\d+)\)", code)
        if m:
            logger.debug(f"display.set_brightness={m.group(1)}")
            return None

        # ── hub.display.rotation(n) ────────────────────────────────
        m = re.match(r"hub\.display\.rotation\((\d+)\)", code)
        if m:
            logger.debug(f"display.rotation={m.group(1)}")
            return None

        # ── hub.sound.set_volume(n) ────────────────────────────────
        m = re.match(r"hub\.sound\.set_volume\((\d+)\)", code)
        if m:
            self.hub.volume = int(m.group(1))
            return None

        # ── hub.sound.get_volume() ─────────────────────────────────
        if code == "hub.sound.get_volume()":
            return str(getattr(self.hub, 'volume', 100))

        # ── hub.battery.voltage() / current() ─────────────────────
        if code == "hub.battery.voltage()":
            return str(getattr(self.hub, 'battery_voltage', 7200))
        if code == "hub.battery.current()":
            return str(getattr(self.hub, 'battery_current', 150))

        # ── hub.button.left/right.is_pressed() ────────────────────
        if code == "hub.button.left.is_pressed()":
            return "True" if self.hub.button_left_pressed else "False"
        if code == "hub.button.right.is_pressed()":
            return "True" if self.hub.button_right_pressed else "False"

        # ── hub.info() ─────────────────────────────────────────────
        if code == "hub.info()":
            return "{'product_variant': 0}"
        m = re.match(r"hub\.info\(\)\['([^']+)'\]", code)
        if m:
            return "0"

        # ── motor.run(port, duty) — PWM ────────────────────────────
        m = re.match(r"motor\.run\(([^,]+),\s*(-?\d+)\)", code)
        if m:
            port_idx = _port(m.group(1))
            duty = int(m.group(2))
            motor = self.hub.get_motor(port_idx)
            if motor:
                motor.speed = max(-100, min(100, duty // 100))
            return None

        # ── motor.run_to_absolute_position(...) ────────────────────
        m = re.match(r"motor\.run_to_absolute_position\(([^,]+),\s*(-?\d+)", code)
        if m:
            port_idx = _port(m.group(1))
            position = int(m.group(2))
            motor = self.hub.get_motor(port_idx)
            if motor:
                motor.position = position
            return None

        # ── print(...) — ignore ────────────────────────────────────
        if code.startswith("print("):
            return None

        logger.warning(f"Console: unrecognized REPL code: {code!r}")
        return None
