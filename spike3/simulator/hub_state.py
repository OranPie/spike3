"""Virtual hub state machine for the SPIKE 3 simulator.

Holds all hub-level state: firmware info, name, UUID, battery,
connected devices on ports, program slots, notification settings.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Optional

from .devices import (
    DeviceBase, Motor, ColorSensor, DistanceSensor, ForceSensor,
    IMU, Matrix5x5, InfoHub,
)
from .storage import SlotStorage


NUM_PORTS = 6       # A-F
NUM_SLOTS = 20
DEFAULT_NAME = "SPIKE Simulator"


class HubState:
    """Complete simulated hub state, thread-safe."""

    def __init__(self, name: str = DEFAULT_NAME):
        self._lock = threading.RLock()

        # Identity
        self.name = name
        self.uuid = str(uuid.uuid4()).replace("-", "")

        # Firmware (matches real Flipper)
        self.fw_major = 1
        self.fw_minor = 8
        self.fw_build = 149
        self.rpc_major = 1
        self.rpc_minor = 0
        self.rpc_build = 47
        self.max_packet_size = 512
        self.max_message_size = 512
        self.max_chunk_size = 4096
        self.product_group_device = 0  # SPIKE_PRIME

        # Battery
        self.battery_level = 100

        # Hub button (center button)
        self.button_pressed = False

        # Left/right hub buttons
        self.button_left: bool = False
        self.button_right: bool = False
        self.button_left_was_pressed: bool = False
        self.button_right_was_pressed: bool = False

        # IMU gesture
        self.gesture: int = 0  # 0=none 1=shake 2=freefall 3=tapped 4=double_tapped

        # Hub temperature (°C)
        self.hub_temperature: int = 28

        # Hub-internal devices
        self.imu = IMU()
        self.matrix = Matrix5x5()
        self.info_hub = InfoHub()

        # External ports (A=0 .. F=5) — None means nothing connected
        self.ports: list[Optional[DeviceBase]] = [None] * NUM_PORTS

        # Notification state
        self.notification_interval_ms: int = 0  # 0 = disabled
        self._notif_enabled = False

        # Program state
        self.running_program: Optional[int] = None  # slot number or None
        self.program_start_time: float = 0

        # Storage
        self.storage = SlotStorage(NUM_SLOTS)

        # Sound state
        self.sound_playing = False
        self.sound_note: int = 0
        self.sound_volume: int = 0

        # Console output buffer (program prints)
        self.console_buffer: list[str] = []

        # Default: motors on A, B, C and distance sensor on D
        self.attach_device(0, Motor(0, device_id=0x31))  # Port A
        self.attach_device(1, Motor(1, device_id=0x31))  # Port B

    # ── Device management ──────────────────────────────────────────

    def attach_device(self, port: int, device: DeviceBase):
        """Attach a device to a port (0=A .. 5=F)."""
        with self._lock:
            if 0 <= port < NUM_PORTS:
                device.port = port
                self.ports[port] = device

    def detach_device(self, port: int) -> Optional[DeviceBase]:
        """Detach and return the device on a port."""
        with self._lock:
            if 0 <= port < NUM_PORTS:
                dev = self.ports[port]
                self.ports[port] = None
                return dev
            return None

    def get_device(self, port: int) -> Optional[DeviceBase]:
        with self._lock:
            if 0 <= port < NUM_PORTS:
                return self.ports[port]
            return None

    def get_motor(self, port: int) -> Optional[Motor]:
        dev = self.get_device(port)
        return dev if isinstance(dev, Motor) else None

    # ── Notification generation ────────────────────────────────────

    def enable_notifications(self, interval_ms: int):
        with self._lock:
            self.notification_interval_ms = interval_ms
            self._notif_enabled = interval_ms > 0

    @property
    def notifications_enabled(self) -> bool:
        return self._notif_enabled

    def build_notification_payload(self) -> bytes:
        """Build the DeviceNotification sub-payloads for all active devices.

        Returns the payload bytes (after msg_id byte), consisting of:
          u16(total_length) + concatenated sub-notification bytes
        """
        parts = []

        # Hub-internal sensors always included
        self.info_hub.battery_level = self.battery_level
        parts.append(self.info_hub.to_notification_bytes())
        parts.append(self.imu.to_notification_bytes())
        parts.append(self.matrix.to_notification_bytes())

        # External port devices
        with self._lock:
            for dev in self.ports:
                if dev is not None:
                    parts.append(dev.to_notification_bytes())

        payload = b"".join(parts)
        import struct
        return struct.pack("<H", len(payload)) + payload

    # ── Physics tick ───────────────────────────────────────────────

    def tick(self, dt: float):
        """Advance all device simulations by dt seconds."""
        self.imu.tick(dt)
        with self._lock:
            for dev in self.ports:
                if dev is not None:
                    dev.tick(dt)

    # ── Program flow ───────────────────────────────────────────────

    def start_program(self, slot: int) -> bool:
        with self._lock:
            if self.storage.has_file(slot):
                self.running_program = slot
                self.program_start_time = time.monotonic()
                return True
            return False

    def stop_program(self):
        with self._lock:
            self.running_program = None

    # ── Button / gesture simulation helpers ───────────────────────

    def press_button(self, button: str):
        """Simulate a button press. button = 'left' or 'right'."""
        if button == "left":
            self.button_left = True
            self.button_left_was_pressed = True
        elif button == "right":
            self.button_right = True
            self.button_right_was_pressed = True

    def release_button(self, button: str):
        """Simulate a button release."""
        if button == "left":
            self.button_left = False
        elif button == "right":
            self.button_right = False

    def trigger_gesture(self, gesture: int):
        """Simulate an IMU gesture (1=shake 2=freefall 3=tapped 4=double_tapped)."""
        self.gesture = gesture

    # ── Display helpers ────────────────────────────────────────────

    def status_summary(self) -> str:
        """One-line status for CLI display."""
        parts = [f"[{self.name}]"]
        parts.append(f"🔋{self.battery_level}%")

        port_labels = "ABCDEF"
        port_strs = []
        with self._lock:
            for i, dev in enumerate(self.ports):
                if dev is None:
                    port_strs.append(f"{port_labels[i]}:·")
                elif isinstance(dev, Motor):
                    port_strs.append(
                        f"{port_labels[i]}:M({dev.speed:+d},{dev.position}°)")
                elif isinstance(dev, ColorSensor):
                    port_strs.append(f"{port_labels[i]}:C({dev.color})")
                elif isinstance(dev, DistanceSensor):
                    port_strs.append(f"{port_labels[i]}:D({dev.distance}mm)")
                elif isinstance(dev, ForceSensor):
                    port_strs.append(f"{port_labels[i]}:F({dev.force}N)")
                else:
                    port_strs.append(f"{port_labels[i]}:?")
        parts.append(" ".join(port_strs))

        if self.running_program is not None:
            elapsed = time.monotonic() - self.program_start_time
            parts.append(f"▶slot{self.running_program}({elapsed:.0f}s)")

        if self.sound_playing:
            parts.append(f"♪{self.sound_note}")

        return " | ".join(parts)
