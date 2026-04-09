"""Interactive CLI for controlling the SPIKE 3 simulator.

Provides a readline-based interface to:
  - View hub status (battery, ports, IMU, matrix)
  - Set sensor values (color, distance, force)
  - Control battery level
  - Attach/detach devices on ports
  - Press the hub button
  - View the 5×5 LED matrix as ASCII art
  - Upload test programs to slots

Usage::

    python -m spike3.simulator          # Start with defaults
    python -m spike3.simulator --tcp    # Use TCP bridge instead of PTY
"""

from __future__ import annotations

import cmd
import logging
import os
import sys
import threading
import time
from typing import Optional

from .hub_state import HubState
from .devices import (
    Motor, ColorSensor, DistanceSensor, ForceSensor, IMU, Matrix5x5,
)
from .com_server import ComServer, TcpComBridge
from ..enums import Color, Orientation

logger = logging.getLogger("spike3.simulator")


BANNER = r"""
  ╔═══════════════════════════════════════════════════╗
  ║   SPIKE 3 Hub Simulator v0.1                      ║
  ║   Type 'help' for commands, 'quit' to exit        ║
  ╚═══════════════════════════════════════════════════╝
"""

COLOR_NAMES = {
    "none": Color.NONE, "black": Color.BLACK, "red": Color.RED,
    "orange": Color.ORANGE, "yellow": Color.YELLOW, "green": Color.GREEN,
    "blue": Color.BLUE, "white": Color.WHITE, "magenta": Color.MAGENTA,
    "purple": Color.PURPLE, "azure": Color.AZURE, "turquoise": Color.TURQUOISE,
}

ORIENT_NAMES = {
    "top": Orientation.TOP, "front": Orientation.FRONT,
    "right": Orientation.RIGHT, "bottom": Orientation.BOTTOM,
    "back": Orientation.BACK, "left": Orientation.LEFT,
}


