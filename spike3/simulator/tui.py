"""Textual TUI dashboard for the SPIKE 3 simulator.

A rich terminal interface with live-updating panels showing:
  - Hub identity & status bar
  - 5×5 LED matrix visualization (large color blocks)
  - Motor gauges with speed/position bars
  - Sensor readouts (color, distance, force)
  - IMU orientation display
  - Live protocol log stream
  - Interactive command palette

Launch::

    python -m spike3.simulator --tui
"""

from __future__ import annotations

import logging
import time
import threading
from typing import Optional

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, Container, ScrollableContainer
from textual.widgets import (
    Header, Footer, Static, Label, Button, Log,
    Input, ProgressBar, RichLog,
)
from textual.reactive import reactive
from textual.timer import Timer
from textual import on, work
from textual.css.query import NoMatches
from rich.text import Text
from rich.table import Table
from rich.panel import Panel
from rich.console import Group
from rich.align import Align

from .hub_state import HubState
from .devices import Motor, ColorSensor, DistanceSensor, ForceSensor, IMU, Matrix5x5
from .com_server import ComServer, TcpComBridge
from ..enums import Color, Orientation


# ── Custom Widgets ─────────────────────────────────────────────────────

class MatrixDisplay(Static):
    """5×5 LED matrix visualization using large Unicode blocks."""

    def __init__(self, hub: HubState, **kwargs):
        super().__init__(**kwargs)
        self.hub = hub

    def render_matrix(self) -> Text:
        text = Text()
        pixels = self.hub.matrix.pixels
        for y in range(5):
            for x in range(5):
                b = pixels[y * 5 + x]
                if b == 0:
                    text.append("  ", style="on #1a1a2e")
                elif b < 25:
                    text.append("  ", style="on #2a2a4e")
                elif b < 50:
                    text.append("  ", style="on #ff8800")
                elif b < 75:
                    text.append("  ", style="on #ffaa00")
                else:
                    text.append("  ", style="on #ffdd00")
                text.append(" ")
            text.append("\n")
        return text

    def update_display(self):
        self.update(self.render_matrix())


class MotorGauge(Static):
    """Motor status gauge with speed bar and position."""

    def __init__(self, hub: HubState, port: int, **kwargs):
        super().__init__(**kwargs)
        self.hub = hub
        self.port = port
        self.port_label = "ABCDEF"[port]

    def render_gauge(self) -> Text:
        dev = self.hub.get_device(self.port)
        if dev is None:
            return Text(f"  Port {self.port_label}: (empty)", style="dim")
        if not isinstance(dev, Motor):
            return Text(f"  Port {self.port_label}: {type(dev).__name__}", style="cyan")

        text = Text()
        text.append(f"  Port {self.port_label} ", style="bold white")
        text.append("Motor", style="bold cyan")

        # Speed bar: -100 to +100
        speed = dev.speed
        bar_width = 20
        center = bar_width // 2
        fill = abs(speed) * center // 100

        text.append("\n  Speed: ")
        text.append("[")
        for i in range(bar_width):
            pos = i - center
            if speed >= 0:
                if 0 <= pos < fill:
                    text.append("█", style="green")
                else:
                    text.append("░", style="dim")
            else:
                if -fill < pos <= 0:
                    text.append("█", style="red")
                else:
                    text.append("░", style="dim")
        text.append(f"] {speed:+4d}")

        # Position
        text.append(f"\n  Pos: {dev.position:>6d}°  Abs: {dev.absolute_pos:>4d}°  Pwr: {dev.power:>4d}")

        return text

    def update_display(self):
        self.update(self.render_gauge())


