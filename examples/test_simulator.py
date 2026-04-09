"""Integration test: spike3 library ↔ simulator via PTY pair.

Starts the simulator on a PTY, connects the spike3.Hub to the slave end,
and verifies:
  1. get_info() returns correct FW version
  2. get_hub_name() returns hub name
  3. DeviceNotifications arrive and contain sensor data
  4. Motor tunnel commands work
  5. Display commands work
"""

from __future__ import annotations

import logging
import os
import sys
import time

# Add parent directory to path for dev use
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from spike3.simulator import ComServer, HubState
from spike3.hub import Hub

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(name)-25s [%(levelname)s]: %(message)s"
)
# Reduce noise
logging.getLogger("spike3.transport").setLevel(logging.WARNING)
logging.getLogger("spike3.simulator.com").setLevel(logging.WARNING)
logging.getLogger("spike3.simulator.responder").setLevel(logging.WARNING)
logging.getLogger("spike3.simulator.tunnel").setLevel(logging.WARNING)

passed = 0
failed = 0


def test(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))


def main():
    global passed, failed

    print("=" * 60)
    print("SPIKE 3 Simulator Integration Test")
    print("=" * 60)

    # 1. Start simulator
    print("\n[1] Starting simulator...")
    hub_state = HubState(name="TestHub")
    server = ComServer(hub_state, symlink_path="/tmp/spike3-test")
    server.start()
    time.sleep(0.3)

    port = server.port
    print(f"    Simulator on: {port}")
    test("Simulator started", server._running)
    test("Port path exists", bool(port))

    # 2. Connect Hub
    print("\n[2] Connecting Hub...")
    try:
        hub = Hub.connect_usb(port, baudrate=115200)
        time.sleep(0.5)
        test("Hub connected", True)
    except Exception as e:
        test("Hub connected", False, str(e))
        server.stop()
        return

    # 3. get_info()
    print("\n[3] Testing get_info()...")
    try:
        info = hub.get_info(timeout=3.0)
        test("get_info() returns data", info is not None)
        if info:
            fw_ver = f"{info.fw_major}.{info.fw_minor}.{info.fw_build}"
            rpc_ver = f"{info.rpc_major}.{info.rpc_minor}.{info.rpc_build}"
            test("FW version correct",
                 fw_ver == "1.8.149",
                 f"got {fw_ver}")
            test("RPC version correct",
                 rpc_ver == "1.0.47",
                 f"got {rpc_ver}")
            test("Max packet size",
                 info.max_packet_size == 512,
                 f"got {info.max_packet_size}")
    except Exception as e:
        test("get_info()", False, str(e))

    # 4. get_hub_name()
    print("\n[4] Testing get_hub_name()...")
    try:
        name = hub.get_hub_name(timeout=3.0)
        test("get_hub_name() returns name", name is not None)
        test("Hub name correct",
             name == "TestHub",
             f"got '{name}'")
    except Exception as e:
        test("get_hub_name()", False, str(e))

    # 5. Device notifications
    print("\n[5] Testing DeviceNotifications...")
    try:
        hub.set_notification_interval(50, timeout=3.0)
        time.sleep(0.5)

        notifs = hub._latest_notifs
        test("Notifications received", len(notifs) > 0,
             f"got {len(notifs)} types")

        battery = hub.get_battery()
        test("Battery level readable",
             battery is not None and battery > 0,
             f"got {battery}")

        imu = hub.get_imu()
        test("IMU data readable", imu is not None)
    except Exception as e:
        test("Notifications", False, str(e))

    # 6. Motor commands via tunnel
    print("\n[6] Testing motor commands...")
    try:
        hub.motor_start(0, 50)
        time.sleep(0.3)
        motor = hub_state.get_motor(0)
        test("Motor A started",
             motor is not None and motor._running,
             f"running={motor._running if motor else '?'}")

        hub.motor_stop(0)
        time.sleep(0.2)
        test("Motor A stopped",
             motor is not None and not motor._running)
    except Exception as e:
        test("Motor commands", False, str(e))

    # 7. Display commands
    print("\n[7] Testing display commands...")
    try:
        hub.display_image("9" * 25)
        time.sleep(0.2)
        test("Matrix pixels set",
             all(p > 0 for p in hub_state.matrix.pixels),
             f"pixels={hub_state.matrix.pixels[:5]}...")

        hub.display_clear()
        time.sleep(0.2)
        test("Matrix cleared",
             all(p == 0 for p in hub_state.matrix.pixels))
    except Exception as e:
        test("Display commands", False, str(e))

    # 8. Tank drive (motor pair)
    print("\n[8] Testing tank drive...")
    try:
        hub.move_start_speeds(0, 1, 50, -50)
        time.sleep(0.3)
        lm = hub_state.get_motor(0)
        rm = hub_state.get_motor(1)
        test("Left motor running", lm is not None and lm._running)
        test("Right motor running", rm is not None and rm._running)
        hub.move_stop(0, 1)
        time.sleep(0.2)
        test("Both motors stopped",
             (lm and not lm._running) and (rm and not rm._running))
    except Exception as e:
        test("Tank drive", False, str(e))

    # 9. Hub light and IMU reset
    print("\n[9] Testing hub control...")
    try:
        hub.hub_light_on(9)  # Red
        time.sleep(0.1)
        test("Hub light command sent", True)

        hub_state.imu.yaw = 45
        hub.reset_yaw()
        time.sleep(0.2)
        test("Yaw reset",
             hub_state.imu.yaw == 0,
             f"got {hub_state.imu.yaw}")
    except Exception as e:
        test("Hub control", False, str(e))

    # 10. Sensor manipulation from simulator side
    print("\n[10] Testing sensor manipulation...")
    from spike3.simulator.devices import ColorSensor, DistanceSensor
    from spike3.enums import Color

    cs = ColorSensor(2)
    cs.set_color(Color.RED)
    hub_state.attach_device(2, cs)
    time.sleep(0.5)
    test("Color sensor attached", hub_state.ports[2] is not None)

    ds = DistanceSensor(3)
    ds.set_distance(150)
    hub_state.attach_device(3, ds)
    time.sleep(0.5)
    test("Distance sensor attached", hub_state.ports[3] is not None)

    # 11. Cleanup
    print("\n[11] Cleanup...")
    try:
        hub.set_notification_interval(0)
        time.sleep(0.1)
    except:
        pass
    hub.close()
    server.stop()
    test("Disconnected cleanly", True)

    # Summary
    print("\n" + "=" * 60)
    total = passed + failed
    print(f"Results: {passed}/{total} passed, {failed} failed")
    if failed == 0:
        print("🎉 ALL TESTS PASSED!")
    else:
        print(f"⚠️  {failed} test(s) failed")
    print("=" * 60)
    return failed == 0


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
