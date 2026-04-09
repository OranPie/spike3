"""Example: Motor, LED and sound control via scratch tunnel commands.

This demonstrates using the tunnel API to control motors, display images,
scroll text, and play sounds — the same commands the Scratch IDE sends.

Usage:
    python example_control.py [PORT]
"""

import sys
import time
import logging

sys.path.insert(0, "..")

from spike3 import Hub, NotifSubId

logging.basicConfig(level=logging.INFO, format="%(name)s [%(levelname)s]: %(message)s")


# Heart image for 5×5 matrix (brightness 0-9, row-major)
HEART = "0909009090900009009000900"
SMILEY = "0900900000090900000099900"


def main():
    if len(sys.argv) > 1:
        port = sys.argv[1]
    else:
        hubs = Hub.find_hubs()
        if not hubs:
            print("No SPIKE hub found.")
            sys.exit(1)
        port = hubs[0]["port"]
        print(f"Found hub: {hubs[0]}")

    print(f"Connecting to {port}...")
    with Hub.connect_usb(port) as hub:
        info = hub.get_info()
        print(f"Hub: FW {info.fw_major}.{info.fw_minor}.{info.fw_build}")

        # Enable notifications so we can read sensor data
        hub.set_notification_interval(100)
        time.sleep(0.5)  # Let first notifications arrive

        # ── Display demos ──────────────────────────────────────────
        print("\n--- LED Matrix ---")
        print("Displaying heart...")
        hub.display_image(HEART)
        time.sleep(2)

        print("Displaying smiley...")
        hub.display_image(SMILEY)
        time.sleep(2)

        print("Scrolling text...")
        hub.display_text("Hello SPIKE!")
        time.sleep(3)

        print("Setting individual pixels...")
        hub.display_clear()
        for i in range(5):
            hub.display_set_pixel(i, i, 100)  # Diagonal line
            time.sleep(0.2)
        time.sleep(1)
        hub.display_clear()

        # ── Sound demos ────────────────────────────────────────────
        print("\n--- Sound ---")
        print("Playing C major scale...")
        # MIDI notes: C4=60, D4=62, E4=64, F4=65, G4=67, A4=69, B4=71, C5=72
        for note in [60, 62, 64, 65, 67, 69, 71, 72]:
            hub.sound_beep_for(80, note, 300)
            time.sleep(0.35)
        hub.sound_off()

        # ── Motor demos ───────────────────────────────────────────
        print("\n--- Motors ---")
        motors = hub.get_all_motors()
        if not motors:
            print("No motors detected. Connect a motor to see motor demos.")
        else:
            for port_num, m in motors.items():
                print(f"Motor on port {port_num}: pos={m.position}")

            first_port = next(iter(motors))
            print(f"\nUsing motor on port {first_port}:")

            print("  Running at speed 50 for 2 seconds...")
            hub.motor_start(first_port, 50)
            time.sleep(2)
            hub.motor_stop(first_port)
            time.sleep(0.5)

            print("  Running 360 degrees at speed 30...")
            hub.motor_run_degrees(first_port, 30, 360)
            time.sleep(3)

            print("  Running for 1 second at speed -40...")
            hub.motor_run_timed(first_port, -40, 1000)
            time.sleep(1.5)

            print("  Going to position 0...")
            hub.motor_go_to_position(first_port, 20, 0)
            time.sleep(2)

        # ── Sensor polling demo ────────────────────────────────────
        print("\n--- Sensor Polling ---")
        battery = hub.get_battery()
        print(f"Battery: {battery}%")

        imu = hub.get_imu()
        if imu:
            print(f"IMU: yaw={imu.yaw} pitch={imu.pitch} roll={imu.roll}")

        for port_num, m in hub.get_all_motors().items():
            print(f"Motor {port_num}: pos={m.position} speed={m.speed}")

        print("\nDone!")


if __name__ == "__main__":
    main()
