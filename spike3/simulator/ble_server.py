"""BLE GATT Peripheral server for the SPIKE 3 simulator.

Advertises as a SPIKE hub using the Atlantis BLE service UUID
(0000fd02-0000-1000-8000-00805f9b34fb) so the real SPIKE App or
the spike3 library can discover and connect wirelessly.

Requires:
  - bless library (pip install bless)
  - Linux with BlueZ 5.43+ or macOS with CoreBluetooth

Usage::

    from spike3.simulator import HubState
    from spike3.simulator.ble_server import BleServer

    hub = HubState(name="BLE Simulator")
    ble = BleServer(hub)
    await ble.start()
    # Hub is now discoverable as "BLE Simulator"
    await ble.stop()

Or from CLI::

    python -m spike3.simulator --ble
"""

from __future__ import annotations

import asyncio
import logging
import struct
import threading
import time
from typing import Optional, Callable

from ..enums import (
    ATLANTIS_SERVICE_UUID,
    ATLANTIS_TX_CHAR_UUID,
    ATLANTIS_RX_CHAR_UUID,
    ATLANTIS_DESIRED_MTU,
)
from .. import cobs
from .hub_state import HubState
from .responder import ProtocolResponder
from .tunnel_handler import TunnelHandler
from .console_handler import ConsoleHandler

logger = logging.getLogger("spike3.simulator.ble")


class BleServer:
    """BLE GATT peripheral that simulates a SPIKE 3 hub.

    Exposes the Atlantis BLE service with TX and RX characteristics.
    The host writes to TX_CHAR (host→hub) and reads/subscribes to
    RX_CHAR (hub→host) via notifications.

    Args:
        hub: HubState instance to serve.
        adapter: BLE adapter name (Linux only, e.g. "hci0").
    """

    def __init__(self, hub: Optional[HubState] = None,
                 adapter: Optional[str] = None):
        self.hub = hub or HubState()
        self._adapter = adapter
        self._server = None  # bless.BlessServer
        self._responder: Optional[ProtocolResponder] = None
        self._tunnel_handler: Optional[TunnelHandler] = None
        self._console_handler: Optional[ConsoleHandler] = None
        self._frame_acc = cobs.FrameAccumulator()
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._notifier_task: Optional[asyncio.Task] = None
        self._ticker_task: Optional[asyncio.Task] = None
        self._subscribed = False

    async def start(self):
        """Start the BLE GATT server and begin advertising."""
        try:
            from bless import BlessServer, BlessGATTCharacteristic, GATTCharacteristicProperties, GATTAttributePermissions
        except ImportError:
            raise ImportError(
                "bless library required for BLE server. "
                "Install with: pip install bless"
            )

        self._loop = asyncio.get_event_loop()
        self._responder = ProtocolResponder(self.hub, self._send_ble_data)
        self._tunnel_handler = TunnelHandler(self.hub, self._responder)
        self._console_handler = ConsoleHandler(self.hub, self._responder)

        # Create GATT server
        self._server = BlessServer(
            name=self.hub.name,
            name_overwrite=True,
        )

        # Set up read/write request handlers
        self._server.read_request_func = self._on_read
        self._server.write_request_func = self._on_write

        # Add the Atlantis service and characteristics
        await self._server.add_new_service(ATLANTIS_SERVICE_UUID)

        # TX characteristic (host → hub): Write, Write Without Response
        await self._server.add_new_characteristic(
            ATLANTIS_SERVICE_UUID,
            ATLANTIS_TX_CHAR_UUID,
            GATTCharacteristicProperties.write
            | GATTCharacteristicProperties.write_without_response,
            None,
            GATTAttributePermissions.writeable,
        )

        # RX characteristic (hub → host): Read, Notify
        await self._server.add_new_characteristic(
            ATLANTIS_SERVICE_UUID,
            ATLANTIS_RX_CHAR_UUID,
            GATTCharacteristicProperties.read
            | GATTCharacteristicProperties.notify,
            None,
            GATTAttributePermissions.readable,
        )

        # Start advertising
        await self._server.start()
        self._running = True

        logger.info(f"BLE server started: '{self.hub.name}' "
                     f"advertising {ATLANTIS_SERVICE_UUID}")

        # Start background tasks
        self._notifier_task = asyncio.create_task(self._notifier_loop())
        self._ticker_task = asyncio.create_task(self._ticker_loop())

    async def stop(self):
        """Stop the BLE server."""
        self._running = False
        if self._notifier_task:
            self._notifier_task.cancel()
        if self._ticker_task:
            self._ticker_task.cancel()
        if self._server:
            await self._server.stop()
        logger.info("BLE server stopped")

    def _on_read(self, characteristic, **kwargs):
        """Handle read requests on characteristics."""
        logger.debug(f"BLE read on {characteristic.uuid}")
        # RX char: return empty (data is pushed via notifications)
        return bytearray(b"")

    def _on_write(self, characteristic, value, **kwargs):
        """Handle write requests (host→hub data on TX char)."""
        if str(characteristic.uuid).lower() == ATLANTIS_TX_CHAR_UUID.lower():
            data = bytes(value)
            logger.debug(f"BLE RX ({len(data)}): {data.hex(' ')}")

            # Check for subscribe indication (CCCD write)
            # bless handles CCCD internally; we detect subscription
            # by the first write to TX characteristic
            self._subscribed = True

            # Feed into COBS decoder
            frames = self._frame_acc.feed(data)
            for decoded, high_pri in frames:
                if decoded:
                    self._responder.handle_message(decoded)
        else:
            logger.debug(f"BLE write on unknown char: {characteristic.uuid}")

    def _send_ble_data(self, data: bytes):
        """Send COBS-framed data back to host via BLE notification."""
        if not self._server or not self._running:
            return

        # BLE has MTU limits — fragment if needed
        mtu = ATLANTIS_DESIRED_MTU  # Use desired MTU as max
        offset = 0
        while offset < len(data):
            chunk = data[offset:offset + mtu]
            try:
                self._server.get_characteristic(ATLANTIS_RX_CHAR_UUID)
                self._server.update_value(
                    ATLANTIS_SERVICE_UUID,
                    ATLANTIS_RX_CHAR_UUID,
                )
                # Set the value and notify
                char = self._server.get_characteristic(ATLANTIS_RX_CHAR_UUID)
                if char:
                    char.value = bytearray(chunk)
                    if self._loop and self._loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            self._server.notify(ATLANTIS_RX_CHAR_UUID, chunk),
                            self._loop,
                        )
            except Exception as e:
                logger.error(f"BLE send error: {e}")
                break
            offset += len(chunk)

    async def _notifier_loop(self):
        """Periodically send DeviceNotification via BLE."""
        while self._running:
            if self.hub.notifications_enabled and self._subscribed:
                interval = self.hub.notification_interval_ms / 1000.0
                try:
                    self._responder.send_device_notification()
                except Exception as e:
                    logger.error(f"BLE notif error: {e}")
                await asyncio.sleep(max(interval, 0.020))
            else:
                await asyncio.sleep(0.050)

    async def _ticker_loop(self):
        """Advance physics at ~100Hz."""
        dt = 0.010
        while self._running:
            try:
                self.hub.tick(dt)
            except Exception:
                pass
            await asyncio.sleep(dt)


