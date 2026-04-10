"""Example: reading all sensor types from a connected SPIKE 3 hub.

Demonstrates color sensor, ultrasonic distance, force sensor, IMU,
buttons, motor encoder, and hub temperature APIs.

Usage:
    python examples/example_sensors.py
    python examples/example_sensors.py COM3
"""

import sys
import time
import spike3
from spike3.enums import Color, DeviceType, Gesture, Port


def print_section(title: str):
    print(f"\n{'─' * 50}")
    print(f"  {title}")
    print('─' * 50)


def main():
    port_arg = sys.argv[1] if len(sys.argv) > 1 else None

    if port_arg:
        hub = spike3.Hub.connect_usb(port_arg)
    else:
        hub = spike3.Hub.connect_usb()

    print(f"✓ Connected to hub: {hub.get_hub_name()}")

    # ── Hub info ────────────────────────────────────────────────────
    print_section("Hub Info")
    info = hub.get_info()
    print(f"  Firmware   : {info.fw_major}.{info.fw_minor}.{info.fw_build}")
    print(f"  Battery    : {hub.get_battery()}%")
    print(f"  Temperature: {hub.temperature()}°C")
    print(f"  Charger    : {'Yes' if hub.charger_connected() else 'No'}")
    try:
        uuid_str = hub.get_device_uuid()
        print(f"  UUID       : {uuid_str}")
    except Exception:
        pass

    # ── IMU ─────────────────────────────────────────────────────────
    print_section("IMU (from DeviceNotification)")
    imu = hub.get_imu()
    if imu:
        print(f"  Orientation: {imu.orientation}")
        print(f"  Yaw        : {imu.yaw}°")
        print(f"  Pitch      : {imu.pitch}°")
        print(f"  Roll       : {imu.roll}°")
        print(f"  Accel X/Y/Z: {imu.accel_x}, {imu.accel_y}, {imu.accel_z}")
    else:
        print("  (no IMU data yet — enable notifications first)")

    # ── IMU via eval_python ──────────────────────────────────────────
    print_section("IMU (via eval_python)")
    print(f"  Tilt angles: {hub.eval_python('hub.motion_sensor.tilt_angles()')}")
    print(f"  Accel      : {hub.eval_python('hub.motion_sensor.acceleration()')}")
    print(f"  Gyro       : {hub.eval_python('hub.motion_sensor.gyroscope()')}")
    print(f"  Gesture    : {Gesture(hub.get_gesture()).name}")

    # ── Buttons ─────────────────────────────────────────────────────
    print_section("Buttons")
    print(f"  Left  pressed  : {hub.left_button_pressed()}")
    print(f"  Right pressed  : {hub.right_button_pressed()}")
    print(f"  Left  was pressed (cleared): {hub.left_button_was_pressed()}")
    print(f"  Right was pressed (cleared): {hub.right_button_was_pressed()}")

    # ── Port device detection ────────────────────────────────────────
    print_section("Connected Ports (from DeviceNotification)")
    port_info = hub.get_port_info()
    port_labels = {0: "A", 1: "B", 2: "C", 3: "D", 4: "E", 5: "F"}
    if port_info:
        for p, dev_id in sorted(port_info.items()):
            try:
                dev_name = DeviceType(dev_id).name
            except ValueError:
                dev_name = f"unknown(0x{dev_id:02x})"
            print(f"  Port {port_labels[p]}: {dev_name}")
    else:
        print("  (no devices detected — ensure notifications are enabled)")

    # ── Per-sensor readings ──────────────────────────────────────────
    print_section("Sensor Readings (uses eval_python on connected devices)")
    print("  Note: readings will return 0/-1 if no sensor on that port.\n")

    for port_idx in range(6):
        label = port_labels[port_idx]
        dev_id = port_info.get(port_idx)
        if dev_id is None:
            continue

        try:
            dev_type = DeviceType(dev_id)
        except ValueError:
            continue

        if dev_type in (DeviceType.COLOR_SENSOR,):
            color_id = hub.color(port_idx)
            try:
                color_name = Color(color_id).name
            except ValueError:
                color_name = f"id={color_id}"
            refl = hub.reflection(port_idx)
            r, g, b = hub.raw_rgb(port_idx)
            print(f"  Port {label} Color sensor:")
            print(f"    Color      : {color_name}")
            print(f"    Reflection : {refl}%")
            print(f"    Raw RGB    : ({r}, {g}, {b})")

        elif dev_type in (DeviceType.DISTANCE_SENSOR,):
            d = hub.distance(port_idx)
            print(f"  Port {label} Distance sensor: {d} mm")

        elif dev_type in (DeviceType.FORCE_SENSOR,):
            f_val = hub.force(port_idx)
            pressed = hub.is_pressed(port_idx)
            print(f"  Port {label} Force sensor: {f_val:.1f} N, pressed={pressed}")

        elif dev_type in (DeviceType.MOTOR_MEDIUM, DeviceType.MOTOR_LARGE,
                          DeviceType.MOTOR_SMALL):
            pos = hub.motor_position(port_idx)
            spd = hub.motor_speed(port_idx)
            stalled = hub.motor_is_stalled(port_idx)
            print(f"  Port {label} Motor: pos={pos}° speed={spd} deg/s stalled={stalled}")

    hub.disconnect()
    print("\n✓ Disconnected")


if __name__ == "__main__":
    main()
