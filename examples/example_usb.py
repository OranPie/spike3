"""Example: Connect to SPIKE 3 hub via USB and read sensor data.

Usage:
    python example_usb.py [PORT]

    PORT defaults to auto-detect (first LEGO SPIKE device found).
"""

import sys
import time
import logging

# Add parent dir to path for development
sys.path.insert(0, "..")

from spike3 import Hub, NotifSubId

logging.basicConfig(level=logging.DEBUG, format="%(name)s [%(levelname)s]: %(message)s")


def on_notification(msg):
    """Handle device notifications from the hub."""
    for notif in msg.notifications:
        if notif.sub_id == NotifSubId.INFO_HUB:
            print(f"  Battery: {notif.battery_level}%")
        elif notif.sub_id == NotifSubId.IMU_HUB:
            print(
                f"  IMU: orientation={notif.orientation} "
                f"yaw={notif.yaw} pitch={notif.pitch} roll={notif.roll} "
                f"accel=({notif.accel_x},{notif.accel_y},{notif.accel_z})"
            )
        elif notif.sub_id == NotifSubId.MATRIX_HUB:
            # Show 5×5 LED matrix as compact grid
            img = notif.image
            rows = [img[r*5:(r+1)*5] for r in range(5)]
            grid = " | ".join(" ".join(f"{p:3d}" for p in row) for row in rows)
            print(f"  Matrix: [{grid}]")
        elif notif.sub_id == NotifSubId.MOTOR:
            print(
                f"  Motor port {notif.port}: "
                f"pos={notif.position} speed={notif.speed} power={notif.power}"
            )
        elif notif.sub_id == NotifSubId.COLOR_SENSOR:
            print(
                f"  Color port {notif.port}: "
                f"color={notif.color} reflection={notif.reflection} "
                f"RGB=({notif.raw_red},{notif.raw_green},{notif.raw_blue})"
            )
        elif notif.sub_id == NotifSubId.DISTANCE_SENSOR:
            print(f"  Distance port {notif.port}: {notif.distance}mm")
        elif notif.sub_id == NotifSubId.FORCE_SENSOR:
            print(
                f"  Force port {notif.port}: "
                f"force={notif.force} touch={notif.touch}"
            )
        elif notif.sub_id == NotifSubId.COLOR_MATRIX:
            img = notif.image
            print(f"  ColorMatrix port {notif.port}: {img}")
        else:
            print(f"  Unknown sub_id={notif.sub_id}")


def on_console(text):
    """Handle console output from running programs."""
    print(f"  Console: {text}")


def main():
    # Find port
    if len(sys.argv) > 1:
        port = sys.argv[1]
    else:
        hubs = Hub.find_hubs()
        if not hubs:
            print("No SPIKE hub found. Connect via USB and retry.")
            print("Or specify port: python example_usb.py /dev/ttyACM0")
            sys.exit(1)
        port = hubs[0]["port"]
        print(f"Found hub: {hubs[0]}")

    print(f"Connecting to {port}...")
    with Hub.connect_usb(port) as hub:
        # 1. Handshake — get hub info
        info = hub.get_info()
        print(f"Hub firmware: {info.fw_major}.{info.fw_minor}.{info.fw_build}")
        print(f"RPC version:  {info.rpc_major}.{info.rpc_minor}.{info.rpc_build}")
        print(f"Max packet:   {info.max_packet_size} bytes")
        print(f"Product:      {info.product_group_device}")

        # 2. Get hub name
        name = hub.get_hub_name()
        print(f"Hub name: {name}")

        # 3. Enable sensor notifications every 100ms
        hub.on_notification = on_notification
        hub.on_console = on_console
        hub.set_notification_interval(100)
        print("Streaming sensor data (Ctrl-C to stop)...")

        # 4. Read notifications for 30 seconds
        try:
            time.sleep(30)
        except KeyboardInterrupt:
            print("\nStopped.")

    print("Disconnected.")


if __name__ == "__main__":
    main()