class SimulatorCLI(cmd.Cmd):
    """Interactive simulator control console."""

    prompt = "spike-sim> "
    intro = BANNER

    def __init__(self, server, hub: HubState):
        super().__init__()
        self.server = server
        self.hub = hub
        self._status_thread: Optional[threading.Thread] = None
        self._show_status = False

    # ── Status display ─────────────────────────────────────────────

    def do_status(self, arg):
        """Show current hub status."""
        print(f"\n{self.hub.status_summary()}")
        print(f"  Port path: {self.server.port}")
        print(f"  Notifications: {'ON' if self.hub.notifications_enabled else 'OFF'} "
              f"({self.hub.notification_interval_ms}ms)")
        print(f"  Name: {self.hub.name}")
        print(f"  UUID: {self.hub.uuid}")
        print(f"  FW: {self.hub.fw_major}.{self.hub.fw_minor}.{self.hub.fw_build}")

        # Show ports
        port_labels = "ABCDEF"
        for i in range(6):
            dev = self.hub.ports[i]
            if dev is None:
                print(f"  Port {port_labels[i]}: (empty)")
            elif isinstance(dev, Motor):
                print(f"  Port {port_labels[i]}: Motor"
                      f" pos={dev.position}° speed={dev.speed}"
                      f" power={dev.power} abs={dev.absolute_pos}°")
            elif isinstance(dev, ColorSensor):
                print(f"  Port {port_labels[i]}: ColorSensor"
                      f" color={dev.color} refl={dev.reflection}"
                      f" RGB=({dev.raw_red},{dev.raw_green},{dev.raw_blue})")
            elif isinstance(dev, DistanceSensor):
                print(f"  Port {port_labels[i]}: DistanceSensor"
                      f" dist={dev.distance}mm")
            elif isinstance(dev, ForceSensor):
                print(f"  Port {port_labels[i]}: ForceSensor"
                      f" force={dev.force}N touch={dev.touch}")

        # IMU
        imu = self.hub.imu
        print(f"  IMU: orient={imu.orientation}"
              f" yaw={imu.yaw} pitch={imu.pitch} roll={imu.roll}"
              f" accel=({imu.accel_x},{imu.accel_y},{imu.accel_z})")

        # Storage
        files = self.hub.storage.list_files(slot=-1)
        print(f"  Storage: {len(files)} files: {files}")
        print()

    def do_matrix(self, arg):
        """Show the 5×5 LED matrix as ASCII art."""
        print()
        print(self.hub.matrix.render_ascii())
        print()

    def do_watch(self, arg):
        """Toggle live status display (updates every second)."""
        if self._show_status:
            self._show_status = False
            print("Status watch OFF")
        else:
            self._show_status = True
            print("Status watch ON (press Enter to stop)")
            self._status_thread = threading.Thread(
                target=self._watch_loop, daemon=True)
            self._status_thread.start()

    def _watch_loop(self):
        while self._show_status:
            # Clear screen and show status
            sys.stdout.write("\033[2J\033[H")  # ANSI clear
            sys.stdout.write(self.hub.status_summary() + "\n")
            sys.stdout.write(f"Port: {self.server.port}\n")
            sys.stdout.write(f"Matrix:\n{self.hub.matrix.render_ascii()}\n")
            sys.stdout.flush()
            time.sleep(1.0)

    # ── Battery ────────────────────────────────────────────────────

    def do_battery(self, arg):
        """Set battery level: battery <0-100>"""
        try:
            level = int(arg)
            self.hub.battery_level = max(0, min(100, level))
            print(f"Battery set to {self.hub.battery_level}%")
        except ValueError:
            print(f"Current battery: {self.hub.battery_level}%")

    # ── Hub name ───────────────────────────────────────────────────

    def do_name(self, arg):
        """Set hub name: name <new_name>"""
        if arg.strip():
            self.hub.name = arg.strip()
            print(f"Hub name set to: {self.hub.name}")
        else:
            print(f"Hub name: {self.hub.name}")

    # ── Port device management ─────────────────────────────────────

    def do_attach(self, arg):
        """Attach a device: attach <port> <type> [options]
        Types: motor, color, distance, force
        Ports: A-F (or 0-5)
        Examples:
          attach A motor
          attach C color
          attach D distance
          attach E force"""
        parts = arg.split()
        if len(parts) < 2:
            print("Usage: attach <port> <type>")
            return

        port = self._parse_port(parts[0])
        if port is None:
            print(f"Invalid port: {parts[0]} (use A-F or 0-5)")
            return

        dtype = parts[1].lower()
        if dtype == "motor":
            dev_id = int(parts[2]) if len(parts) > 2 else 0x31
            self.hub.attach_device(port, Motor(port, device_id=dev_id))
            print(f"Motor attached to port {'ABCDEF'[port]}")
        elif dtype in ("color", "colorsensor"):
            self.hub.attach_device(port, ColorSensor(port))
            print(f"ColorSensor attached to port {'ABCDEF'[port]}")
        elif dtype in ("distance", "distancesensor"):
            self.hub.attach_device(port, DistanceSensor(port))
            print(f"DistanceSensor attached to port {'ABCDEF'[port]}")
        elif dtype in ("force", "forcesensor"):
            self.hub.attach_device(port, ForceSensor(port))
            print(f"ForceSensor attached to port {'ABCDEF'[port]}")
        else:
            print(f"Unknown device type: {dtype}")
            print("Types: motor, color, distance, force")

    def do_detach(self, arg):
        """Detach device from port: detach <port>"""
        port = self._parse_port(arg.strip())
        if port is None:
            print("Usage: detach <port> (A-F or 0-5)")
            return
        dev = self.hub.detach_device(port)
        if dev:
            print(f"Detached {type(dev).__name__} from port {'ABCDEF'[port]}")
        else:
            print(f"Port {'ABCDEF'[port]} was already empty")

    # ── Sensor value setting ───────────────────────────────────────

    def do_color(self, arg):
        """Set color sensor value: color <port> <color_name>
        Colors: none, black, red, orange, yellow, green, blue, white,
                magenta, purple, azure, turquoise"""
        parts = arg.split()
        if len(parts) < 2:
            print("Usage: color <port> <color_name>")
            return
        port = self._parse_port(parts[0])
        if port is None:
            return
        dev = self.hub.get_device(port)
        if not isinstance(dev, ColorSensor):
            print(f"Port {'ABCDEF'[port]} doesn't have a color sensor")
            return
        color = COLOR_NAMES.get(parts[1].lower())
        if color is None:
            print(f"Unknown color: {parts[1]}")
            print(f"Available: {', '.join(COLOR_NAMES.keys())}")
            return
        dev.set_color(color)
        print(f"Color sensor on {'ABCDEF'[port]} set to {parts[1]}")

    def do_distance(self, arg):
        """Set distance sensor: distance <port> <mm>"""
        parts = arg.split()
        if len(parts) < 2:
            print("Usage: distance <port> <mm>")
            return
        port = self._parse_port(parts[0])
        if port is None:
            return
        dev = self.hub.get_device(port)
        if not isinstance(dev, DistanceSensor):
            print(f"Port {'ABCDEF'[port]} doesn't have a distance sensor")
            return
        try:
            mm = int(parts[1])
            dev.set_distance(mm)
            print(f"Distance on {'ABCDEF'[port]} set to {mm}mm")
        except ValueError:
            print("Distance must be an integer (mm)")

    def do_force(self, arg):
        """Set force sensor: force <port> <newtons>"""
        parts = arg.split()
        if len(parts) < 2:
            print("Usage: force <port> <newtons>")
            return
        port = self._parse_port(parts[0])
        if port is None:
            return
        dev = self.hub.get_device(port)
        if not isinstance(dev, ForceSensor):
            print(f"Port {'ABCDEF'[port]} doesn't have a force sensor")
            return
        try:
            n = int(parts[1])
            dev.set_force(n)
            print(f"Force on {'ABCDEF'[port]} set to {n}N")
        except ValueError:
            print("Force must be an integer (0-100)")

    def do_imu(self, arg):
        """Set IMU orientation: imu <orientation> [yaw] [pitch] [roll]
        Orientations: top, front, right, bottom, back, left"""
        parts = arg.split()
        if not parts:
            print("Usage: imu <orientation> [yaw] [pitch] [roll]")
            return
        orient = ORIENT_NAMES.get(parts[0].lower())
        if orient is None:
            print(f"Unknown orientation: {parts[0]}")
            print(f"Available: {', '.join(ORIENT_NAMES.keys())}")
            return
        yaw = int(parts[1]) if len(parts) > 1 else 0
        pitch = int(parts[2]) if len(parts) > 2 else 0
        roll = int(parts[3]) if len(parts) > 3 else 0
        self.hub.imu.set_orientation(orient, yaw, pitch, roll)
        print(f"IMU set to {parts[0]} (yaw={yaw}, pitch={pitch}, roll={roll})")

    # ── Program slots ──────────────────────────────────────────────

    def do_upload(self, arg):
        """Upload a test program: upload <slot> [filename]"""
        parts = arg.split()
        if not parts:
            print("Usage: upload <slot> [filename]")
            return
        try:
            slot = int(parts[0])
        except ValueError:
            print("Slot must be a number (0-19)")
            return
        filename = parts[1] if len(parts) > 1 else f"program_{slot}.py"
        # Create a simple test program
        data = f'# Test program in slot {slot}\nprint("Hello from slot {slot}!")\n'.encode()
        import zlib
        crc = zlib.crc32(data) & 0xFFFFFFFF
        self.hub.storage.begin_upload(filename, slot, crc)
        self.hub.storage.append_chunk(0, data)
        self.hub.storage.finish_upload()
        print(f"Uploaded '{filename}' to slot {slot} ({len(data)} bytes)")

    def do_slots(self, arg):
        """List all program slots."""
        for i in range(self.hub.storage.num_slots):
            f = self.hub.storage.get_file(i)
            if f:
                print(f"  Slot {i:2d}: {f.filename} ({len(f.data)} bytes, CRC=0x{f.crc32:08X})")
            else:
                print(f"  Slot {i:2d}: (empty)")

    # ── Sound ──────────────────────────────────────────────────────

    def do_sound(self, arg):
        """Show/control sound state."""
        if self.hub.sound_playing:
            print(f"Sound: playing note={self.hub.sound_note} vol={self.hub.sound_volume}")
        else:
            print("Sound: off")

    # ── Port path ──────────────────────────────────────────────────

    def do_port(self, arg):
        """Show the virtual serial port path for connecting."""
        print(f"Connect to: {self.server.port}")

    # ── Quit ───────────────────────────────────────────────────────

    def do_quit(self, arg):
        """Stop the simulator and exit."""
        self._show_status = False
        print("Stopping simulator...")
        self.server.stop()
        return True

    def do_exit(self, arg):
        """Same as quit."""
        return self.do_quit(arg)

    def do_EOF(self, arg):
        """Handle Ctrl-D."""
        print()
        return self.do_quit(arg)

    # ── Helpers ────────────────────────────────────────────────────

    @staticmethod
    def _parse_port(s: str) -> Optional[int]:
        """Parse port string: 'A'-'F' or '0'-'5'."""
        s = s.strip().upper()
        if len(s) == 1:
            if 'A' <= s <= 'F':
                return ord(s) - ord('A')
            if '0' <= s <= '5':
                return int(s)
        return None


