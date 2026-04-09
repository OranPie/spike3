"""Virtual COM port server using PTY pairs (Linux/Mac).

Creates a pseudo-terminal pair: the master end is used by the simulator,
and the slave end is exposed as a device path (e.g. /dev/pts/5) that
the SPIKE App or spike3 library can connect to as a real serial port.

On Windows, use `com0com` to create a virtual COM pair, then pass
one end to ComServer and connect the app to the other.

Usage::

    from spike3.simulator import ComServer, HubState, ProtocolResponder

    hub = HubState()
    server = ComServer(hub)
    server.start()
    print(f"Connect to: {server.port}")
    # ... interact ...
    server.stop()
"""

from __future__ import annotations

import logging
import os
import platform
import struct
import sys
import threading
import time
from typing import Optional

from .. import cobs
from ..enums import MsgId
from .hub_state import HubState
from .responder import ProtocolResponder
from .tunnel_handler import TunnelHandler

logger = logging.getLogger("spike3.simulator.com")


class ComServer:
    """Virtual COM port simulator server.

    Creates a PTY pair (Linux/Mac) and runs the full Atlantis protocol
    stack on the master end.
    """

    def __init__(self, hub: Optional[HubState] = None,
                 symlink_path: Optional[str] = None):
        """
        Args:
            hub: HubState to use. Creates default if None.
            symlink_path: Optional path to symlink the slave PTY to
                         (e.g. '/tmp/spike3-sim'). Ignored on Windows.
        """
        self.hub = hub or HubState()
        self._symlink_path = symlink_path
        self._master_fd: Optional[int] = None
        self._slave_path: str = ""
        self._port: str = ""  # The path clients should connect to
        self._running = False
        self._reader_thread: Optional[threading.Thread] = None
        self._notifier_thread: Optional[threading.Thread] = None
        self._ticker_thread: Optional[threading.Thread] = None
        self._responder: Optional[ProtocolResponder] = None
        self._tunnel_handler: Optional[TunnelHandler] = None
        self._frame_acc = cobs.FrameAccumulator()
        self._write_lock = threading.Lock()

    @property
    def port(self) -> str:
        """The serial port path that clients should connect to."""
        return self._port

    def start(self):
        """Create PTY pair and start the simulator."""
        if self._running:
            return

        self._create_pty()
        self._responder = ProtocolResponder(self.hub, self._write_to_master)
        self._tunnel_handler = TunnelHandler(self.hub, self._responder)

        self._running = True

        # Reader thread: reads from master (client writes to slave)
        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True, name="sim-reader"
        )
        self._reader_thread.start()

        # Notification thread: sends periodic DeviceNotifications
        self._notifier_thread = threading.Thread(
            target=self._notifier_loop, daemon=True, name="sim-notifier"
        )
        self._notifier_thread.start()

        # Physics tick thread
        self._ticker_thread = threading.Thread(
            target=self._ticker_loop, daemon=True, name="sim-ticker"
        )
        self._ticker_thread.start()

        logger.info(f"Simulator started on {self._port}")

    def stop(self):
        """Stop the simulator and close the PTY."""
        self._running = False

        if self._reader_thread:
            self._reader_thread.join(timeout=2.0)
        if self._notifier_thread:
            self._notifier_thread.join(timeout=2.0)
        if self._ticker_thread:
            self._ticker_thread.join(timeout=2.0)

        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None

        if self._symlink_path and os.path.islink(self._symlink_path):
            try:
                os.unlink(self._symlink_path)
            except OSError:
                pass

        logger.info("Simulator stopped")

    def _create_pty(self):
        """Create a PTY pair for virtual serial communication."""
        if platform.system() == "Windows":
            raise RuntimeError(
                "PTY not available on Windows. Use com0com to create a "
                "virtual COM pair, then pass one end via --port argument."
            )

        import pty
        master_fd, slave_fd = pty.openpty()
        self._master_fd = master_fd
        self._slave_path = os.ttyname(slave_fd)
        os.close(slave_fd)  # Close slave FD; clients open by path

        # Configure master for raw mode (no line buffering, no echo)
        import termios
        attrs = termios.tcgetattr(master_fd)
        # Raw mode
        attrs[0] = 0  # iflag
        attrs[1] = 0  # oflag
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL  # cflag
        attrs[3] = 0  # lflag
        attrs[4] = termios.B115200  # ispeed
        attrs[5] = termios.B115200  # ospeed
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 1  # 100ms timeout
        termios.tcsetattr(master_fd, termios.TCSANOW, attrs)

        self._port = self._slave_path

        # Optional: create a symlink for easier access
        if self._symlink_path:
            try:
                if os.path.exists(self._symlink_path):
                    os.unlink(self._symlink_path)
                os.symlink(self._slave_path, self._symlink_path)
                self._port = self._symlink_path
                logger.info(f"Created symlink: {self._symlink_path} → {self._slave_path}")
            except OSError as e:
                logger.warning(f"Failed to create symlink: {e}")

    def _write_to_master(self, data: bytes):
        """Write COBS-framed data to the master (→ client reads from slave)."""
        if self._master_fd is None:
            return
        with self._write_lock:
            try:
                os.write(self._master_fd, data)
            except OSError as e:
                if self._running:
                    logger.error(f"Write error: {e}")

    def _reader_loop(self):
        """Read from master (client writes to slave) and dispatch messages."""
        logger.debug("Reader thread started")
        while self._running and self._master_fd is not None:
            try:
                data = os.read(self._master_fd, 4096)
                if not data:
                    time.sleep(0.01)
                    continue
                logger.debug(f"SIM RX raw ({len(data)}): {data.hex(' ')}")
                # Feed into COBS frame accumulator
                frames = self._frame_acc.feed(data)
                for decoded, high_pri in frames:
                    if decoded:
                        self._responder.handle_message(decoded)
            except OSError:
                if self._running:
                    time.sleep(0.01)
            except Exception as e:
                logger.error(f"Reader error: {e}")
                time.sleep(0.01)
        logger.debug("Reader thread exiting")

    def _notifier_loop(self):
        """Periodically send DeviceNotification if enabled."""
        logger.debug("Notifier thread started")
        while self._running:
            if self.hub.notifications_enabled:
                interval = self.hub.notification_interval_ms / 1000.0
                try:
                    self._responder.send_device_notification()
                except Exception as e:
                    logger.error(f"Notification send error: {e}")
                time.sleep(max(interval, 0.010))
            else:
                time.sleep(0.050)
        logger.debug("Notifier thread exiting")

    def _ticker_loop(self):
        """Advance physics simulation at ~100Hz."""
        dt = 0.010  # 10ms tick
        while self._running:
            try:
                self.hub.tick(dt)
            except Exception as e:
                logger.error(f"Tick error: {e}")
            time.sleep(dt)


