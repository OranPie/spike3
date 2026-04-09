"""Example: Upload and run a Python program on SPIKE 3 hub via USB.

Usage:
    python example_upload.py PORT program.py [SLOT]
"""

import sys
import zlib

sys.path.insert(0, "..")

from spike3 import Hub


def main():
    if len(sys.argv) < 3:
        print("Usage: python example_upload.py PORT program.py [SLOT]")
        sys.exit(1)

    port = sys.argv[1]
    program_file = sys.argv[2]
    slot = int(sys.argv[3]) if len(sys.argv) > 3 else 0

    with open(program_file, "rb") as f:
        program_data = f.read()

    print(f"Connecting to {port}...")
    with Hub.connect_usb(port) as hub:
        info = hub.get_info()
        print(f"FW: {info.fw_major}.{info.fw_minor}.{info.fw_build}")

        def progress(sent, total):
            pct = sent * 100 // total
            print(f"\r  Uploading: {pct}% ({sent}/{total} bytes)", end="", flush=True)

        hub.upload_program(
            filename=program_file.split("/")[-1],
            data=program_data,
            slot=slot,
            on_progress=progress,
        )
        print(f"\nUpload complete. Starting program in slot {slot}...")
        hub.start_program(slot)
        print("Program started.")

        # Listen for console output
        hub.on_console = lambda text: print(f"  > {text}")
        print("Listening for console output (Ctrl-C to stop)...")

        try:
            import time
            time.sleep(60)
        except KeyboardInterrupt:
            hub.stop_program(slot)
            print("\nProgram stopped.")

    print("Disconnected.")


if __name__ == "__main__":
    main()
