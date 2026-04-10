"""Simulated SPIKE 3 devices: motors, sensors, IMU, LED matrix.

Each device models realistic state and provides tick-based physics
(e.g., motor position changes with speed, IMU gyro drifts slightly).
"""

from __future__ import annotations

import math
import random
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from ..enums import NotifSubId, Color, Orientation


class DeviceBase:
    """Base class for all simulated devices."""

    SUB_ID: int = -1
    SIZE: int = 0  # notification payload size including sub_id byte

    def __init__(self, port: Optional[int] = None):
        self.port = port  # None for hub-internal devices (IMU, matrix, info)
        self._lock = threading.Lock()

    def to_notification_bytes(self) -> bytes:
        """Serialize current state to DeviceNotification sub-payload."""
        raise NotImplementedError

    def tick(self, dt: float):
        """Advance simulation by dt seconds. Override for physics."""
        pass


class Motor(DeviceBase):
    """Simulated motor with position, speed, power, stall detection.

    Device IDs from real hardware:
      0x30 (48) = small motor
      0x31 (49) = medium/large motor
    """

    SUB_ID = NotifSubId.MOTOR
    SIZE = 12

    def __init__(self, port: int, device_id: int = 0x31):
        super().__init__(port)
        self.device_id = device_id
        self.absolute_pos: int = 0      # -180..179
        self.power: int = 0             # -100..100
        self.speed: int = 0             # -100..100, current RPM-ish
        self.position: int = 0          # cumulative degrees
        self.stall: bool = False

        # Control targets (set by tunnel commands)
        self._target_speed: int = 0
        self._target_degrees: Optional[int] = None
        self._target_time_end: Optional[float] = None
        self._target_position: Optional[int] = None
        self._stop_mode: int = 1        # 0=float, 1=brake, 2=hold
        self._running: bool = False

    def start(self, speed: int, stall: bool = True):
        with self._lock:
            self._target_speed = max(-100, min(100, speed))
            self._target_degrees = None
            self._target_time_end = None
            self._target_position = None
            self._running = True
            self.stall = False

    def stop(self, mode: int = 1):
        with self._lock:
            self._target_speed = 0
            self._running = False
            self._stop_mode = mode
            if mode == 0:  # float
                pass  # speed decays in tick
            elif mode == 1:  # brake
                self.speed = 0
                self.power = 0
            elif mode == 2:  # hold
                self.speed = 0
                self.power = 0

    def run_degrees(self, speed: int, degrees: int, stop: int = 1):
        with self._lock:
            self._target_speed = max(-100, min(100, speed))
            self._target_degrees = self.position + degrees
            self._target_time_end = None
            self._target_position = None
            self._stop_mode = stop
            self._running = True

    def run_timed(self, speed: int, time_ms: int, stop: int = 1):
        with self._lock:
            self._target_speed = max(-100, min(100, speed))
            self._target_degrees = None
            self._target_time_end = time.monotonic() + time_ms / 1000.0
            self._target_position = None
            self._stop_mode = stop
            self._running = True

    def go_to_position(self, speed: int, position: int, stop: int = 1):
        with self._lock:
            self._target_speed = abs(speed) if position > self.position else -abs(speed)
            self._target_degrees = None
            self._target_time_end = None
            self._target_position = position
            self._stop_mode = stop
            self._running = True

    def set_position_offset(self, offset: int):
        with self._lock:
            self.position = offset

    def tick(self, dt: float):
        with self._lock:
            if not self._running:
                if self._stop_mode == 0:  # float: coast to stop
                    self.speed = int(self.speed * 0.9)
                    if abs(self.speed) < 2:
                        self.speed = 0
                self.power = 0
                return

            # Ramp speed toward target
            diff = self._target_speed - self.speed
            ramp = int(diff * min(1.0, dt * 10))  # 100ms ramp
            if ramp == 0 and diff != 0:
                ramp = 1 if diff > 0 else -1
            self.speed = self.speed + ramp
            self.power = self.speed

            # Move position
            # speed 100 ≈ 1000 deg/s, so degrees_per_tick = speed * 10 * dt
            degrees_moved = int(self.speed * 10 * dt)
            self.position += degrees_moved
            self.absolute_pos = ((self.position % 360) + 360) % 360
            if self.absolute_pos > 179:
                self.absolute_pos -= 360

            # Check target conditions
            if self._target_degrees is not None:
                if ((self._target_speed > 0 and self.position >= self._target_degrees) or
                        (self._target_speed < 0 and self.position <= self._target_degrees)):
                    self.position = self._target_degrees
                    self._running = False
                    self.speed = 0
                    self.power = 0

            if self._target_time_end is not None:
                if time.monotonic() >= self._target_time_end:
                    self._running = False
                    self.speed = 0
                    self.power = 0

            if self._target_position is not None:
                if ((self._target_speed > 0 and self.position >= self._target_position) or
                        (self._target_speed < 0 and self.position <= self._target_position)):
                    self.position = self._target_position
                    self._running = False
                    self.speed = 0
                    self.power = 0

    def to_notification_bytes(self) -> bytes:
        with self._lock:
            return struct.pack("<BBBhhbi",
                               self.SUB_ID, self.port, self.device_id,
                               self.absolute_pos, self.power,
                               self.speed, self.position)


