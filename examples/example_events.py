"""Example: event-driven button and gesture polling on SPIKE 3.

Shows how to use wait_for_button(), the gesture API, and callbacks.

Usage:
    python examples/example_events.py
    python examples/example_events.py COM3
"""

import sys
import time
import spike3
from spike3.enums import Gesture


GESTURE_NAMES = {
    Gesture.NONE: "none",
    Gesture.SHAKE: "shake",
    Gesture.FREEFALL: "freefall",
    Gesture.TAPPED: "tapped",
    Gesture.DOUBLE_TAPPED: "double-tapped",
}


def main():
    port_arg = sys.argv[1] if len(sys.argv) > 1 else None

    if port_arg:
        hub = spike3.Hub.connect_usb(port_arg)
    else:
        hub = spike3.Hub.connect_usb()

    print(f"✓ Connected to: {hub.get_hub_name()}")

    # Register notification callback for asynchronous sensor streaming
    def on_notif(notif):
        pass  # raw notification; use get_imu() etc. to read latest

    hub.on_notification = on_notif

    # Register console output callback
    def on_console(text: str):
        if text.strip() and text.strip() not in (">>>", "..."):
            print(f"[REPL] {text.strip()}")

    hub.on_console = on_console

    print("\nPressing buttons demo:")
    print("  → Waiting up to 10 s for the LEFT button …")
    pressed = hub.wait_for_button("left", timeout=10.0)
    if pressed:
        print("  LEFT button pressed! ✓")
        hub.display_image("09990:90009:90009:90009:09990")  # box
    else:
        print("  Timed out (no press detected)")

    print("\n  → Waiting up to 10 s for the RIGHT button …")
    pressed = hub.wait_for_button("right", timeout=10.0)
    if pressed:
        print("  RIGHT button pressed! ✓")
        hub.display_clear()
    else:
        print("  Timed out (no press detected)")

    # Poll buttons with was_pressed
    print("\nButton polling loop (5 s) — press left or right button:")
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if hub.left_button_was_pressed():
            print("  LEFT was_pressed event!")
        if hub.right_button_was_pressed():
            print("  RIGHT was_pressed event!")
        time.sleep(0.1)
    print("  Polling done.")

    # Gesture detection loop
    print("\nGesture detection (10 s) — shake or tap the hub:")
    last_gesture = Gesture.NONE
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        g = hub.get_gesture()
        if g != Gesture.NONE and g != last_gesture:
            name = GESTURE_NAMES.get(g, f"id={g}")
            print(f"  Gesture detected: {name.upper()}")
            last_gesture = g
        elif g == Gesture.NONE:
            last_gesture = Gesture.NONE
        time.sleep(0.05)
    print("  Gesture detection done.")

    hub.disconnect()
    print("\n✓ Disconnected")


if __name__ == "__main__":
    main()
