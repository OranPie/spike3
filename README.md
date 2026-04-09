# spike3 — Python Communication Library for LEGO SPIKE 3 Hubs

A reverse-engineered Python library for two-way communication with LEGO Education SPIKE 3 (Flipper) hubs over USB serial and BLE.

Implements the **Atlantis binary protocol** as used by the SPIKE App v3.6.0, including sensor notifications, motor/LED/sound control via scratch tunnel commands, file management, and program flow control.

Includes a **full hardware simulator** with physics engine, virtual COM port, BLE peripheral, interactive CLI, and rich TUI dashboard.

## Quick Start

```python
from spike3 import Hub

# Connect via USB (auto-detect or specify port)
with Hub.connect_usb() as hub:
    info = hub.get_info()
    print(f"Firmware: {info.fw_major}.{info.fw_minor}.{info.fw_build}")
    print(f"Name: {hub.get_hub_name()}")

    # Enable sensor notifications
    hub.set_notification_interval(50)

    import time; time.sleep(0.5)
    print(f"Battery: {hub.get_battery()}%")

    # Motor control (port 0 = A)
    hub.motor_start(0, 50)
    time.sleep(2)
    hub.motor_stop(0)

    # LED matrix — heart
    hub.display_image('0909009090900009009000900')

    # Sound — Middle C for 500ms
    hub.sound_beep_for(100, 60, 500)
```

## Installation

```bash
pip install -e .          # Install from source
pip install -e ".[ble]"   # Include BLE support (bleak)
```

## Features

| Feature | Status |
|---------|--------|
| USB serial connection | ✅ Working (tested on real hardware) |
| BLE connection (Atlantis GATT) | ✅ Implemented |
| Hub info / name / UUID | ✅ Working |
| Sensor notifications (IMU, motors, color, distance, force, matrix) | ✅ Working |
| Motor control (start, stop, degrees, timed, position, tank drive) | ✅ Via tunnel |
| LED display (image, text, pixel, color matrix) | ✅ Via tunnel |
| Sound (beep, note, off) | ✅ Via tunnel |
| Hub light on/off | ✅ Via tunnel |
| Yaw reset, orientation set | ✅ Via tunnel |
| File upload / download | ✅ Protocol implemented |
| Program flow (start, stop) | ✅ Working |
| Slot management (clear, move, list, delete) | ✅ Working |
| COBS framing (modified, XOR=3) | ✅ Verified against JS source |
| DeviceNotification sub-parsing (8 types) | ✅ Working |
| **Hardware simulator** | ✅ Full implementation |
| **TUI dashboard** | ✅ Rich Textual UI |

## Architecture

```
┌─────────────────────────────────────┐
│             Hub (hub.py)            │  71 methods — high-level API
│  motor_start() display_image() ..   │
├──────────────┬──────────────────────┤
│ Atlantis     │ Tunnel (tunnel.py)   │  Protocol layers
│ (atlantis.py)│ 38 scratch.* cmds    │
├──────────────┴──────────────────────┤
│          COBS Framing (cobs.py)     │  Modified COBS codec
├─────────────────────────────────────┤
│     Transport (transport.py)        │  USB serial / BLE
└─────────────────────────────────────┘

┌─────────────────────────────────────┐
│         Simulator (simulator/)      │  Fake hardware
├────────┬──────────┬────────┬────────┤
│Devices │ Physics  │Storage │Protocol│
│8 types │ 100Hz    │20 slots│Respond │
├────────┴──────────┴────────┴────────┤
│  COM Server (PTY)  │  BLE Server    │
├────────────────────┼────────────────┤
│  Interactive CLI    │  Textual TUI   │
└────────────────────┴────────────────┘
```

## Simulator

The simulator creates a fake SPIKE hub accessible over a virtual COM port (Linux/macOS) or TCP (Windows). The real SPIKE App or this library can connect to it.

```bash
# Basic CLI
python -m spike3.simulator

# Rich TUI dashboard
python -m spike3.simulator --tui

# With pre-built scenario
python -m spike3.simulator --tui --scenario robot

# TCP mode (Windows)
python -m spike3.simulator --tcp --tcp-port 51337

# BLE peripheral
python -m spike3.simulator --ble

# Debug logging
python -m spike3.simulator --debug
```

### Simulator Features

- **8 device types**: Motor (with physics), ColorSensor, DistanceSensor, ForceSensor, IMU (with noise), 5×5 Matrix, InfoHub, ColorMatrix
- **Physics engine**: 100Hz tick, motor ramp, brake/float/hold, gyro drift
- **20-slot storage**: Upload/download with CRC32 validation
- **Full Atlantis protocol**: Handles all 16 message types + 32 tunnel commands
- **Virtual COM port**: PTY pair with symlink for easy connection
- **TCP bridge**: For Windows or remote connections
- **BLE peripheral**: GATT server advertising as SPIKE hub
- **5 scenario presets**: Robot, Color Sorter, Distance Alarm, Music Box, Line Follower
- **Event recording/replay**: Capture and playback simulator events
- **18 interactive CLI commands**: attach, detach, motor, imu, sensor, color, etc.

### TUI Dashboard

The Textual TUI provides real-time visualization:
- Hub status bar with connection state and battery
- 5×5 LED matrix ASCII display
- Motor gauges per port (speed, position, stall detection)
- Sensor panels (color, distance, force values)
- IMU display (yaw, pitch, roll, accelerometer)
- Live protocol log stream
- Keyboard shortcuts for interactive control

## Protocol Details

The library implements the **Atlantis** binary protocol used by SPIKE 3 firmware:

- **Transport**: USB serial at 115200 baud (VID=0x0694, PID=0x0009) or BLE GATT service `0000fd02-...`
- **Framing**: Modified COBS with XOR=3, max_run=83, implicit stride=2
- **Messages**: Request/response pairs identified by msg_id bytes
- **Notifications**: Periodic DeviceNotification with sub-notifications per sensor/device
- **Tunnel**: Bidirectional channel carrying JSON-RPC `scratch.*` commands to MicroPython runtime

## Examples

See the `examples/` directory:
- `example_usb.py` — Connect, read sensors, display notifications
- `example_control.py` — Motor, LED, and sound control
- `example_ble.py` — BLE connection
- `example_upload.py` — Upload a program file
- `test_simulator.py` — Integration test suite (24 tests)

## Project Stats

- **17+ Python modules**, ~5500 lines of code
- **71 Hub methods**, 38 tunnel command builders
- **32 simulator tunnel handlers**, 16 Atlantis message handlers
- **24/24 integration tests** passing end-to-end

## License

MIT
