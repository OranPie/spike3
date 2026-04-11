"""spike3.recorder — Event recording and playback for SPIKE 3 hub.

Record hub commands into a timeline, then play them back with exact timing.
Useful for choreography, demos, and automated testing.

Usage::

    from spike3 import Hub
    from spike3.recorder import Recorder

    hub = Hub.connect_usb('COM3')
    rec = Recorder(hub)

    rec.start()
    hub.display_text('Hi')
    hub.sound_beep(80, 60)
    hub.motor_start(0, 50)
    time.sleep(1)
    hub.motor_stop(0)
    rec.stop()

    # Save to file
    rec.save('my_demo.json')

    # Play it back
    rec.load('my_demo.json')
    rec.play()
"""

from __future__ import annotations

import json
import logging
import time
import threading
from dataclasses import dataclass, field, asdict
from typing import Any, Optional, Callable

logger = logging.getLogger("spike3.recorder")


@dataclass
class RecordedEvent:
    """A single recorded hub command."""
    timestamp: float  # seconds since recording start
    method: str       # Hub method name (e.g. "display_text")
    args: list        # positional arguments
    kwargs: dict      # keyword arguments

    def to_dict(self) -> dict:
        return {
            "t": round(self.timestamp, 4),
            "m": self.method,
            "a": self.args,
            "k": self.kwargs,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RecordedEvent":
        return cls(
            timestamp=d["t"],
            method=d["m"],
            args=d.get("a", []),
            kwargs=d.get("k", {}),
        )


class Recorder:
    """Records and replays Hub command sequences with timing."""

    # Methods that are recorded when called on the hub
    RECORDABLE = {
        # Display
        "display_text", "display_image", "display_image_for",
        "display_set_pixel", "display_clear", "display_number",
        "display_scroll", "display_set_brightness", "display_rotate",
        # Sound
        "sound_beep", "sound_beep_for", "sound_off",
        "sound_play_note", "sound_set_volume",
        # Motor
        "motor_start", "motor_stop", "motor_run_degrees",
        "motor_run_timed", "motor_go_to_position", "motor_set_position",
        "motor_pwm", "motor_go_direction_to_position",
        # Motor pair
        "move_tank_degrees", "move_tank_timed",
        "move_start_powers", "move_start_speeds", "move_stop",
        # Hub light
        "hub_light_on", "hub_light_off",
        # Other
        "reset_yaw", "set_orientation",
    }

    def __init__(self, hub):
        """
        Args:
            hub: A connected spike3.Hub instance.
        """
        self._hub = hub
        self._events: list[RecordedEvent] = []
        self._recording = False
        self._start_time: float = 0.0
        self._original_methods: dict[str, Callable] = {}

    @property
    def events(self) -> list[RecordedEvent]:
        return list(self._events)

    @property
    def duration(self) -> float:
        """Total recording duration in seconds."""
        if not self._events:
            return 0.0
        return self._events[-1].timestamp

    def start(self):
        """Start recording hub commands."""
        if self._recording:
            return
        self._events.clear()
        self._recording = True
        self._start_time = time.monotonic()
        self._install_hooks()
        logger.info("Recording started")

    def stop(self):
        """Stop recording."""
        if not self._recording:
            return
        self._recording = False
        self._remove_hooks()
        logger.info(f"Recording stopped: {len(self._events)} events, "
                    f"{self.duration:.1f}s")

    def _install_hooks(self):
        """Monkey-patch hub methods to intercept calls."""
        for name in self.RECORDABLE:
            if hasattr(self._hub, name):
                original = getattr(self._hub, name)
                self._original_methods[name] = original
                setattr(self._hub, name, self._make_wrapper(name, original))

    def _remove_hooks(self):
        """Restore original hub methods."""
        for name, original in self._original_methods.items():
            setattr(self._hub, name, original)
        self._original_methods.clear()

    def _make_wrapper(self, method_name: str, original: Callable):
        """Create a wrapper that records then calls original."""
        def wrapper(*args, **kwargs):
            if self._recording:
                ts = time.monotonic() - self._start_time
                event = RecordedEvent(
                    timestamp=ts,
                    method=method_name,
                    args=list(args),
                    kwargs=dict(kwargs),
                )
                self._events.append(event)
                logger.debug(f"Recorded: {method_name}({args}, {kwargs}) @ {ts:.3f}s")
            return original(*args, **kwargs)
        return wrapper

    def play(self, speed: float = 1.0, on_event: Optional[Callable] = None):
        """Play back recorded events with timing.

        Args:
            speed: Playback speed multiplier (2.0 = double speed).
            on_event: Optional callback(event) called before each event.
        """
        if not self._events:
            logger.warning("No events to play")
            return

        logger.info(f"Playing {len(self._events)} events at {speed}x speed")
        start = time.monotonic()

        for event in self._events:
            # Wait for the right time
            target = event.timestamp / speed
            elapsed = time.monotonic() - start
            if target > elapsed:
                time.sleep(target - elapsed)

            if on_event:
                on_event(event)

            # Execute the command
            method = getattr(self._hub, event.method, None)
            if method:
                try:
                    method(*event.args, **event.kwargs)
                except Exception as e:
                    logger.warning(f"Playback error on {event.method}: {e}")
            else:
                logger.warning(f"Method not found: {event.method}")

        logger.info("Playback complete")

    def play_async(self, speed: float = 1.0,
                   on_event: Optional[Callable] = None) -> threading.Thread:
        """Play back in a background thread. Returns the thread."""
        t = threading.Thread(
            target=self.play, args=(speed, on_event),
            daemon=True, name="spike3-playback"
        )
        t.start()
        return t

    def save(self, path: str):
        """Save recording to a JSON file."""
        data = {
            "version": 1,
            "event_count": len(self._events),
            "duration": round(self.duration, 3),
            "events": [e.to_dict() for e in self._events],
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"Saved {len(self._events)} events to {path}")

    def load(self, path: str):
        """Load a recording from a JSON file."""
        with open(path) as f:
            data = json.load(f)
        self._events = [RecordedEvent.from_dict(e) for e in data["events"]]
        logger.info(f"Loaded {len(self._events)} events from {path}")

    def clear(self):
        """Clear all recorded events."""
        self._events.clear()


# ── Preset Scenarios ──────────────────────────────────────────────────

class Scenarios:
    """Pre-built command sequences for common SPIKE 3 demos."""

    @staticmethod
    def heart_beat(hub, repeats: int = 3):
        """Display a beating heart animation on the LED matrix."""
        small = "0090000900999009990009900"
        big   = "9909999099999999099990990"
        for _ in range(repeats):
            hub.display_image(small)
            time.sleep(0.3)
            hub.display_image(big)
            time.sleep(0.5)
            hub.display_image(small)
            time.sleep(0.3)
            hub.display_clear()
            time.sleep(0.4)

    @staticmethod
    def rainbow_light(hub, cycles: int = 2, delay: float = 0.3):
        """Cycle the hub status light through all colors."""
        colors = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]  # Pink → White
        for _ in range(cycles):
            for c in colors:
                hub.hub_light_on(c)
                time.sleep(delay)
        hub.hub_light_off()

    @staticmethod
    def siren(hub, cycles: int = 3):
        """Play a siren sound with alternating light."""
        for _ in range(cycles):
            hub.hub_light_on(9)  # Red
            hub.sound_beep(100, 72)  # High note
            time.sleep(0.3)
            hub.hub_light_on(3)  # Blue
            hub.sound_beep(100, 60)  # Low note
            time.sleep(0.3)
        hub.sound_off()
        hub.hub_light_off()

    @staticmethod
    def motor_test(hub, port: int = 0):
        """Test a motor: forward, backward, stop.

        Args:
            port: Motor port (0=A).
        """
        hub.motor_start(port, 50)
        time.sleep(1)
        hub.motor_start(port, -50)
        time.sleep(1)
        hub.motor_stop(port)

    @staticmethod
    def countdown(hub, start: int = 5):
        """Countdown from start with display and beep."""
        for n in range(start, 0, -1):
            hub.display_number(n)
            hub.sound_beep(60, 72)
            time.sleep(0.1)
            hub.sound_off()
            time.sleep(0.9)
        hub.display_text("GO!")
        hub.sound_beep(100, 84)
        time.sleep(1)
        hub.sound_off()
        hub.display_clear()

    @staticmethod
    def emoji_show(hub):
        """Show a sequence of emoji faces on the matrix."""
        happy = "0090000900000009999009900"  # smile
        sad   = "0090000900000009900099990"  # frown
        wink  = "0090000000000009999009900"  # wink
        cool  = "0000099990000009999009900"  # sunglasses

        for face in [happy, sad, wink, cool, happy]:
            hub.display_image(face)
            time.sleep(0.8)
        hub.display_clear()

    @staticmethod
    def piano(hub, notes: list[tuple[int, int]] = None):
        """Play a sequence of MIDI notes.

        Args:
            notes: List of (midi_note, duration_ms). Defaults to a scale.
        """
        if notes is None:
            notes = [
                (60, 250), (62, 250), (64, 250), (65, 250),
                (67, 250), (69, 250), (71, 250), (72, 500),
            ]
        for note, dur in notes:
            hub.sound_play_note(note, dur, 80)
            time.sleep(dur / 1000 + 0.05)
        hub.sound_off()