def main():
    """Entry point for `python -m spike3.simulator`."""
    import argparse

    parser = argparse.ArgumentParser(
        description="SPIKE 3 Hub Simulator — fake hub on virtual COM port")
    parser.add_argument("--name", default="SPIKE Simulator",
                        help="Hub name (default: 'SPIKE Simulator')")
    parser.add_argument("--tcp", action="store_true",
                        help="Use TCP bridge instead of PTY (for Windows)")
    parser.add_argument("--tcp-port", type=int, default=51337,
                        help="TCP port for TCP bridge mode (default: 51337)")
    parser.add_argument("--symlink", default="/tmp/spike3-sim",
                        help="Symlink path for PTY (default: /tmp/spike3-sim)")
    parser.add_argument("--debug", action="store_true",
                        help="Enable debug logging")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress all logging (CLI only)")
    parser.add_argument("--tui", action="store_true",
                        help="Launch rich Textual TUI instead of basic CLI")
    parser.add_argument("--ble", action="store_true",
                        help="Also start BLE GATT peripheral (requires Bluetooth adapter)")
    parser.add_argument("--ble-only", action="store_true",
                        help="Run BLE peripheral only (no COM/TCP)")
    parser.add_argument("--scenario", default=None,
                        choices=["robot", "sorter", "alarm", "music", "line"],
                        help="Load a pre-built device scenario on startup")
    args = parser.parse_args()

    # Configure logging
    if args.quiet:
        logging.basicConfig(level=logging.CRITICAL)
    elif args.debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(name)s [%(levelname)s]: %(message)s"
        )
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(name)s [%(levelname)s]: %(message)s"
        )

    hub = HubState(name=args.name)

    # Apply scenario if requested
    if args.scenario:
        _apply_scenario(hub, args.scenario)

    # BLE-only mode
    if args.ble_only:
        from .ble_server import run_ble_server
        run_ble_server(hub)
        return

    if args.tcp:
        server = TcpComBridge(hub, port=args.tcp_port)
    else:
        server = ComServer(hub, symlink_path=args.symlink)

    ble_bridge = None

    try:
        server.start()

        # Also start BLE if requested
        if args.ble:
            import asyncio as _asyncio
            import threading as _threading
            from .ble_server import BleServer

            def _start_ble():
                loop = _asyncio.new_event_loop()
                _asyncio.set_event_loop(loop)
                ble = BleServer(hub)
                try:
                    loop.run_until_complete(ble.start())
                    loop.run_forever()
                except Exception as e:
                    logger.error(f"BLE error: {e}")
                    print(f"  ⚠ BLE failed: {e}")
                finally:
                    loop.close()

            ble_thread = _threading.Thread(target=_start_ble, daemon=True, name="ble-server")
            ble_thread.start()
            print(f"  BLE advertising as: {args.name}")

        if args.tui:
            from .tui import run_tui
            run_tui(hub, server)
        else:
            print(f"  Simulator ready! Connect to: {server.port}")
            print()
            cli = SimulatorCLI(server, hub)
            cli.cmdloop()
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        server.stop()


