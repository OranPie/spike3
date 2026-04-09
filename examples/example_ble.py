"""Example: Connect to SPIKE 3 hub via BLE and read sensor data.

Usage:
    python example_ble.py [ADDRESS]

    ADDRESS is the BLE device address. If omitted, scans for hubs.
    Requires: pip install bleak
"""

import asyncio
import sys
import time
import logging

sys.path.insert(0, "..")

from spike3 import Hub, BleTransport, NotifSubId

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")


def on_notification(msg):
    for notif in msg.notifications:
        if notif.sub_id == NotifSubId.INFO_HUB:
            print(f"  Battery: {notif.battery_level}%")
        elif notif.sub_id == NotifSubId.IMU_HUB:
            print(f"  IMU: yaw={notif.yaw} pitch={notif.pitch} roll={notif.roll}")


async def main():
    if len(sys.argv) > 1:
        address = sys.argv[1]
    else:
        print("Scanning for SPIKE hubs via BLE...")
        devices = await BleTransport.scan(timeout=10.0)
        if not devices:
            print("No SPIKE hub found via BLE.")
            print("Make sure hub is powered on and in pairing mode.")
            sys.exit(1)
        address = devices[0]["address"]
        print(f"Found: {devices[0]}")

    print(f"Connecting to {address}...")
    hub = Hub.connect_ble(address)

    try:
        info = hub.get_info()
        print(f"Hub firmware: {info.fw_major}.{info.fw_minor}.{info.fw_build}")

        name = hub.get_hub_name()
        print(f"Hub name: {name}")

        hub.on_notification = on_notification
        hub.set_notification_interval(100)
        print("Streaming sensor data (Ctrl-C to stop)...")

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        hub.close()


if __name__ == "__main__":
    asyncio.run(main())