class ColorSensor(DeviceBase):
    """Simulated color sensor."""

    SUB_ID = NotifSubId.COLOR_SENSOR
    SIZE = 10

    def __init__(self, port: int):
        super().__init__(port)
        self.color: int = Color.NONE
        self.reflection: int = 0
        self.raw_red: int = 0
        self.raw_green: int = 0
        self.raw_blue: int = 0

    def set_color(self, color: int):
        """Set detected color. Also updates approximate RGB."""
        with self._lock:
            self.color = color
            # Approximate RGB for common colors
            rgb_map = {
                Color.NONE: (0, 0, 0), Color.BLACK: (10, 10, 10),
                Color.RED: (800, 100, 100), Color.ORANGE: (700, 300, 50),
                Color.YELLOW: (600, 600, 50), Color.GREEN: (50, 600, 50),
                Color.BLUE: (50, 50, 800), Color.WHITE: (800, 800, 800),
                Color.MAGENTA: (600, 50, 600), Color.PURPLE: (300, 50, 600),
                Color.AZURE: (50, 300, 800), Color.TURQUOISE: (50, 600, 600),
            }
            self.raw_red, self.raw_green, self.raw_blue = rgb_map.get(color, (0, 0, 0))
            if color == Color.NONE:
                self.reflection = 0
            else:
                self.reflection = max(self.raw_red, self.raw_green, self.raw_blue) // 10

    def to_notification_bytes(self) -> bytes:
        with self._lock:
            return struct.pack("<BBbBHHH",
                               self.SUB_ID, self.port, self.color,
                               self.reflection,
                               self.raw_red, self.raw_green, self.raw_blue)


class DistanceSensor(DeviceBase):
    """Simulated ultrasonic distance sensor."""

    SUB_ID = NotifSubId.DISTANCE_SENSOR
    SIZE = 4

    def __init__(self, port: int):
        super().__init__(port)
        self.distance: int = -1  # mm, -1 = no object detected

    def set_distance(self, mm: int):
        with self._lock:
            self.distance = max(-1, min(2000, mm))

    def to_notification_bytes(self) -> bytes:
        with self._lock:
            return struct.pack("<BBh", self.SUB_ID, self.port, self.distance)


class ForceSensor(DeviceBase):
    """Simulated force/pressure sensor."""

    SUB_ID = NotifSubId.FORCE_SENSOR
    SIZE = 4

    def __init__(self, port: int):
        super().__init__(port)
        self.force: int = 0    # 0-100 (Newtons approx)
        self.touch: int = 0    # 0 or 1

    def set_force(self, force: int):
        with self._lock:
            self.force = max(0, min(100, force))
            self.touch = 1 if self.force > 0 else 0

    @property
    def pressed(self) -> bool:
        """True if the sensor is being pressed."""
        return self.touch > 0

    def to_notification_bytes(self) -> bytes:
        with self._lock:
            return struct.pack("<BBBB", self.SUB_ID, self.port,
                               self.force, self.touch)


