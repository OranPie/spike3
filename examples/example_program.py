"""Example: uploading and running a MicroPython program on SPIKE 3.

Demonstrates program upload (with progress), slot management,
execution, and cleanup.

Usage:
    python examples/example_program.py
    python examples/example_program.py COM3
"""

import sys
import time
import spike3

# ── Demo program to upload ───────────────────────────────────────────────────
DEMO_PROGRAM = """\
import hub
import motor
import time
from hub import port

# Show countdown on matrix
for i in range(5, 0, -1):
    hub.display.show(i)
    time.sleep_ms(500)

# Flash a smiley face
hub.display.show(hub.Image(
    "09090:"
    "09090:"
    "00000:"
    "90009:"
    "09990"
))
time.sleep_ms(2000)
hub.display.off()

print("Program complete!")
"""


def progress(sent: int, total: int):
    pct = int(100 * sent / total) if total else 100
    bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
    print(f"\r  [{bar}] {pct:3d}%  {sent}/{total} bytes", end="", flush=True)
    if sent >= total:
        print()


def main():
    port_arg = sys.argv[1] if len(sys.argv) > 1 else None

    if port_arg:
        hub = spike3.Hub.connect_usb(port_arg)
    else:
        hub = spike3.Hub.connect_usb()

    print(f"✓ Connected to: {hub.get_hub_name()}")

    # List what's in the slots directory
    try:
        files = hub.list_files()
        print(f"  Slot files: {files or '(none)'}")
    except Exception as e:
        print(f"  list_files: {e}")

    # Upload demo program to slot 0
    print("\n▶ Uploading program to slot 0 …")
    hub.run_python_program(
        DEMO_PROGRAM, slot=0, run=False, on_progress=progress
    )
    print("  Upload complete.")

    # Start the program
    print("\n▶ Starting slot 0 …")
    hub.run_slot(0)
    print("  Program started. Waiting 8 s for completion …")
    time.sleep(8)

    # Stop (in case it's still running)
    print("\n■ Stopping slot 0 (if still running) …")
    try:
        hub.stop_slot(0)
    except Exception:
        pass

    # Clean up
    print("\n🗑  Clearing slot 0 …")
    try:
        hub.clear_slot(0)
        print("  Slot 0 cleared.")
    except Exception as e:
        print(f"  clear_slot: {e}")

    hub.disconnect()
    print("\n✓ Disconnected")


if __name__ == "__main__":
    main()