def run_ble_server(hub: HubState):
    """Run the BLE server synchronously (blocks until stopped)."""
    async def _run():
        server = BleServer(hub)
        await server.start()
        print(f"  BLE simulator advertising as: {hub.name}")
        print(f"  Service UUID: {ATLANTIS_SERVICE_UUID}")
        print("  Press Ctrl-C to stop...")
        try:
            while True:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await server.stop()

    asyncio.run(_run())


class BleBridge:
    """Combined COM + BLE server for maximum compatibility.

    Runs both a PTY/TCP server and BLE peripheral simultaneously,
    sharing the same HubState so changes from either transport
    are reflected in both.
    """

    def __init__(self, hub: Optional[HubState] = None,
                 symlink_path: str = "/tmp/spike3-sim"):
        self.hub = hub or HubState()
        self._symlink_path = symlink_path
        self._com_server = None
        self._ble_server = None
        self._ble_thread = None

    def start(self):
        """Start both COM and BLE servers."""
        from .com_server import ComServer
        self._com_server = ComServer(self.hub, symlink_path=self._symlink_path)
        self._com_server.start()

        # BLE server runs in its own thread with its own event loop
        self._ble_server = BleServer(self.hub)
        self._ble_thread = threading.Thread(
            target=self._run_ble, daemon=True, name="ble-server"
        )
        self._ble_thread.start()

        logger.info(f"Bridge started: COM={self._com_server.port}, BLE={self.hub.name}")

    def _run_ble(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._ble_server.start())
            loop.run_forever()
        except Exception as e:
            logger.error(f"BLE server error: {e}")
        finally:
            loop.close()

    def stop(self):
        if self._com_server:
            self._com_server.stop()
        if self._ble_server and self._ble_server._loop:
            asyncio.run_coroutine_threadsafe(
                self._ble_server.stop(),
                self._ble_server._loop,
            )

    @property
    def port(self) -> str:
        return self._com_server.port if self._com_server else ""
