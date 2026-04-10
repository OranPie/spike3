"""Entry point for `python -m spike3`.

Launches the SPIKE 3 Hub TUI — a terminal dashboard for connecting to
and controlling a real LEGO SPIKE 3 hub (or simulator).

Usage::

    python -m spike3                          # interactive connection screen
    python -m spike3 --port COM3              # auto-connect USB
    python -m spike3 --port /dev/ttyACM0      # auto-connect USB (Linux/Mac)
    python -m spike3 --tcp localhost 51337    # auto-connect to simulator

For the simulator (fake hub):

    python -m spike3.simulator                # basic CLI
    python -m spike3.simulator --tui          # simulator TUI
    python -m spike3.simulator --tcp          # TCP mode (Windows-friendly)
"""

import argparse
import logging


def main():
    parser = argparse.ArgumentParser(
        prog="python -m spike3",
        description="SPIKE 3 Hub TUI — real-time hub dashboard & control",
    )
    parser.add_argument(
        "--port", "-p", default=None,
        help="USB serial port to connect directly (e.g. COM3, /dev/ttyACM0)",
    )
    parser.add_argument(
        "--tcp", nargs=2, metavar=("HOST", "PORT"), default=None,
        help="Connect to simulator/hub via TCP (e.g. --tcp localhost 51337)",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(name)s [%(levelname)s]: %(message)s",
    )

    from .hub_tui import run_hub_tui

    tcp_host = None
    tcp_port = None
    if args.tcp:
        tcp_host = args.tcp[0]
        try:
            tcp_port = int(args.tcp[1])
        except ValueError:
            print(f"Invalid TCP port: {args.tcp[1]}")
            raise SystemExit(1)

    run_hub_tui(port=args.port, tcp_host=tcp_host, tcp_port=tcp_port)


if __name__ == "__main__":
    main()