def _apply_scenario(hub: HubState, name: str):
    """Pre-configure hub with a device scenario."""
    from .devices import Motor, ColorSensor, DistanceSensor, ForceSensor
    from ..enums import Color

    if name == "robot":
        hub.attach_device(0, Motor(0, device_id=0x31))
        hub.attach_device(1, Motor(1, device_id=0x31))
        cs = ColorSensor(2); cs.set_color(Color.RED)
        hub.attach_device(2, cs)
        ds = DistanceSensor(3); ds.set_distance(250)
        hub.attach_device(3, ds)
    elif name == "sorter":
        hub.attach_device(0, Motor(0, device_id=0x31))
        cs = ColorSensor(1); cs.set_color(Color.BLUE)
        hub.attach_device(1, cs)
        fs = ForceSensor(2); fs.set_force(30)
        hub.attach_device(2, fs)
    elif name == "alarm":
        ds = DistanceSensor(0); ds.set_distance(500)
        hub.attach_device(0, ds)
    elif name == "music":
        hub.sound_playing = True
        hub.sound_note = 60
        hub.sound_volume = 80
    elif name == "line":
        hub.attach_device(0, Motor(0, device_id=0x31))
        hub.attach_device(1, Motor(1, device_id=0x31))
        cs1 = ColorSensor(2); cs1.set_color(Color.WHITE)
        hub.attach_device(2, cs1)
        cs2 = ColorSensor(3); cs2.set_color(Color.BLACK)
        hub.attach_device(3, cs2)


if __name__ == "__main__":
    main()
