"""Transport layer: USB serial and BLE connections to SPIKE hubs.

USB uses pyserial (System.IO.Ports equivalent).
BLE uses bleak (Windows.Devices.Bluetooth equivalent).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import Callable, Optional

from .enums import (
    LEGO_VENDOR_ID, ProductId, ConnectionType,
    ATLANTIS_SERVICE_UUID, ATLANTIS_TX_CHAR_UUID, ATLANTIS_RX_CHAR_UUID,
    LWP3_SERVICE_UUID, LWP3_CHAR_UUID,
    POST_OPEN_DELAY,
)

logger = logging.getLogger("spike3.transport")


class Transport(ABC):
    """Abstract transport interface."""

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def write(self, data: bytes) -> None: ...

    @abstractmethod
    def read(self, size: int = 4096, timeout: float = 1.0) -> bytes: ...

    @property
    @abstractmethod
    def is_open(self) -> bool: ...

    def set_on_data(self, callback: Optional[Callable[[bytes], None]]) -> None:
        """Set callback for asynchronous data reception (optional)."""
        pass


# ── USB Serial Transport ───────────────────────────────────────────

class UsbTransport(Transport):
    """USB CDC serial transport using pyserial.

    Mirrors the native .NET HubUsbConnection which uses System.IO.Ports.SerialPort
    with DTR and RTS enabled.
    """

    def __init__(self, port: str, baudrate: int = 115200):
        """
        Args:
            port: Serial port name (e.g. 'COM3', '/dev/ttyACM0').
            baudrate: Baud rate. Default 115200 (standard for LEGO hubs).
        """
        self._port_name = port
        self._baudrate = baudrate
        self._serial = None
        self._on_data: Optional[Callable[[bytes], None]] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False

    def open(self) -> None:
        import serial
        self._serial = serial.Serial(
            port=self._port_name,
            baudrate=self._baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.1,
            write_timeout=5.0,
        )
        # Match native .NET: DTR and RTS enabled
        # (may fail on PTY / virtual ports — non-critical)
        try:
            self._serial.dtr = True
            self._serial.rts = True
        except (OSError, serial.SerialException):
            logger.debug("DTR/RTS not supported on this port (PTY?)")
        logger.info(f"Opened USB serial: {self._port_name} @ {self._baudrate}")

        # Flush any stale data in the OS buffers
        try:
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()
        except (OSError, serial.SerialException):
            pass

        # Wait for hub to stabilize after DTR/RTS assertion
        # USB CDC devices often need a brief delay before they accept data
        time.sleep(POST_OPEN_DELAY)
        logger.debug(f"Post-open delay {POST_OPEN_DELAY}s complete, flushed buffers")

        if self._on_data:
            self._start_reader()

    def close(self) -> None:
        self._running = False
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2.0)
        if self._serial and self._serial.is_open:
            self._serial.close()
            logger.info(f"Closed USB serial: {self._port_name}")
        self._serial = None

    def write(self, data: bytes) -> None:
        if not self._serial or not self._serial.is_open:
            raise IOError("SerialPort is not open")
        logger.debug(f"TX ({len(data)}): {data.hex(' ')}")
        self._serial.write(data)
        self._serial.flush()

    def read(self, size: int = 4096, timeout: float = 1.0) -> bytes:
        if not self._serial or not self._serial.is_open:
            raise IOError("SerialPort is not open")
        old_timeout = self._serial.timeout
        self._serial.timeout = timeout
        try:
            data = self._serial.read(size)
            return data if data else b""
        finally:
            self._serial.timeout = old_timeout

    @property
    def is_open(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def set_on_data(self, callback: Optional[Callable[[bytes], None]]) -> None:
        self._on_data = callback
        if self.is_open and callback and not self._running:
            self._start_reader()

    def _start_reader(self):
        self._running = True
        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True, name="spike3-usb-reader"
        )
        self._reader_thread.start()

    def _reader_loop(self):
        """Background thread mimicking .NET RunReceiveLoopAsync."""
        logger.debug("Reader thread started")
        consecutive_errors = 0
        MAX_CONSECUTIVE_ERRORS = 10
        while self._running and self.is_open:
            try:
                data = self._serial.read(4096)
                if data:
                    logger.debug(f"RX ({len(data)}): {data.hex(' ')}")
                    consecutive_errors = 0  # reset on successful read
                    if self._on_data:
                        self._on_data(data)
            except (OSError, IOError) as e:
                if not self._running:
                    break
                consecutive_errors += 1
                logger.warning(f"Serial read error ({consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}): {e}")
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.error("Too many consecutive serial errors, exiting reader loop")
                    break
                # Brief pause before retry to avoid busy-loop on persistent errors
                import time
                time.sleep(0.1)
            except Exception as e:
                if self._running:
                    logger.error(f"Unexpected reader error: {e}")
                break
        logger.debug("Falling out of receive loop")

    @staticmethod
    def find_spike_ports() -> list[dict]:
        """Enumerate USB serial ports that match LEGO SPIKE VID/PID.

        Returns:
            List of dicts with 'port', 'vid', 'pid', 'product_id', 'description'.
        """
        try:
            from serial.tools.list_ports import comports
        except ImportError:
            logger.warning("pyserial not installed; cannot enumerate ports")
            return []

        results = []
        for p in comports():
            if p.vid == LEGO_VENDOR_ID:
                try:
                    pid_enum = ProductId(p.pid)
                except ValueError:
                    pid_enum = None
                results.append({
                    "port": p.device,
                    "vid": p.vid,
                    "pid": p.pid,
                    "product_id": pid_enum,
                    "description": p.description,
                })
        return results


# ── BLE Transport ──────────────────────────────────────────────────

class BleTransport(Transport):
    """BLE GATT transport using bleak.

    Supports both Atlantis (0000fd02) and LWP3 (00001623) BLE services.
    Automatically detects which service the hub advertises.
    """

    def __init__(self, address: str):
        """
        Args:
            address: BLE device address or UUID.
        """
        self._address = address
        self._client = None
        self._on_data: Optional[Callable[[bytes], None]] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._connected = False
        self._tx_uuid: Optional[str] = None
        self._rx_uuid: Optional[str] = None

    def open(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="spike3-ble-loop"
        )
        self._thread.start()
        # Run connect in the BLE event loop
        future = asyncio.run_coroutine_threadsafe(self._connect(), self._loop)
        future.result(timeout=15.0)

    async def _connect(self):
        from bleak import BleakClient

        self._client = BleakClient(self._address)
        await self._client.connect()
        self._connected = True
        logger.info(f"Connected to BLE device: {self._address}")

        # Detect which GATT service is available
        services = self._client.services
        if services.get_service(ATLANTIS_SERVICE_UUID):
            self._tx_uuid = ATLANTIS_TX_CHAR_UUID
            self._rx_uuid = ATLANTIS_RX_CHAR_UUID
            logger.info("Using Atlantis BLE service (0000fd02)")
        elif services.get_service(LWP3_SERVICE_UUID):
            self._tx_uuid = LWP3_CHAR_UUID
            self._rx_uuid = LWP3_CHAR_UUID  # LWP3 is bidirectional
            logger.info("Using LWP3 BLE service (00001623)")
        else:
            # Fallback to Atlantis
            self._tx_uuid = ATLANTIS_TX_CHAR_UUID
            self._rx_uuid = ATLANTIS_RX_CHAR_UUID
            logger.warning("No known service found, trying Atlantis UUIDs")

        # Request larger MTU if possible
        try:
            if hasattr(self._client, 'mtu_size'):
                logger.debug(f"BLE MTU: {self._client.mtu_size}")
        except Exception:
            pass

        # Subscribe to RX characteristic notifications
        await self._client.start_notify(
            self._rx_uuid, self._notification_handler
        )

    def _notification_handler(self, sender, data: bytearray):
        """BLE characteristic value changed handler."""
        if self._on_data:
            self._on_data(bytes(data))

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def close(self) -> None:
        if self._client and self._connected:
            if self._loop and self._loop.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    self._client.disconnect(), self._loop
                )
                try:
                    future.result(timeout=5.0)
                except Exception:
                    pass
            self._connected = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._client = None
        logger.info(f"Disconnected from BLE device: {self._address}")

    def write(self, data: bytes) -> None:
        if not self._client or not self._connected:
            raise IOError("BLE device not connected")
        future = asyncio.run_coroutine_threadsafe(
            self._client.write_gatt_char(
                self._tx_uuid, data, response=False
            ),
            self._loop,
        )
        future.result(timeout=5.0)

    def read(self, size: int = 4096, timeout: float = 1.0) -> bytes:
        # BLE uses notification-based reception, not polling
        raise NotImplementedError("BLE uses notification callbacks; use set_on_data()")

    @property
    def is_open(self) -> bool:
        return self._connected

    def set_on_data(self, callback: Optional[Callable[[bytes], None]]) -> None:
        self._on_data = callback

    @staticmethod
    async def scan(timeout: float = 10.0) -> list[dict]:
        """Scan for SPIKE hubs via BLE advertisement.

        Returns:
            List of dicts with 'address', 'name', 'rssi'.
        """
        from bleak import BleakScanner

        devices = await BleakScanner.discover(
            timeout=timeout,
            service_uuids=[ATLANTIS_SERVICE_UUID],
        )
        return [
            {"address": d.address, "name": d.name or "Unknown", "rssi": d.rssi}
            for d in devices
        ]


# ── TCP Transport ──────────────────────────────────────────────────

class TcpTransport(Transport):
    """Raw TCP socket transport for connecting to the simulator's TcpComBridge.

    Allows the spike3 library (and hub_tui) to connect to the SPIKE 3
    simulator over a plain TCP connection — the primary connectivity
    mechanism on Windows where PTY is unavailable.

    Usage::

        transport = TcpTransport("127.0.0.1", 51337)
        transport.open()
        hub = Hub(transport)

    Or via factory::

        hub = Hub.connect_tcp("127.0.0.1", 51337)
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 51337):
        self._host = host
        self._port = port
        self._sock = None
        self._on_data: Optional[Callable[[bytes], None]] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False

    def open(self) -> None:
        import socket
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(5.0)
        self._sock.connect((self._host, self._port))
        self._sock.settimeout(0.1)
        self._running = True
        logger.info(f"Connected via TCP: {self._host}:{self._port}")
        if self._on_data:
            self._start_reader()

    def close(self) -> None:
        self._running = False
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2.0)
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        logger.info(f"Closed TCP connection: {self._host}:{self._port}")

    def write(self, data: bytes) -> None:
        if not self._sock:
            raise IOError("TCP socket not connected")
        logger.debug(f"TX ({len(data)}): {data.hex(' ')}")
        self._sock.sendall(data)

    def read(self, size: int = 4096, timeout: float = 1.0) -> bytes:
        if not self._sock:
            raise IOError("TCP socket not connected")
        import socket
        old_timeout = self._sock.gettimeout()
        self._sock.settimeout(timeout)
        try:
            return self._sock.recv(size) or b""
        except socket.timeout:
            return b""
        finally:
            self._sock.settimeout(old_timeout)

    @property
    def is_open(self) -> bool:
        return self._sock is not None and self._running

    def set_on_data(self, callback: Optional[Callable[[bytes], None]]) -> None:
        self._on_data = callback
        if self.is_open and callback and (
            self._reader_thread is None or not self._reader_thread.is_alive()
        ):
            self._start_reader()

    def _start_reader(self):
        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True, name="spike3-tcp-reader"
        )
        self._reader_thread.start()

    def _reader_loop(self):
        import socket
        logger.debug("TCP reader thread started")
        while self._running and self._sock:
            try:
                data = self._sock.recv(4096)
                if not data:
                    logger.info("TCP server closed connection")
                    break
                logger.debug(f"RX ({len(data)}): {data.hex(' ')}")
                if self._on_data:
                    self._on_data(data)
            except socket.timeout:
                continue
            except OSError as e:
                if self._running:
                    logger.error(f"TCP read error: {e}")
                break
        logger.debug("TCP reader thread exiting")
