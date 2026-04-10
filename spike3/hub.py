"""High-level Hub class for communicating with SPIKE 3 hubs.

Provides a user-friendly API that manages transport, framing, and protocol
layers internally. Supports both Atlantis (new firmware) and MicroPython
JSON-RPC (legacy firmware) protocols.
"""

from __future__ import annotations

import logging
import struct
import threading
import time
from typing import Any, Callable, Optional

from . import atlantis, cobs, micropython, tunnel
from .enums import (
    MsgId, Status, ProgramAction, NotifSubId, Port,
    LEGO_VENDOR_ID, ProductId, HubType, ConnectionType,
    DEFAULT_TIMEOUT, SETUP_TIMEOUT, RESPONSE_TO_REQUEST,
)
from .transport import Transport, UsbTransport, BleTransport, TcpTransport

logger = logging.getLogger("spike3")


class Hub:
    """Main interface for communicating with a LEGO SPIKE 3 hub.

    Typical usage (USB, Atlantis protocol)::

        hub = Hub.connect_usb('/dev/ttyACM0')
        info = hub.get_info()
        print(f"FW {info.fw_major}.{info.fw_minor}.{info.fw_build}")
        hub.set_notification_interval(50)
        hub.on_notification = my_callback
        hub.start_program(0)
        hub.close()

    Or with context manager::

        with Hub.connect_usb('/dev/ttyACM0') as hub:
            info = hub.get_info()
    """

    def __init__(self, transport: Transport, protocol: str = "atlantis"):
        """
        Args:
            transport: An open Transport instance.
            protocol: 'atlantis' or 'micropython'.
        """
        self._transport = transport
        self._protocol = protocol
        self._lock = threading.Lock()

        # Atlantis state
        self._frame_acc = cobs.FrameAccumulator()
        self._pending: dict[int, threading.Event] = {}
        self._responses: dict[int, Any] = {}
        self._info: Optional[atlantis.InfoResponse] = None
        self._max_chunk_size = 512

        # MicroPython state
        self._mp_acc = micropython.MessageAccumulator()
        self._mp_pending: dict[int, threading.Event] = {}
        self._mp_responses: dict[int, Any] = {}
        self._mp_next_id = 1

        # Notification callbacks
        self.on_notification: Optional[Callable[[Any], None]] = None
        self.on_console: Optional[Callable[[str], None]] = None
        self.on_program_flow: Optional[Callable[[Any], None]] = None
        self.on_tunnel: Optional[Callable[[bytes], None]] = None

        # Latest sensor snapshot (updated on every DeviceNotification)
        self._latest_notifs: dict[tuple, Any] = {}  # keyed by (sub_id,) or (sub_id, port)

        # Start background receive
        self._transport.set_on_data(self._on_raw_data)

    # ── Factory methods ─────────────────────────────────────────────

    @classmethod
    def connect_usb(cls, port: str, baudrate: int = 115200,
                    protocol: str = "atlantis") -> "Hub":
        """Connect to a SPIKE hub via USB serial.

        Args:
            port: Serial port name (e.g. 'COM3', '/dev/ttyACM0').
            baudrate: Baud rate (default 115200).
            protocol: 'atlantis' or 'micropython'.
        """
        transport = UsbTransport(port, baudrate)
        transport.open()
        hub = cls(transport, protocol)
        logger.info(f"Connected to hub via USB: {port}")
        return hub

    @classmethod
    def connect_ble(cls, address: str, protocol: str = "atlantis") -> "Hub":
        """Connect to a SPIKE hub via BLE.

        Args:
            address: BLE device address or UUID.
            protocol: 'atlantis' (default for BLE).
        """
        transport = BleTransport(address)
        transport.open()
        hub = cls(transport, protocol)
        logger.info(f"Connected to hub via BLE: {address}")
        return hub

    @classmethod
    def connect_tcp(cls, host: str = "127.0.0.1", port: int = 51337,
                    protocol: str = "atlantis") -> "Hub":
        """Connect to a SPIKE hub (or simulator) via raw TCP.

        Used primarily on Windows where PTY is unavailable. Connect to
        the simulator's TcpComBridge::

            # Terminal 1 — start simulator
            python -m spike3.simulator --tcp --tcp-port 51337

            # Terminal 2 — connect
            hub = Hub.connect_tcp("127.0.0.1", 51337)

        Args:
            host: TCP host (default 127.0.0.1).
            port: TCP port (default 51337).
            protocol: 'atlantis' (default) or 'micropython'.
        """
        transport = TcpTransport(host, port)
        transport.open()
        hub = cls(transport, protocol)
        logger.info(f"Connected to hub via TCP: {host}:{port}")
        return hub

    @staticmethod
    def find_hubs() -> list[dict]:
        """Find connected SPIKE hubs on USB serial ports."""
        return UsbTransport.find_spike_ports()

    # ── Context manager ─────────────────────────────────────────────

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        """Disconnect from the hub."""
        self._transport.close()
        logger.info("Hub connection closed")

    # ── Raw data handler ────────────────────────────────────────────

    def _on_raw_data(self, raw: bytes):
        """Called by transport when raw bytes arrive."""
        if self._protocol == "atlantis":
            self._handle_atlantis_data(raw)
        else:
            self._handle_micropython_data(raw)

    def _handle_atlantis_data(self, raw: bytes):
        frames = self._frame_acc.feed(raw)
        for decoded, high_pri in frames:
            if not decoded:
                continue
            try:
                msg = atlantis.decode_message(decoded)
                logger.debug(f"Decoded Atlantis msg: id={msg.msg_id} type={type(msg).__name__}")
            except Exception as e:
                logger.warning(f"Failed to decode Atlantis message ({len(decoded)} bytes: {decoded[:32].hex(' ')}): {e}")
                continue
            self._dispatch_atlantis(msg)

    def _dispatch_atlantis(self, msg):
        msg_id = msg.msg_id

        # Check if this is a response to a pending request
        if msg_id in RESPONSE_TO_REQUEST:
            req_id = RESPONSE_TO_REQUEST[msg_id]
            if req_id in self._pending:
                self._responses[req_id] = msg
                self._pending[req_id].set()
                return

        # Unsolicited messages
        if msg_id == MsgId.DEVICE_NOTIFICATION:
            # Cache latest sensor values for polling API
            for sub in msg.notifications:
                key = (sub.sub_id, getattr(sub, 'port', None))
                self._latest_notifs[key] = sub
            if self.on_notification:
                self.on_notification(msg)
        elif msg_id == MsgId.CONSOLE_NOTIFICATION:
            if self.on_console:
                self.on_console(msg.text)
        elif msg_id == MsgId.PROGRAM_FLOW_NOTIFICATION:
            if self.on_program_flow:
                self.on_program_flow(msg)
        elif msg_id == MsgId.TUNNEL_MESSAGE:
            if self.on_tunnel:
                self.on_tunnel(msg.data)

    def _handle_micropython_data(self, raw: bytes):
        messages = self._mp_acc.feed(raw)
        for msg in messages:
            if isinstance(msg, micropython.RpcResponse):
                if msg.id in self._mp_pending:
                    self._mp_responses[msg.id] = msg
                    self._mp_pending[msg.id].set()
            elif isinstance(msg, micropython.RpcError):
                if msg.id in self._mp_pending:
                    self._mp_responses[msg.id] = msg
                    self._mp_pending[msg.id].set()
            elif isinstance(msg, micropython.RpcNotification):
                if self.on_notification:
                    self.on_notification(msg)

    # ── Atlantis low-level send/receive ─────────────────────────────

    def _send_atlantis(self, msg, timeout: float = DEFAULT_TIMEOUT):
        """Send an Atlantis message and wait for its response."""
        raw = atlantis.encode_message(msg)
        framed = cobs.encode(raw)

        req_id = msg.msg_id
        event = threading.Event()
        self._pending[req_id] = event

        logger.debug(f"Sending Atlantis msg_id={req_id} raw={raw.hex(' ')} framed={framed.hex(' ')}")

        try:
            with self._lock:
                self._transport.write(framed)

            if not event.wait(timeout=timeout):
                raise TimeoutError(
                    f"No response for message {req_id} within {timeout}s"
                )
            resp = self._responses.pop(req_id)
            if hasattr(resp, "status") and resp.status == Status.NACK:
                raise RuntimeError(f"Hub NACK'd message {req_id}")
            return resp
        finally:
            self._pending.pop(req_id, None)

    def _send_atlantis_no_response(self, msg):
        """Send an Atlantis message without waiting for response."""
        raw = atlantis.encode_message(msg)
        framed = cobs.encode(raw)
        with self._lock:
            self._transport.write(framed)

    # ── MicroPython low-level send/receive ──────────────────────────

    def _send_micropython(self, method: str, params: Any = None,
                          timeout: float = DEFAULT_TIMEOUT):
        """Send a MicroPython JSON-RPC request and wait for response."""
        msg_id = self._mp_next_id
        self._mp_next_id += 1

        req = micropython.RpcRequest(id=msg_id, method=method, params=params)
        event = threading.Event()
        self._mp_pending[msg_id] = event

        try:
            with self._lock:
                self._transport.write(req.to_bytes())

            if not event.wait(timeout=timeout):
                raise TimeoutError(
                    f"No response for RPC {method} (id={msg_id}) within {timeout}s"
                )
            resp = self._mp_responses.pop(msg_id)
            if isinstance(resp, micropython.RpcError):
                raise RuntimeError(f"RPC error: {resp.error}")
            return resp.result
        finally:
            self._mp_pending.pop(msg_id, None)

    def _send_micropython_raw(self, data: bytes):
        """Send raw bytes for REPL commands."""
        with self._lock:
            self._transport.write(data)

    # ── Polling mode (for transports without async callback) ────────

    def poll(self, timeout: float = 0.1):
        """Manually poll for incoming data (use when no background reader).

        Call this in a loop if your transport doesn't support set_on_data.
        """
        try:
            data = self._transport.read(4096, timeout=timeout)
            if data:
                self._on_raw_data(data)
        except Exception:
            pass

    # ── High-level Atlantis API ─────────────────────────────────────

    def get_info(self, timeout: float = SETUP_TIMEOUT,
                 retries: int = 2) -> atlantis.InfoResponse:
        """Send InfoRequest and return InfoResponse.

        This is typically the first message after connection.
        Retries on timeout (matching SPIKE App behavior).
        """
        last_err = None
        for attempt in range(retries):
            try:
                if attempt > 0:
                    logger.info(f"Retrying InfoRequest (attempt {attempt + 1}/{retries})")
                resp = self._send_atlantis(atlantis.InfoRequest(), timeout=timeout)
                self._info = resp
                self._max_chunk_size = resp.max_chunk_size
                logger.info(
                    f"Hub info: FW {resp.fw_major}.{resp.fw_minor}.{resp.fw_build}, "
                    f"RPC {resp.rpc_major}.{resp.rpc_minor}.{resp.rpc_build}, "
                    f"maxPacket={resp.max_packet_size}, maxChunk={resp.max_chunk_size}"
                )
                return resp
            except TimeoutError as e:
                last_err = e
                logger.warning(f"InfoRequest attempt {attempt + 1} timed out after {timeout}s")
        raise last_err

    def set_notification_interval(self, delay_ms: int = 50,
                                   timeout: float = SETUP_TIMEOUT):
        """Enable device notifications at the given interval.

        Args:
            delay_ms: Notification interval in milliseconds.
        """
        return self._send_atlantis(
            atlantis.DeviceNotificationRequest(delay_ms=delay_ms),
            timeout=timeout,
        )

    def start_program(self, slot: int = 0, timeout: float = DEFAULT_TIMEOUT):
        """Start a program in the given slot."""
        return self._send_atlantis(
            atlantis.ProgramFlowRequest(
                action=ProgramAction.START, slot=slot
            ),
            timeout=timeout,
        )

    def stop_program(self, slot: int = 0, timeout: float = DEFAULT_TIMEOUT):
        """Stop a program in the given slot."""
        return self._send_atlantis(
            atlantis.ProgramFlowRequest(
                action=ProgramAction.STOP, slot=slot
            ),
            timeout=timeout,
        )

    def get_hub_name(self, timeout: float = DEFAULT_TIMEOUT) -> str:
        """Get the hub's Bluetooth name."""
        resp = self._send_atlantis(
            atlantis.GetHubNameRequest(), timeout=timeout
        )
        return resp.name

    def set_hub_name(self, name: str, timeout: float = DEFAULT_TIMEOUT):
        """Set the hub's Bluetooth name."""
        return self._send_atlantis(
            atlantis.SetHubNameRequest(name=name), timeout=timeout
        )

    def get_device_uuid(self, timeout: float = DEFAULT_TIMEOUT) -> str:
        """Get the hub's unique device UUID."""
        resp = self._send_atlantis(
            atlantis.DeviceUuidRequest(), timeout=timeout
        )
        return resp.uuid

    def clear_slot(self, slot: int, timeout: float = DEFAULT_TIMEOUT):
        """Clear a program slot on the hub."""
        return self._send_atlantis(
            atlantis.ClearSlotRequest(slot=slot), timeout=timeout
        )

    def list_path(self, path: str = "", slot: int = 0,
                  timeout: float = DEFAULT_TIMEOUT):
        """List files at the given path on the hub.

        Note: use list_files() for a simpler API that returns filenames.
        """
        return self._send_atlantis(
            atlantis.ListPathRequest(path=path, slot=slot), timeout=timeout
        )

    def delete_path(self, path: str, slot: int = 0,
                    timeout: float = DEFAULT_TIMEOUT):
        """Delete a file or directory on the hub."""
        return self._send_atlantis(
            atlantis.DeletePathRequest(path=path, slot=slot), timeout=timeout
        )

    def move_slot(self, slot_from: int, slot_to: int,
                  timeout: float = DEFAULT_TIMEOUT):
        """Move a program from one slot to another."""
        return self._send_atlantis(
            atlantis.MoveSlotRequest(slot_from=slot_from, slot_to=slot_to),
            timeout=timeout,
        )

    def list_files(self, path: str = "", slot: int = 0,
                   timeout: float = DEFAULT_TIMEOUT) -> list:
        """List files at a path on the hub. Returns list of filenames."""
        resp = self._send_atlantis(
            atlantis.ListPathRequest(path=path, slot=slot), timeout=timeout
        )
        return resp.items

    def download_file_start(self, filename: str, slot: int = 0,
                            timeout: float = DEFAULT_TIMEOUT):
        """Start downloading a file from the hub.

        Returns StartFileDownloadResponse with status and file_crc.
        """
        return self._send_atlantis(
            atlantis.StartFileDownloadRequest(filename=filename, slot=slot),
            timeout=timeout,
        )

    def send_tunnel(self, data: bytes):
        """Send a tunnel message (bidirectional, no response expected)."""
        self._send_atlantis_no_response(atlantis.TunnelMessage(data=data))

    # ── Sensor polling API ─────────────────────────────────────────

    def get_battery(self) -> int:
        """Get last reported battery level (0-100%).

        Requires notifications to be enabled via set_notification_interval().
        """
        notif = self._latest_notifs.get((NotifSubId.INFO_HUB, None))
        return notif.battery_level if notif else -1

    def get_imu(self) -> Optional[Any]:
        """Get latest IMU data (orientation, accel, gyro).

        Returns ImuHubNotif or None if no data yet.
        """
        return self._latest_notifs.get((NotifSubId.IMU_HUB, None))

    def get_motor(self, port: int) -> Optional[Any]:
        """Get latest motor data for a port.

        Args:
            port: Port number (0=A, 1=B, ..., 5=F) or Port enum.

        Returns MotorNotif or None if no motor on that port.
        """
        return self._latest_notifs.get((NotifSubId.MOTOR, port))

    def get_color_sensor(self, port: int) -> Optional[Any]:
        """Get latest color sensor data for a port."""
        return self._latest_notifs.get((NotifSubId.COLOR_SENSOR, port))

    def get_distance_sensor(self, port: int) -> Optional[Any]:
        """Get latest distance sensor data for a port."""
        return self._latest_notifs.get((NotifSubId.DISTANCE_SENSOR, port))

    def get_force_sensor(self, port: int) -> Optional[Any]:
        """Get latest force sensor data for a port."""
        return self._latest_notifs.get((NotifSubId.FORCE_SENSOR, port))

    def get_matrix(self) -> Optional[Any]:
        """Get latest 5×5 LED matrix state."""
        return self._latest_notifs.get((NotifSubId.MATRIX_HUB, None))

    def get_all_motors(self) -> dict:
        """Get all connected motors as {port: MotorNotif}."""
        result = {}
        for (sid, port), notif in self._latest_notifs.items():
            if sid == NotifSubId.MOTOR:
                result[port] = notif
        return result

    # ── Convenience motor/LED/sound via tunnel ─────────────────────

    def motor_start(self, port: int, speed: int, **kwargs):
        """Start a motor at the given speed (via scratch JSON-RPC tunnel).

        Args:
            port: Port number (0=A, 1=B, ..., 5=F).
            speed: Speed -100 to 100.
        """
        self.send_tunnel(tunnel.motor_start(port, speed, **kwargs))

    def motor_stop(self, port: int, stop: int = 1):
        """Stop a motor.  stop: 0=float, 1=brake, 2=hold."""
        self.send_tunnel(tunnel.motor_stop(port, stop))

    def motor_run_degrees(self, port: int, speed: int, degrees: int,
                          **kwargs):
        """Run motor for a number of degrees."""
        self.send_tunnel(
            tunnel.motor_run_for_degrees(port, speed, degrees, **kwargs)
        )

    def motor_run_timed(self, port: int, speed: int, time_ms: int,
                        **kwargs):
        """Run motor for a specified time in milliseconds."""
        self.send_tunnel(
            tunnel.motor_run_timed(port, speed, time_ms, **kwargs)
        )

    def motor_go_to_position(self, port: int, speed: int, position: int,
                             direction: str = "shortest", **kwargs):
        """Move motor to an absolute position."""
        self.send_tunnel(
            tunnel.motor_go_to_position(port, speed, position,
                                        direction, **kwargs)
        )

    def motor_set_position(self, port: int, offset: int):
        """Reset motor encoder position."""
        self.send_tunnel(tunnel.motor_set_position(port, offset))

    def display_image(self, image: str):
        """Display a 25-char brightness string on 5×5 LED matrix.

        Example: '9909999099000990099009900' = heart
        Each char '0'-'9' = brightness level, row-major.
        """
        self.send_tunnel(tunnel.display_image(image))

    def display_image_for(self, image: str, duration_ms: int):
        """Display image for duration_ms then clear."""
        self.send_tunnel(tunnel.display_image_for(image, duration_ms))

    def display_text(self, text: str):
        """Scroll text across the 5×5 matrix."""
        self.send_tunnel(tunnel.display_text(text))

    def display_set_pixel(self, x: int, y: int, brightness: int):
        """Set a single pixel (x=0-4, y=0-4, brightness=0-100)."""
        self.send_tunnel(tunnel.display_set_pixel(x, y, brightness))

    def display_clear(self):
        """Clear the 5×5 LED matrix."""
        self.send_tunnel(tunnel.display_clear())

    def sound_beep(self, volume: int = 100, note: int = 60):
        """Play a beep.  note=MIDI number (60=middle C)."""
        self.send_tunnel(tunnel.sound_beep(volume, note))

    def sound_beep_for(self, volume: int, note: int, duration_ms: int):
        """Play a beep for duration_ms milliseconds."""
        self.send_tunnel(
            tunnel.sound_beep_for_time(volume, note, duration_ms)
        )

    def sound_off(self):
        """Stop all sounds."""
        self.send_tunnel(tunnel.sound_off())

    # ── Motor pair (tank/steering) ─────────────────────────────────

    def move_tank_degrees(self, left_port: int, right_port: int,
                          left_speed: int, right_speed: int,
                          degrees: int, **kwargs):
        """Tank drive two motors for a number of degrees."""
        self.send_tunnel(tunnel.move_tank_degrees(
            left_port, right_port, left_speed, right_speed, degrees, **kwargs))

    def move_tank_timed(self, left_port: int, right_port: int,
                        left_speed: int, right_speed: int,
                        time_ms: int, **kwargs):
        """Tank drive two motors for a specified time."""
        self.send_tunnel(tunnel.move_tank_timed(
            left_port, right_port, left_speed, right_speed, time_ms, **kwargs))

    def move_start_powers(self, left_port: int, right_port: int,
                          left_power: int, right_power: int):
        """Start two motors at PWM power levels."""
        self.send_tunnel(tunnel.move_start_powers(
            left_port, right_port, left_power, right_power))

    def move_start_speeds(self, left_port: int, right_port: int,
                          left_speed: int, right_speed: int, **kwargs):
        """Start two motors at specified speeds."""
        self.send_tunnel(tunnel.move_start_speeds(
            left_port, right_port, left_speed, right_speed, **kwargs))

    def move_stop(self, left_port: int, right_port: int, stop: int = 1):
        """Stop two motors (tank pair)."""
        self.send_tunnel(tunnel.move_stop(left_port, right_port, stop))

    # ── Color matrix (3×3) ─────────────────────────────────────────

    def color_matrix_set_image(self, port: int, image: list):
        """Set the 3×3 color matrix image."""
        self.send_tunnel(tunnel.color_matrix_set_image(port, image))

    def color_matrix_set_pixel(self, port: int, x: int, y: int,
                               color: int, brightness: int):
        """Set a single pixel on the 3×3 color matrix."""
        self.send_tunnel(tunnel.color_matrix_set_pixel(
            port, x, y, color, brightness))

    def color_matrix_clear(self, port: int):
        """Clear the 3×3 color matrix."""
        self.send_tunnel(tunnel.color_matrix_clear(port))

    # ── Hub status light ───────────────────────────────────────────

    def hub_light_on(self, color: int):
        """Set the hub status light color."""
        self.send_tunnel(tunnel.hub_light_on(color))

    def hub_light_off(self):
        """Turn off the hub status light."""
        self.send_tunnel(tunnel.hub_light_off())

    def reset_yaw(self):
        """Reset the hub IMU yaw angle to zero."""
        self.send_tunnel(tunnel.reset_yaw())

    def set_orientation(self, up: str = "top", front: str = "front"):
        """Set the hub orientation reference."""
        self.send_tunnel(tunnel.set_orientation(up, front))

    def play_sound(self, path: str, volume: int = 100, **kwargs):
        """Play a sound file on the hub."""
        self.send_tunnel(tunnel.play_sound(path, volume, **kwargs))

    def upload_program(self, filename: str, data: bytes, slot: int = 0,
                       on_progress: Optional[Callable[[int, int], None]] = None,
                       timeout: float = DEFAULT_TIMEOUT):
        """Upload a program file to the hub.

        Args:
            filename: Destination filename on hub.
            data: Program file bytes.
            slot: Target program slot (0-19).
            on_progress: Optional callback(bytes_sent, total_bytes).
            timeout: Timeout for each chunk transfer.
        """
        import zlib
        file_crc = zlib.crc32(data) & 0xFFFFFFFF

        # 1. Start file upload
        self._send_atlantis(
            atlantis.StartFileUploadRequest(
                filename=filename, slot=slot, file_crc=file_crc
            ),
            timeout=timeout,
        )

        # 2. Transfer chunks
        chunk_size = self._max_chunk_size
        offset = 0
        running_crc = 0
        while offset < len(data):
            chunk = data[offset:offset + chunk_size]
            running_crc = zlib.crc32(chunk, running_crc) & 0xFFFFFFFF
            self._send_atlantis(
                atlantis.TransferChunkRequest(
                    running_crc=running_crc, chunk_data=chunk
                ),
                timeout=timeout,
            )
            offset += len(chunk)
            if on_progress:
                on_progress(offset, len(data))

        logger.info(f"Uploaded {filename} ({len(data)} bytes) to slot {slot}")

    # ── High-level MicroPython API ──────────────────────────────────

    def mp_call(self, method: str, params: Any = None,
                timeout: float = DEFAULT_TIMEOUT):
        """Call a MicroPython JSON-RPC method.

        Args:
            method: RPC method name.
            params: Method parameters.
        """
        return self._send_micropython(method, params, timeout)

    def mp_repl_send(self, command: str, delay: float = 0.1):
        """Send a raw MicroPython REPL command.

        Args:
            command: REPL command string (without trailing \\r).
            delay: Wait time after sending.
        """
        self._send_micropython_raw(command.encode("utf-8") + b"\r")
        time.sleep(delay)

    def mp_interrupt(self):
        """Send Ctrl-C to interrupt running MicroPython code."""
        self._send_micropython_raw(b"\x03")

    def mp_soft_reboot(self):
        """Send Ctrl-D for MicroPython soft reboot."""
        self._send_micropython_raw(b"\x04")

    def mp_get_hub_info(self, timeout: float = DEFAULT_TIMEOUT) -> dict:
        """Get hub info via MicroPython REPL (legacy hubs).

        Sends `import hub;hub.info()` and parses the response.
        """
        self.mp_interrupt()
        time.sleep(0.1)
        self.mp_repl_send("import hub;print(hub.info())")
        # Response will arrive through the notification callback
        # For synchronous usage, caller should poll() and parse
        return {}