class SensorPanel(Static):
    """Sensor readout panel."""

    def __init__(self, hub: HubState, **kwargs):
        super().__init__(**kwargs)
        self.hub = hub

    COLOR_NAMES = {
        Color.NONE: ("None", "dim"), Color.BLACK: ("Black", "white on black"),
        Color.RED: ("Red", "white on red"), Color.ORANGE: ("Orange", "black on #ff8800"),
        Color.YELLOW: ("Yellow", "black on yellow"), Color.GREEN: ("Green", "white on green"),
        Color.BLUE: ("Blue", "white on blue"), Color.WHITE: ("White", "black on white"),
        Color.MAGENTA: ("Magenta", "white on magenta"), Color.PURPLE: ("Purple", "white on #8b00ff"),
        Color.AZURE: ("Azure", "white on #0080ff"), Color.TURQUOISE: ("Turquoise", "black on #00ffcc"),
    }

    def render_sensors(self) -> Text:
        text = Text()
        port_labels = "ABCDEF"
        found = False
        for i in range(6):
            dev = self.hub.get_device(i)
            if isinstance(dev, ColorSensor):
                found = True
                cname, cstyle = self.COLOR_NAMES.get(dev.color, ("?", "dim"))
                text.append(f"  {port_labels[i]} Color: ")
                text.append(f" {cname} ", style=cstyle)
                text.append(f"  Refl:{dev.reflection}  RGB:({dev.raw_red},{dev.raw_green},{dev.raw_blue})\n")
            elif isinstance(dev, DistanceSensor):
                found = True
                d = dev.distance
                bar_w = 15
                fill = min(bar_w, max(0, d * bar_w // 2000)) if d >= 0 else 0
                text.append(f"  {port_labels[i]} Dist:  ")
                text.append("▮" * fill, style="yellow")
                text.append("▯" * (bar_w - fill), style="dim")
                text.append(f"  {d}mm\n")
            elif isinstance(dev, ForceSensor):
                found = True
                bar_w = 10
                fill = dev.force * bar_w // 100
                text.append(f"  {port_labels[i]} Force: ")
                text.append("▮" * fill, style="red" if dev.touch else "cyan")
                text.append("▯" * (bar_w - fill), style="dim")
                text.append(f"  {dev.force}N {'(pressed)' if dev.touch else ''}\n")

        if not found:
            text.append("  No sensors attached\n", style="dim")
        return text

    def update_display(self):
        self.update(self.render_sensors())


class ImuDisplay(Static):
    """IMU orientation and acceleration display."""

    ORIENT_ICONS = {
        Orientation.TOP: "⬆ Top", Orientation.FRONT: "⬅ Front",
        Orientation.RIGHT: "➡ Right", Orientation.BOTTOM: "⬇ Bottom",
        Orientation.BACK: "➡ Back", Orientation.LEFT: "⬅ Left",
    }

    def __init__(self, hub: HubState, **kwargs):
        super().__init__(**kwargs)
        self.hub = hub

    def render_imu(self) -> Text:
        imu = self.hub.imu
        text = Text()
        orient_str = self.ORIENT_ICONS.get(imu.orientation, "?")
        text.append(f"  Orient: {orient_str}\n", style="bold")
        text.append(f"  Yaw: {imu.yaw:>5d}  Pitch: {imu.pitch:>5d}  Roll: {imu.roll:>5d}\n")
        text.append(f"  Accel: ({imu.accel_x:>5d}, {imu.accel_y:>5d}, {imu.accel_z:>5d})\n")
        text.append(f"  Gyro:  ({imu.gyro_x:>5d}, {imu.gyro_y:>5d}, {imu.gyro_z:>5d})")
        return text

    def update_display(self):
        self.update(self.render_imu())


class HubStatusBar(Static):
    """Top status bar with hub identity and connection info."""

    def __init__(self, hub: HubState, server, **kwargs):
        super().__init__(**kwargs)
        self.hub = hub
        self.server = server

    def render_status(self) -> Text:
        text = Text()
        text.append(" 🤖 ", style="bold")
        text.append(self.hub.name, style="bold white")
        text.append("  │  ", style="dim")
        text.append(f"FW {self.hub.fw_major}.{self.hub.fw_minor}.{self.hub.fw_build}", style="cyan")
        text.append("  │  ", style="dim")

        # Battery with color
        bl = self.hub.battery_level
        if bl > 50:
            bstyle = "green"
        elif bl > 20:
            bstyle = "yellow"
        else:
            bstyle = "red"
        text.append(f"🔋{bl}%", style=bstyle)
        text.append("  │  ", style="dim")

        text.append(f"📡 {self.server.port}", style="bright_green")
        text.append("  │  ", style="dim")

        notif = "ON" if self.hub.notifications_enabled else "OFF"
        text.append(f"Notif: {notif}", style="green" if self.hub.notifications_enabled else "dim")

        if self.hub.running_program is not None:
            elapsed = time.monotonic() - self.hub.program_start_time
            text.append("  │  ", style="dim")
            text.append(f"▶ Slot {self.hub.running_program} ({elapsed:.0f}s)", style="bold green")

        if self.hub.sound_playing:
            text.append("  │  ", style="dim")
            text.append(f"♪ Note {self.hub.sound_note}", style="magenta")

        return text

    def update_display(self):
        self.update(self.render_status())


# ── Logging handler that writes to TUI ─────────────────────────────────

class TuiLogHandler(logging.Handler):
    """Routes log records to a Textual RichLog widget."""

    def __init__(self, log_widget: RichLog):
        super().__init__()
        self._log = log_widget

    def emit(self, record):
        try:
            msg = self.format(record)
            style = "dim"
            if record.levelno >= logging.ERROR:
                style = "bold red"
            elif record.levelno >= logging.WARNING:
                style = "yellow"
            elif record.levelno >= logging.INFO:
                style = "white"
            text = Text(msg, style=style)
            self._log.write(text)
        except Exception:
            pass


# ── Main TUI App ───────────────────────────────────────────────────────

TUI_CSS = """
Screen {
    layout: grid;
    grid-size: 3 4;
    grid-columns: 1fr 1fr 1fr;
    grid-rows: 3 1fr 1fr 1fr;
}

#status-bar {
    column-span: 3;
    height: 3;
    background: $surface;
    border: solid $primary;
    padding: 0 1;
}

#matrix-panel {
    height: 100%;
    border: solid $accent;
    padding: 0 1;
}

#motors-panel {
    column-span: 2;
    height: 100%;
    border: solid $accent;
    padding: 0 1;
}

#imu-panel {
    height: 100%;
    border: solid $accent;
    padding: 0 1;
}

#sensors-panel {
    height: 100%;
    border: solid $accent;
    padding: 0 1;
}

#controls-panel {
    height: 100%;
    border: solid $accent;
    padding: 0 1;
}

#log-panel {
    column-span: 3;
    height: 100%;
    border: solid $accent;
}

.panel-title {
    text-style: bold;
    color: $text;
    padding: 0 1;
}

RichLog {
    height: 100%;
    scrollbar-size: 1 1;
}

Input {
    dock: bottom;
    margin: 0 1;
}

Button {
    min-width: 12;
    margin: 0 1;
}
"""


class SimulatorTUI(App):
    """SPIKE 3 Hub Simulator — Textual TUI."""

    CSS = TUI_CSS
    TITLE = "SPIKE 3 Hub Simulator"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("b", "cycle_battery", "Battery ±10"),
        ("m", "toggle_motor", "Motor A Toggle"),
        ("n", "attach_next", "Attach Device"),
        ("r", "reset_yaw", "Reset Yaw"),
        ("d", "toggle_debug", "Debug Log"),
        ("h", "show_heart", "Heart ❤"),
        ("c", "clear_matrix", "Clear Matrix"),
        ("s", "scenario_menu", "Scenario"),
        ("p", "press_button", "Hub Button"),
        ("space", "toggle_notifications", "Notif Toggle"),
    ]

    def __init__(self, hub: HubState, server, **kwargs):
        super().__init__(**kwargs)
        self.hub = hub
        self.server = server
        self._debug_logging = False
        self._scenario_idx = 0
        self._event_recording = False
        self._recorded_events: list[tuple[float, str, dict]] = []
        self._record_start: float = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        # Status bar
        yield HubStatusBar(self.hub, self.server, id="status-bar")

        # Row 2: Matrix | Motors
        yield Container(
            Label(" 💡 LED Matrix", classes="panel-title"),
            MatrixDisplay(self.hub, id="matrix-display"),
            id="matrix-panel",
        )
        yield Container(
            Label(" ⚙ Motors", classes="panel-title"),
            MotorGauge(self.hub, 0, id="motor-a"),
            MotorGauge(self.hub, 1, id="motor-b"),
            MotorGauge(self.hub, 2, id="motor-c"),
            id="motors-panel",
        )

        # Row 3: IMU | Sensors | Controls
        yield Container(
            Label(" 🧭 IMU", classes="panel-title"),
            ImuDisplay(self.hub, id="imu-display"),
            id="imu-panel",
        )
        yield Container(
            Label(" 📊 Sensors", classes="panel-title"),
            SensorPanel(self.hub, id="sensor-display"),
            id="sensors-panel",
        )
        yield Container(
            Label(" 🎮 Quick Controls", classes="panel-title"),
            Static(
                "[b]B[/b] Battery  [b]M[/b] Motor  [b]H[/b] Heart\n"
                "[b]N[/b] Attach   [b]R[/b] Reset   [b]C[/b] Clear\n"
                "[b]S[/b] Scenario [b]P[/b] Button  [b]Space[/b] Notif\n"
                "[b]D[/b] Debug    [b]Q[/b] Quit",
            ),
            id="controls-panel",
        )

        # Row 4: Log
        yield Container(
            Label(" 📋 Protocol Log", classes="panel-title"),
            RichLog(id="log-view", highlight=True, markup=True, max_lines=500),
            id="log-panel",
        )

        yield Footer()

    def on_mount(self) -> None:
        # Set up log handler
        log_widget = self.query_one("#log-view", RichLog)
        handler = TuiLogHandler(log_widget)
        handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        handler.setLevel(logging.INFO)

        for name in ["spike3.simulator", "spike3.simulator.com",
                      "spike3.simulator.responder", "spike3.simulator.tunnel"]:
            logger = logging.getLogger(name)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)

        # Start refresh timer
        self.set_interval(0.15, self._refresh_displays)

        # Welcome message
        log_widget.write(Text("╔════════════════════════════════════════╗", style="bold cyan"))
        log_widget.write(Text("║  SPIKE 3 Hub Simulator TUI v0.1       ║", style="bold cyan"))
        log_widget.write(Text(f"║  Port: {self.server.port:<32s}║", style="bold cyan"))
        log_widget.write(Text("╚════════════════════════════════════════╝", style="bold cyan"))

    def _refresh_displays(self) -> None:
        """Refresh all display widgets."""
        try:
            self.query_one("#status-bar", HubStatusBar).update_display()
            self.query_one("#matrix-display", MatrixDisplay).update_display()
            self.query_one("#motor-a", MotorGauge).update_display()
            self.query_one("#motor-b", MotorGauge).update_display()
            self.query_one("#motor-c", MotorGauge).update_display()
            self.query_one("#imu-display", ImuDisplay).update_display()
            self.query_one("#sensor-display", SensorPanel).update_display()
        except NoMatches:
            pass

    def _log(self, msg: str, style: str = "white"):
        try:
            log_widget = self.query_one("#log-view", RichLog)
            log_widget.write(Text(msg, style=style))
        except NoMatches:
            pass

    # ── Actions ────────────────────────────────────────────────────

    def action_cycle_battery(self) -> None:
        self.hub.battery_level = (self.hub.battery_level - 10) % 110
        if self.hub.battery_level > 100:
            self.hub.battery_level = 100
        self._log(f"🔋 Battery: {self.hub.battery_level}%", "green")

    def action_toggle_motor(self) -> None:
        motor = self.hub.get_motor(0)
        if motor:
            if motor._running:
                motor.stop(1)
                self._log("⚙ Motor A stopped", "yellow")
            else:
                motor.start(75)
                self._log("⚙ Motor A started at speed 75", "green")

    def action_attach_next(self) -> None:
        """Cycle through attaching different devices to port C."""
        devices = [
            (ColorSensor(2), "ColorSensor"),
            (DistanceSensor(2), "DistanceSensor"),
            (ForceSensor(2), "ForceSensor"),
            (Motor(2, device_id=0x31), "Motor"),
        ]
        self._scenario_idx = (self._scenario_idx + 1) % len(devices)
        dev, name = devices[self._scenario_idx]
        self.hub.attach_device(2, dev)
        self._log(f"🔌 Attached {name} to port C", "cyan")

    def action_reset_yaw(self) -> None:
        self.hub.imu.yaw = 0
        self.hub.imu.pitch = 0
        self.hub.imu.roll = 0
        self._log("🧭 IMU reset", "cyan")

    def action_show_heart(self) -> None:
        heart = "0909000000909000009000000090900000009000"
        # Corrected 25-char heart pattern
        heart = "0909009090009000090000900"
        self.hub.matrix.set_image(heart.replace("0", "0").replace("9", "9"))
        # Actually let me make a proper heart:
        heart_pattern = [
            0, 99, 0, 99, 0,
            99, 99, 99, 99, 99,
            99, 99, 99, 99, 99,
            0, 99, 99, 99, 0,
            0, 0, 99, 0, 0,
        ]
        self.hub.matrix.pixels = heart_pattern
        self._log("❤ Heart pattern displayed", "red")

    def action_clear_matrix(self) -> None:
        self.hub.matrix.clear()
        self._log("💡 Matrix cleared", "dim")

    def action_toggle_debug(self) -> None:
        self._debug_logging = not self._debug_logging
        level = logging.DEBUG if self._debug_logging else logging.INFO
        for name in ["spike3.simulator.com", "spike3.simulator.responder",
                      "spike3.simulator.tunnel"]:
            logging.getLogger(name).setLevel(level)
        self._log(f"🔧 Debug logging: {'ON' if self._debug_logging else 'OFF'}",
                  "yellow" if self._debug_logging else "dim")

    def action_toggle_notifications(self) -> None:
        if self.hub.notifications_enabled:
            self.hub.enable_notifications(0)
            self._log("📡 Notifications disabled", "dim")
        else:
            self.hub.enable_notifications(50)
            self._log("📡 Notifications enabled (50ms)", "green")

    def action_press_button(self) -> None:
        """Simulate hub button press."""
        self.hub.button_pressed = True
        self._log("🔘 Hub button PRESSED", "bold yellow")

        def release():
            import time
            time.sleep(0.5)
            self.hub.button_pressed = False
            self.call_from_thread(self._log, "🔘 Hub button released", "dim")

        threading.Thread(target=release, daemon=True).start()

    def action_scenario_menu(self) -> None:
        """Cycle through pre-built scenarios."""
        scenarios = [
            ("🤖 Robot", self._scenario_robot),
            ("🎨 Color Sorter", self._scenario_sorter),
            ("📏 Distance Alarm", self._scenario_alarm),
            ("🎵 Music Box", self._scenario_music),
            ("🏁 Line Follower", self._scenario_line),
        ]
        idx = getattr(self, '_scen_cycle', 0)
        name, func = scenarios[idx % len(scenarios)]
        self._scen_cycle = idx + 1
        func()
        self._log(f"🎬 Scenario loaded: {name}", "bold magenta")

    def _scenario_robot(self):
        """Robot: 2 motors, distance sensor, color sensor."""
        self.hub.attach_device(0, Motor(0, device_id=0x31))
        self.hub.attach_device(1, Motor(1, device_id=0x31))
        cs = ColorSensor(2); cs.set_color(Color.RED)
        self.hub.attach_device(2, cs)
        ds = DistanceSensor(3); ds.set_distance(250)
        self.hub.attach_device(3, ds)
        self.hub.detach_device(4)
        self.hub.detach_device(5)
        # Heart on matrix
        self.hub.matrix.pixels = [
            0, 99, 0, 99, 0,
            99, 99, 99, 99, 99,
            99, 99, 99, 99, 99,
            0, 99, 99, 99, 0,
            0, 0, 99, 0, 0,
        ]

    def _scenario_sorter(self):
        """Color sorter: 1 motor, color sensor, force sensor."""
        self.hub.attach_device(0, Motor(0, device_id=0x31))
        cs = ColorSensor(1); cs.set_color(Color.BLUE)
        self.hub.attach_device(1, cs)
        fs = ForceSensor(2); fs.set_force(30)
        self.hub.attach_device(2, fs)
        self.hub.detach_device(3)
        self.hub.detach_device(4)
        self.hub.detach_device(5)
        # Arrow on matrix
        self.hub.matrix.pixels = [
            0, 0, 99, 0, 0,
            0, 99, 99, 99, 0,
            99, 0, 99, 0, 99,
            0, 0, 99, 0, 0,
            0, 0, 99, 0, 0,
        ]

    def _scenario_alarm(self):
        """Distance alarm: distance sensor with variable reading."""
        ds = DistanceSensor(0); ds.set_distance(500)
        self.hub.attach_device(0, ds)
        self.hub.detach_device(1)
        self.hub.detach_device(2)
        self.hub.detach_device(3)
        # Exclamation mark
        self.hub.matrix.pixels = [
            0, 0, 99, 0, 0,
            0, 0, 99, 0, 0,
            0, 0, 99, 0, 0,
            0, 0, 0, 0, 0,
            0, 0, 99, 0, 0,
        ]

    def _scenario_music(self):
        """Music box: no external devices, play sounds."""
        for i in range(6):
            self.hub.detach_device(i)
        self.hub.sound_playing = True
        self.hub.sound_note = 60
        self.hub.sound_volume = 80
        # Music note on matrix
        self.hub.matrix.pixels = [
            0, 0, 99, 99, 0,
            0, 0, 99, 0, 99,
            0, 0, 99, 0, 0,
            0, 99, 99, 0, 0,
            99, 99, 99, 0, 0,
        ]

    def _scenario_line(self):
        """Line follower: 2 motors + 2 color sensors."""
        self.hub.attach_device(0, Motor(0, device_id=0x31))
        self.hub.attach_device(1, Motor(1, device_id=0x31))
        cs1 = ColorSensor(2); cs1.set_color(Color.WHITE)
        self.hub.attach_device(2, cs1)
        cs2 = ColorSensor(3); cs2.set_color(Color.BLACK)
        self.hub.attach_device(3, cs2)
        self.hub.detach_device(4)
        self.hub.detach_device(5)
        # Smiley
        self.hub.matrix.pixels = [
            0, 99, 0, 99, 0,
            0, 99, 0, 99, 0,
            0, 0, 0, 0, 0,
            99, 0, 0, 0, 99,
            0, 99, 99, 99, 0,
        ]

    # ── Event Recording ────────────────────────────────────────────

    def start_recording(self):
        self._event_recording = True
        self._recorded_events = []
        self._record_start = time.monotonic()
        self._log("⏺ Recording started", "bold red")

    def stop_recording(self) -> list:
        self._event_recording = False
        self._log(f"⏹ Recording stopped ({len(self._recorded_events)} events)", "red")
        return self._recorded_events

    def record_event(self, event_type: str, data: dict):
        if self._event_recording:
            t = time.monotonic() - self._record_start
            self._recorded_events.append((t, event_type, data))

    def replay_events(self, events: list):
        """Replay recorded events in a background thread."""
        def _replay():
            self.call_from_thread(self._log, "▶ Replaying events...", "bold green")
            t0 = time.monotonic()
            for t, evt, data in events:
                wait = t - (time.monotonic() - t0)
                if wait > 0:
                    time.sleep(wait)
                self._apply_event(evt, data)
            self.call_from_thread(self._log, "⏹ Replay complete", "green")
        threading.Thread(target=_replay, daemon=True).start()

    def _apply_event(self, event_type: str, data: dict):
        if event_type == "battery":
            self.hub.battery_level = data["level"]
        elif event_type == "motor_start":
            motor = self.hub.get_motor(data["port"])
            if motor:
                motor.start(data["speed"])
        elif event_type == "motor_stop":
            motor = self.hub.get_motor(data["port"])
            if motor:
                motor.stop()
        elif event_type == "color":
            dev = self.hub.get_device(data["port"])
            if isinstance(dev, ColorSensor):
                dev.set_color(data["color"])
        elif event_type == "distance":
            dev = self.hub.get_device(data["port"])
            if isinstance(dev, DistanceSensor):
                dev.set_distance(data["mm"])

    def action_quit(self) -> None:
        self.server.stop()
        self.exit()


def run_tui(hub: HubState, server):
    """Launch the TUI dashboard."""
    app = SimulatorTUI(hub, server)
    app.run()