class IMU(DeviceBase):
    """Simulated inertial measurement unit (gyro + accelerometer).

    Simulates slight drift and noise like real hardware.
    """

    SUB_ID = NotifSubId.IMU_HUB
    SIZE = 21

    def __init__(self):
        super().__init__(port=None)
        self.orientation: int = Orientation.TOP
        self.yaw_face: int = 0
        self.yaw: int = 0
        self.pitch: int = 0
        self.roll: int = 0
        self.accel_x: int = 0
        self.accel_y: int = 0
        self.accel_z: int = -980     # ~1g downward at rest
        self.gyro_x: int = 0
        self.gyro_y: int = 0
        self.gyro_z: int = 0
        self._noise_enabled = True

    def set_orientation(self, orientation: int, yaw: int = 0,
                        pitch: int = 0, roll: int = 0):
        with self._lock:
            self.orientation = orientation
            self.yaw = yaw
            self.pitch = pitch
            self.roll = roll
            # Update accel based on orientation
            accel_map = {
                Orientation.TOP: (0, 0, -980),
                Orientation.BOTTOM: (0, 0, 980),
                Orientation.FRONT: (0, -980, 0),
                Orientation.BACK: (0, 980, 0),
                Orientation.LEFT: (-980, 0, 0),
                Orientation.RIGHT: (980, 0, 0),
            }
            self.accel_x, self.accel_y, self.accel_z = accel_map.get(
                orientation, (0, 0, -980))

    def tick(self, dt: float):
        """Add slight noise like real IMU."""
        if not self._noise_enabled:
            return
        with self._lock:
            self.gyro_x = random.randint(-3, 3)
            self.gyro_y = random.randint(-3, 3)
            self.gyro_z = random.randint(-3, 3)
            self.accel_x += random.randint(-5, 5)
            self.accel_y += random.randint(-5, 5)
            self.accel_z += random.randint(-5, 5)

    def to_notification_bytes(self) -> bytes:
        with self._lock:
            return struct.pack("<BBBhhh hhh hhh",
                               self.SUB_ID,
                               self.orientation, self.yaw_face,
                               self.yaw, self.pitch, self.roll,
                               self.accel_x, self.accel_y, self.accel_z,
                               self.gyro_x, self.gyro_y, self.gyro_z)


class Matrix5x5(DeviceBase):
    """Simulated 5x5 LED matrix on the hub face."""

    SUB_ID = NotifSubId.MATRIX_HUB
    SIZE = 26

    def __init__(self):
        super().__init__(port=None)
        self.pixels: list[int] = [0] * 25  # brightness 0-100

    def set_image(self, image_str: str):
        """Set from 25-char brightness string ('0'-'9' mapped to 0-100)."""
        with self._lock:
            for i, ch in enumerate(image_str[:25]):
                if ch.isdigit():
                    self.pixels[i] = int(ch) * 11  # 0→0, 9→99
                else:
                    self.pixels[i] = 0
            # Pad if string was short
            for i in range(len(image_str), 25):
                self.pixels[i] = 0

    def set_pixel(self, x: int, y: int, brightness: int):
        with self._lock:
            if 0 <= x < 5 and 0 <= y < 5:
                self.pixels[y * 5 + x] = max(0, min(100, brightness))

    def clear(self):
        with self._lock:
            self.pixels = [0] * 25

    def to_notification_bytes(self) -> bytes:
        with self._lock:
            return bytes([self.SUB_ID]) + bytes(self.pixels)

    def render_ascii(self) -> str:
        """Render matrix as ASCII art for CLI display."""
        lines = []
        with self._lock:
            for y in range(5):
                row = []
                for x in range(5):
                    b = self.pixels[y * 5 + x]
                    if b == 0:
                        row.append("·")
                    elif b < 30:
                        row.append("░")
                    elif b < 60:
                        row.append("▒")
                    elif b < 90:
                        row.append("▓")
                    else:
                        row.append("█")
                lines.append(" ".join(row))
        return "\n".join(lines)


class InfoHub(DeviceBase):
    """Simulated hub info (battery level)."""

    SUB_ID = NotifSubId.INFO_HUB
    SIZE = 2

    def __init__(self):
        super().__init__(port=None)
        self.battery_level: int = 100

    def to_notification_bytes(self) -> bytes:
        return bytes([self.SUB_ID, self.battery_level])
