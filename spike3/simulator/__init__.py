"""spike3.simulator — Fake SPIKE 3 Hub Simulator.

Provides a software simulation of a LEGO SPIKE 3 hub that communicates
over a virtual COM port (PTY pair) or BLE peripheral, implementing the
full Atlantis binary protocol. The real SPIKE App or the spike3 Python
library can connect to it as if it were real hardware.

Quick start::

    from spike3.simulator import ComServer, HubState

    hub = HubState()
    server = ComServer(hub)
    server.start()
    print(f"Connect to: {server.port}")
    # Now connect with: Hub.connect_usb(server.port)

Interactive CLI::

    python -m spike3.simulator          # Basic CLI
    python -m spike3.simulator --tui    # Rich TUI dashboard
"""

from .hub_state import HubState
from .devices import (
    Motor, ColorSensor, DistanceSensor, ForceSensor,
    IMU, Matrix5x5, DeviceBase,
)
from .storage import SlotStorage
from .com_server import ComServer, TcpComBridge
from .responder import ProtocolResponder
from .tunnel_handler import TunnelHandler
from .console_handler import ConsoleHandler

__all__ = [
    "HubState", "Motor", "ColorSensor", "DistanceSensor", "ForceSensor",
    "IMU", "Matrix5x5", "DeviceBase", "SlotStorage",
    "ComServer", "TcpComBridge", "ProtocolResponder", "TunnelHandler", "ConsoleHandler",
]