class TcpComBridge:
    """TCP-to-PTY bridge for Windows compatibility.

    Runs a TCP server that forwards data to/from the simulator,
    allowing Windows users to use com0com or similar tools to bridge
    TCP to a virtual COM port.
    """

    def __init__(self, hub: Optional[HubState] = None,
                 host: str = "127.0.0.1", port: int = 51337):
        self.hub = hub or HubState()
        self._host = host
        self._tcp_port = port
        self._running = False
        self._responder: Optional[ProtocolResponder] = None
        self._tunnel_handler: Optional[TunnelHandler] = None
        self._server_socket = None
        self._client_socket = None
        self._frame_acc = cobs.FrameAccumulator()
        self._write_lock = threading.Lock()
        self._threads: list[threading.Thread] = []

    @property
    def port(self) -> str:
        return f"tcp://{self._host}:{self._tcp_port}"

    def start(self):
        import socket
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind((self._host, self._tcp_port))
        self._server_socket.listen(1)
        self._server_socket.settimeout(1.0)
        self._running = True

        self._responder = ProtocolResponder(self.hub, self._write_to_client)
        self._tunnel_handler = TunnelHandler(self.hub, self._responder)

        accept_thread = threading.Thread(
            target=self._accept_loop, daemon=True, name="sim-tcp-accept")
        accept_thread.start()
        self._threads.append(accept_thread)

        ticker = threading.Thread(
            target=self._ticker_loop, daemon=True, name="sim-tcp-ticker")
        ticker.start()
        self._threads.append(ticker)

        notifier = threading.Thread(
            target=self._notifier_loop, daemon=True, name="sim-tcp-notifier")
        notifier.start()
        self._threads.append(notifier)

        logger.info(f"TCP simulator listening on {self._host}:{self._tcp_port}")

    def stop(self):
        self._running = False
        if self._client_socket:
            try: self._client_socket.close()
            except: pass
        if self._server_socket:
            try: self._server_socket.close()
            except: pass
        for t in self._threads:
            t.join(timeout=2.0)

    def _write_to_client(self, data: bytes):
        if self._client_socket:
            with self._write_lock:
                try:
                    self._client_socket.sendall(data)
                except OSError:
                    pass

    def _accept_loop(self):
        while self._running:
            try:
                conn, addr = self._server_socket.accept()
                logger.info(f"Client connected from {addr}")
                self._client_socket = conn
                self._handle_client(conn)
            except TimeoutError:
                continue
            except OSError:
                break

    def _handle_client(self, conn):
        conn.settimeout(0.1)
        while self._running:
            try:
                data = conn.recv(4096)
                if not data:
                    break
                frames = self._frame_acc.feed(data)
                for decoded, _ in frames:
                    if decoded:
                        self._responder.handle_message(decoded)
            except TimeoutError:
                continue
            except OSError:
                break
        logger.info("Client disconnected")
        self._client_socket = None

    def _notifier_loop(self):
        while self._running:
            if self.hub.notifications_enabled and self._client_socket:
                interval = self.hub.notification_interval_ms / 1000.0
                try:
                    self._responder.send_device_notification()
                except Exception:
                    pass
                time.sleep(max(interval, 0.010))
            else:
                time.sleep(0.050)

    def _ticker_loop(self):
        dt = 0.010
        while self._running:
            try:
                self.hub.tick(dt)
            except Exception:
                pass
            time.sleep(dt)
