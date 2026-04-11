"""Real-hub Textual TUI for connecting to and controlling a SPIKE 3 hub.

Provides a rich terminal dashboard for live interaction with an actual
LEGO SPIKE 3 hub (or simulator), without needing the SPIKE App.

Screens:
  ConnectScreen  — USB scan, BLE scan, TCP/simulator connect
  DashboardScreen — live sensor data, controls, log stream

Usage::

    python -m spike3                         # launch connection TUI
    python -m spike3 --port COM3             # connect USB directly
    python -m spike3 --tcp localhost 51337   # connect to simulator
"""

from __future__ import annotations

import asyncio
import logging
import platform
import threading
import time
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, Container, ScrollableContainer
from textual.screen import Screen, ModalScreen
from textual.widgets import (
    Header, Footer, Static, Label, Button, Input,
    RichLog, ListView, ListItem, RadioSet, RadioButton,
    TabbedContent, TabPane, DataTable, Select,
)
from textual.reactive import reactive
from textual import on, work
from textual.css.query import NoMatches
from rich.text import Text
from rich.panel import Panel
from rich.table import Table

from .hub import Hub
from .transport import UsbTransport, TcpTransport
from .enums import (
    NotifSubId, Color, Orientation,
    ATLANTIS_SERVICE_UUID,
)
from . import atlantis

logger = logging.getLogger("spike3.hub_tui")

PORT_LABELS = "ABCDEF"

COLOR_STYLES: dict[int, tuple[str, str]] = {
    Color.NONE:      ("None",      "dim"),
    Color.BLACK:     ("Black",     "white on #111111"),
    Color.RED:       ("Red",       "white on red"),
    Color.ORANGE:    ("Orange",    "black on #ff8800"),
    Color.YELLOW:    ("Yellow",    "black on yellow"),
    Color.GREEN:     ("Green",     "white on green"),
    Color.BLUE:      ("Blue",      "white on #0055ff"),
    Color.WHITE:     ("White",     "black on white"),
    Color.MAGENTA:   ("Magenta",   "white on magenta"),
    Color.PURPLE:    ("Purple",    "white on #6600cc"),
    Color.AZURE:     ("Azure",     "white on #0080ff"),
    Color.TURQUOISE: ("Turquoise", "black on #00ccaa"),
}

ORIENT_ICONS: dict[int, str] = {
    Orientation.TOP:    "⬆ Top",
    Orientation.FRONT:  "⬅ Front",
    Orientation.RIGHT:  "➡ Right",
    Orientation.BOTTOM: "⬇ Bottom",
    Orientation.BACK:   "⬅ Back",
    Orientation.LEFT:   "➡ Left",
}


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _speed_bar(speed: int, width: int = 20) -> Text:
    """Render a horizontal bar for motor speed (-100..+100)."""
    text = Text()
    center = width // 2
    fill = abs(speed) * center // 100
    text.append("[")
    for i in range(width):
        pos = i - center
        if speed >= 0:
            style = "bold green" if 0 <= pos < fill else "dim"
        else:
            style = "bold red" if -fill < pos <= 0 else "dim"
        text.append("█" if (
            (speed >= 0 and 0 <= pos < fill) or
            (speed < 0 and -fill < pos <= 0)
        ) else "░", style=style)
    text.append(f"] {speed:+4d}")
    return text


def _battery_color(pct: int) -> str:
    if pct > 50:
        return "bold green"
    elif pct > 20:
        return "bold yellow"
    return "bold red"


# ─────────────────────────────────────────────────────────────────────
# Shared widgets (work from Hub._latest_notifs)
# ─────────────────────────────────────────────────────────────────────

class HubStatusBar(Static):
    """Top-of-screen status bar for the real hub."""

    DEFAULT_CSS = """
    HubStatusBar {
        height: 1;
        background: $boost;
        color: $text;
        padding: 0 1;
    }
    """

    def __init__(self, hub: Hub, conn_label: str, **kwargs):
        super().__init__(**kwargs)
        self._hub = hub
        self._conn_label = conn_label
        self._start_time = time.monotonic()
        self._name = "?"
        self._fw = "?.?.?"

    def set_info(self, name: str, fw: str):
        self._name = name
        self._fw = fw
        self.update(self._build())

    def refresh_data(self):
        self.update(self._build())

    def _build(self) -> Text:
        text = Text()
        text.append(" 🤖 ", style="bold")
        text.append(self._name, style="bold white")
        text.append("  │  ", style="dim")
        text.append(f"FW {self._fw}", style="cyan")
        text.append("  │  ", style="dim")
        text.append(self._conn_label, style="bright_green")
        text.append("  │  ", style="dim")

        # Battery
        info = self._hub._latest_notifs.get((NotifSubId.INFO_HUB, None))
        if info:
            bl = info.battery_level
            text.append(f"🔋 {bl}%", style=_battery_color(bl))
        else:
            text.append("🔋 --", style="dim")

        # Uptime
        up = int(time.monotonic() - self._start_time)
        text.append("  │  ", style="dim")
        text.append(f"⏱ {up // 60:02d}:{up % 60:02d}", style="dim")

        return text


class MatrixPanel(Static):
    """5×5 LED matrix from hub notifications."""

    DEFAULT_CSS = """
    MatrixPanel {
        height: 12;
        border: round $accent;
        padding: 0 1;
    }
    """

    def __init__(self, hub: Hub, **kwargs):
        super().__init__(**kwargs)
        self._hub = hub

    def refresh_data(self):
        self.update(self._build())

    def _build(self) -> Text:
        notif = self._hub._latest_notifs.get((NotifSubId.MATRIX_HUB, None))
        text = Text()
        text.append(" 5×5 Matrix\n", style="bold dim")
        if notif is None:
            text.append(" (no data yet)\n" * 5, style="dim")
            return text
        pixels = notif.image
        for y in range(5):
            text.append(" ")
            for x in range(5):
                b = pixels[y * 5 + x]
                if b == 0:
                    text.append("  ", style="on #1a1a2e")
                elif b < 30:
                    text.append("  ", style="on #2a2a5e")
                elif b < 60:
                    text.append("  ", style="on #ff8800")
                elif b < 80:
                    text.append("  ", style="on #ffaa22")
                else:
                    text.append("  ", style="on #ffdd00")
                text.append(" ")
            text.append("\n")
        return text


class PortsPanel(Static):
    """6-port overview panel — motors and sensors."""

    DEFAULT_CSS = """
    PortsPanel {
        height: 20;
        border: round $accent;
        padding: 0 1;
    }
    """

    def __init__(self, hub: Hub, **kwargs):
        super().__init__(**kwargs)
        self._hub = hub

    def refresh_data(self):
        self.update(self._build())

    def _build(self) -> Text:
        text = Text()
        text.append(" Ports\n", style="bold dim")
        for port in range(6):
            lbl = PORT_LABELS[port]
            # Check each device type
            motor = self._hub._latest_notifs.get((NotifSubId.MOTOR, port))
            color = self._hub._latest_notifs.get((NotifSubId.COLOR_SENSOR, port))
            dist  = self._hub._latest_notifs.get((NotifSubId.DISTANCE_SENSOR, port))
            force = self._hub._latest_notifs.get((NotifSubId.FORCE_SENSOR, port))
            cmatrix = self._hub._latest_notifs.get((NotifSubId.COLOR_MATRIX, port))

            text.append(f" {lbl} ", style="bold yellow")
            if motor is not None:
                text.append("Motor ", style="cyan")
                text.append(_speed_bar(motor.speed, 14))
                text.append(f" pos:{motor.position:>6d}°")
                if motor.speed == 0:
                    text.append(" ■", style="dim")
                else:
                    text.append(" ▶", style="green")
            elif color is not None:
                name, style = COLOR_STYLES.get(color.color, ("?", "dim"))
                text.append("Color  ", style="magenta")
                text.append(f" {name} ", style=style)
                text.append(f"  refl:{color.reflection:3d}%")
            elif dist is not None:
                text.append("Dist   ", style="blue")
                d = dist.distance
                bar = min(15, max(0, d * 15 // 2000)) if d >= 0 else 0
                text.append("▮" * bar, style="yellow")
                text.append("▯" * (15 - bar), style="dim")
                text.append(f" {d:5d}mm")
            elif force is not None:
                text.append("Force  ", style="red")
                bar = force.force * 10 // 100
                text.append("▮" * bar, style="red" if force.touch else "cyan")
                text.append("▯" * (10 - bar), style="dim")
                text.append(f" {force.force:3d}N {'(pressed)' if force.touch else ''}")
            elif cmatrix is not None:
                text.append("ColMatrix 3×3", style="bright_magenta")
            else:
                text.append("(empty)", style="dim")
            text.append("\n")
        return text


class ImuPanel(Static):
    """IMU orientation and angle display."""

    DEFAULT_CSS = """
    ImuPanel {
        height: 8;
        border: round $accent;
        padding: 0 1;
    }
    """

    def __init__(self, hub: Hub, **kwargs):
        super().__init__(**kwargs)
        self._hub = hub

    def refresh_data(self):
        self.update(self._build())

    def _build(self) -> Text:
        notif = self._hub._latest_notifs.get((NotifSubId.IMU_HUB, None))
        text = Text()
        text.append(" IMU\n", style="bold dim")
        if notif is None:
            text.append(" (waiting for notifications...)\n", style="dim")
            return text
        orient = ORIENT_ICONS.get(notif.orientation, f"#{notif.orientation}")
        text.append(f" Orient:  {orient}\n", style="bold")
        text.append(f" Yaw:   {notif.yaw:>6d}°   Pitch: {notif.pitch:>6d}°   Roll: {notif.roll:>6d}°\n")
        text.append(f" Accel: ({notif.accel_x:>6d}, {notif.accel_y:>6d}, {notif.accel_z:>6d})\n")
        text.append(f" Gyro:  ({notif.gyro_x:>6d}, {notif.gyro_y:>6d}, {notif.gyro_z:>6d})\n")
        return text


# ─────────────────────────────────────────────────────────────────────
# Modals
# ─────────────────────────────────────────────────────────────────────

class MotorModal(ModalScreen):
    """Overlay for motor control."""

    DEFAULT_CSS = """
    MotorModal {
        align: center middle;
    }
    MotorModal > Container {
        width: 50;
        height: 18;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    def __init__(self, hub: Hub, **kwargs):
        super().__init__(**kwargs)
        self._hub = hub

    def compose(self) -> ComposeResult:
        with Container():
            yield Label("⚙  Motor Control", classes="modal-title")
            yield Label("Port (A-F):")
            yield Input(placeholder="A", id="motor-port", max_length=1)
            yield Label("Speed (-100 to +100):")
            yield Input(placeholder="50", id="motor-speed")
            with Horizontal():
                yield Button("▶ Start", id="motor-start", variant="success")
                yield Button("■ Stop",  id="motor-stop",  variant="error")
                yield Button("✕ Close", id="motor-close")

    @on(Button.Pressed, "#motor-start")
    def _start(self):
        port_str = self.query_one("#motor-port", Input).value.upper()
        speed_str = self.query_one("#motor-speed", Input).value
        if port_str in PORT_LABELS:
            port_idx = PORT_LABELS.index(port_str)
            try:
                speed = int(speed_str)
                self._hub.motor_start(port_idx, speed)
            except (ValueError, Exception) as e:
                logger.warning(f"Motor start failed: {e}")

    @on(Button.Pressed, "#motor-stop")
    def _stop(self):
        port_str = self.query_one("#motor-port", Input).value.upper()
        if port_str in PORT_LABELS:
            port_idx = PORT_LABELS.index(port_str)
            try:
                self._hub.motor_stop(port_idx)
            except Exception as e:
                logger.warning(f"Motor stop failed: {e}")

    @on(Button.Pressed, "#motor-close")
    def _close(self):
        self.dismiss()


class DisplayModal(ModalScreen):
    """Overlay for LED matrix control."""

    DEFAULT_CSS = """
    DisplayModal {
        align: center middle;
    }
    DisplayModal > Container {
        width: 60;
        height: 20;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    PRESET_IMAGES = {
        "Heart":    "0909009090900009009000900",
        "Happy":    "0090009000900090091919190",
        "X":        "9000900090090009000900090",
        "All On":   "9" * 25,
        "All Off":  "0" * 25,
        "Checkmark":"0000000009090009000000000",
    }

    def __init__(self, hub: Hub, **kwargs):
        super().__init__(**kwargs)
        self._hub = hub

    def compose(self) -> ComposeResult:
        with Container():
            yield Label("🖥  Display Control", classes="modal-title")
            yield Label("Text (scrolls across matrix):")
            yield Input(placeholder="Hello!", id="disp-text")
            yield Label("Image (25 digits 0-9, row-major):")
            yield Input(placeholder="09090900009009009090", id="disp-image")
            yield Label("Presets:")
            opts = [(v, k) for k, v in self.PRESET_IMAGES.items()]
            yield Select(
                [(k, k) for k in self.PRESET_IMAGES],
                id="disp-preset",
                prompt="Choose preset…",
            )
            with Horizontal():
                yield Button("Text", id="disp-do-text", variant="primary")
                yield Button("Image", id="disp-do-image", variant="primary")
                yield Button("Preset", id="disp-do-preset", variant="success")
                yield Button("Clear", id="disp-clear", variant="warning")
                yield Button("✕ Close", id="disp-close")

    @on(Button.Pressed, "#disp-do-text")
    def _text(self):
        txt = self.query_one("#disp-text", Input).value
        if txt:
            try:
                self._hub.display_text(txt)
            except Exception as e:
                logger.warning(f"Display text failed: {e}")

    @on(Button.Pressed, "#disp-do-image")
    def _image(self):
        img = self.query_one("#disp-image", Input).value
        if img:
            try:
                self._hub.display_image(img)
            except Exception as e:
                logger.warning(f"Display image failed: {e}")

    @on(Button.Pressed, "#disp-do-preset")
    def _preset(self):
        sel = self.query_one("#disp-preset", Select)
        if sel.value and sel.value != Select.BLANK:
            key = str(sel.value)
            img = self.PRESET_IMAGES.get(key, "")
            if img:
                try:
                    self._hub.display_image(img)
                except Exception as e:
                    logger.warning(f"Display preset failed: {e}")

    @on(Button.Pressed, "#disp-clear")
    def _clear(self):
        try:
            self._hub.display_image("0" * 25)
        except Exception as e:
            logger.warning(f"Display clear failed: {e}")

    @on(Button.Pressed, "#disp-close")
    def _close(self):
        self.dismiss()


class SoundModal(ModalScreen):
    """Overlay for sound control."""

    DEFAULT_CSS = """
    SoundModal {
        align: center middle;
    }
    SoundModal > Container {
        width: 50;
        height: 16;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    NOTES = {"C4": 60, "D4": 62, "E4": 64, "F4": 65, "G4": 67, "A4": 69, "B4": 71, "C5": 72}

    def __init__(self, hub: Hub, **kwargs):
        super().__init__(**kwargs)
        self._hub = hub

    def compose(self) -> ComposeResult:
        with Container():
            yield Label("🔊  Sound Control", classes="modal-title")
            yield Label("Note (MIDI 0-127):")
            yield Input(placeholder="60", id="snd-note")
            yield Label("Volume (0-100):")
            yield Input(placeholder="100", id="snd-vol")
            yield Label("Duration ms (0 = forever):")
            yield Input(placeholder="500", id="snd-dur")
            with Horizontal():
                yield Button("▶ Beep", id="snd-beep", variant="success")
                yield Button("■ Stop", id="snd-stop", variant="error")
                yield Button("✕ Close", id="snd-close")

    @on(Button.Pressed, "#snd-beep")
    def _beep(self):
        try:
            note = int(self.query_one("#snd-note", Input).value or "60")
            vol  = int(self.query_one("#snd-vol",  Input).value or "100")
            dur  = int(self.query_one("#snd-dur",  Input).value or "500")
            self._hub.sound_beep_for(vol, note, dur)
        except Exception as e:
            logger.warning(f"Sound beep failed: {e}")

    @on(Button.Pressed, "#snd-stop")
    def _stop(self):
        try:
            self._hub.sound_off()
        except Exception as e:
            logger.warning(f"Sound stop failed: {e}")

    @on(Button.Pressed, "#snd-close")
    def _close(self):
        self.dismiss()


class HubLightModal(ModalScreen):
    """Overlay for hub status light color control."""

    DEFAULT_CSS = """
    HubLightModal {
        align: center middle;
    }
    HubLightModal > Container {
        width: 50;
        height: 14;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    COLORS = [
        ("Off",       -1),
        ("Black",      0), ("Pink",   1), ("Purple",  2),
        ("Blue",       3), ("Azure",  4), ("Turquoise", 5),
        ("Green",      6), ("Yellow", 7), ("Orange", 8),
        ("Red",        9), ("White", 10),
    ]

    def __init__(self, hub: Hub, **kwargs):
        super().__init__(**kwargs)
        self._hub = hub

    def compose(self) -> ComposeResult:
        with Container():
            yield Label("💡  Hub Light Control", classes="modal-title")
            yield Label("Select color:")
            with Horizontal():
                for name, val in self.COLORS:
                    yield Button(name, id=f"light-{val}", variant="primary" if val >= 0 else "error")
            yield Button("✕ Close", id="light-close")

    @on(Button.Pressed)
    def _on_button(self, event: Button.Pressed):
        btn_id = event.button.id or ""
        if btn_id == "light-close":
            self.dismiss()
            return
        if btn_id.startswith("light-"):
            val = int(btn_id.split("-")[1])
            try:
                if val < 0:
                    self._hub.hub_light_off()
                else:
                    self._hub.hub_light_on(val)
            except Exception as e:
                logger.warning(f"Hub light failed: {e}")


class ReplModal(ModalScreen):
    """Interactive MicroPython REPL overlay."""

    DEFAULT_CSS = """
    ReplModal {
        align: center middle;
    }
    ReplModal > Container {
        width: 80;
        height: 30;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #repl-output {
        height: 1fr;
        border: round $accent;
        margin-bottom: 1;
    }
    """

    def __init__(self, hub: Hub, **kwargs):
        super().__init__(**kwargs)
        self._hub = hub
        self._history: list[str] = []
        self._hist_idx: int = -1

    def compose(self) -> ComposeResult:
        with Container():
            yield Label("🐍  MicroPython REPL", classes="modal-title")
            yield RichLog(id="repl-output", highlight=True, markup=True)
            yield Input(placeholder=">>> type Python code here", id="repl-input")
            with Horizontal():
                yield Button("Send", id="repl-send", variant="success")
                yield Button("Ctrl-C", id="repl-int", variant="warning")
                yield Button("✕ Close", id="repl-close")

    @on(Input.Submitted, "#repl-input")
    def _input_submitted(self, event: Input.Submitted):
        self._execute()

    @on(Button.Pressed, "#repl-send")
    def _send(self):
        self._execute()

    def _execute(self):
        inp = self.query_one("#repl-input", Input)
        code = inp.value.strip()
        if not code:
            return
        out = self.query_one("#repl-output", RichLog)
        out.write(Text(f">>> {code}", style="bold cyan"))
        self._history.append(code)
        self._hist_idx = -1
        inp.value = ""
        self._do_eval(code)

    @work(thread=True)
    def _do_eval(self, code: str):
        try:
            result = self._hub.eval_python(code, timeout=5.0)
            self.app.call_from_thread(
                lambda: self.query_one("#repl-output", RichLog).write(
                    Text(result or "(no output)", style="green" if result else "dim")
                )
            )
        except Exception as e:
            self.app.call_from_thread(
                lambda: self.query_one("#repl-output", RichLog).write(
                    Text(f"Error: {e}", style="bold red")
                )
            )

    @on(Button.Pressed, "#repl-int")
    def _interrupt(self):
        try:
            self._hub.mp_interrupt()
            self.query_one("#repl-output", RichLog).write(
                Text("^C (interrupt sent)", style="yellow"))
        except Exception as e:
            logger.warning(f"Interrupt failed: {e}")

    @on(Button.Pressed, "#repl-close")
    def _close(self):
        self.dismiss()


class BatteryModal(ModalScreen):
    """Battery and power details overlay."""

    DEFAULT_CSS = """
    BatteryModal {
        align: center middle;
    }
    BatteryModal > Container {
        width: 45;
        height: 14;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    def __init__(self, hub: Hub, **kwargs):
        super().__init__(**kwargs)
        self._hub = hub

    def compose(self) -> ComposeResult:
        with Container():
            yield Label("🔋  Battery Info", classes="modal-title")
            yield Static("Loading...", id="batt-info")
            with Horizontal():
                yield Button("🔄 Refresh", id="batt-refresh", variant="primary")
                yield Button("✕ Close", id="batt-close")

    def on_mount(self):
        self._refresh_info()

    @on(Button.Pressed, "#batt-refresh")
    def _on_refresh(self):
        self._refresh_info()

    @work(thread=True)
    def _refresh_info(self):
        try:
            pct = self._hub.get_battery()
            voltage = self._hub.battery_voltage()
            current = self._hub.battery_current()
            charger = self._hub.charger_connected()
            text = Text()
            text.append(f"  Level:    ", style="bold")
            text.append(f"{pct}%\n", style=_battery_color(pct))
            text.append(f"  Voltage:  {voltage} mV\n")
            text.append(f"  Current:  {current} mA\n")
            text.append(f"  Charger:  {'🔌 Connected' if charger else '🔋 Battery'}\n")
            self.app.call_from_thread(
                lambda: self.query_one("#batt-info", Static).update(text)
            )
        except Exception as e:
            self.app.call_from_thread(
                lambda: self.query_one("#batt-info", Static).update(f"Error: {e}")
            )


class ProgramsModal(ModalScreen):
    """Overlay for program slot management."""

    DEFAULT_CSS = """
    ProgramsModal {
        align: center middle;
    }
    ProgramsModal > Container {
        width: 60;
        height: 20;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    def __init__(self, hub: Hub, **kwargs):
        super().__init__(**kwargs)
        self._hub = hub

    def compose(self) -> ComposeResult:
        with Container():
            yield Label("📁  Programs", classes="modal-title")
            yield Label("Slot (0-19):")
            yield Input(placeholder="0", id="prog-slot")
            with Horizontal():
                yield Button("▶ Run",  id="prog-run",  variant="success")
                yield Button("■ Stop", id="prog-stop", variant="error")
            yield Label("")
            yield Label("List files in path (e.g. /flash/fs):")
            yield Input(placeholder="/flash/fs", id="prog-path")
            yield Button("List", id="prog-list", variant="primary")
            yield Static("", id="prog-output")
            yield Button("✕ Close", id="prog-close")

    @on(Button.Pressed, "#prog-run")
    def _run(self):
        try:
            slot = int(self.query_one("#prog-slot", Input).value or "0")
            self._hub.start_program(slot)
        except Exception as e:
            logger.warning(f"Start program failed: {e}")

    @on(Button.Pressed, "#prog-stop")
    def _stop(self):
        try:
            self._hub.stop_program()
        except Exception as e:
            logger.warning(f"Stop program failed: {e}")

    @on(Button.Pressed, "#prog-list")
    def _list(self):
        path = self.query_one("#prog-path", Input).value or "/flash/fs"
        try:
            result = self._hub.list_path(path)
            out = self.query_one("#prog-output", Static)
            if result:
                lines = "\n".join(f"  {f}" for f in result.files[:20])
                out.update(f"Files:\n{lines}")
            else:
                out.update("(no result)")
        except Exception as e:
            self.query_one("#prog-output", Static).update(f"Error: {e}")

    @on(Button.Pressed, "#prog-close")
    def _close(self):
        self.dismiss()


# ─────────────────────────────────────────────────────────────────────
# Dashboard Screen
# ─────────────────────────────────────────────────────────────────────

class DashboardScreen(Screen):
    """Live hub dashboard — shown after successful connection."""

    BINDINGS = [
        Binding("m", "motor",    "Motor"),
        Binding("d", "display",  "Display"),
        Binding("s", "sound",    "Sound"),
        Binding("p", "programs", "Programs"),
        Binding("l", "light",    "Light"),
        Binding("r", "repl",     "REPL"),
        Binding("b", "battery",  "Battery"),
        Binding("y", "reset_yaw","Yaw Reset"),
        Binding("ctrl+d", "disconnect", "Disconnect"),
        Binding("q", "app.quit", "Quit"),
    ]

    CSS = """
    #status-bar {
        dock: top;
        height: 1;
        background: $boost;
        padding: 0 1;
    }
    #main-layout {
        layout: grid;
        grid-size: 3 2;
        grid-rows: 13 1fr;
        height: 1fr;
    }
    #matrix-cell  { column-span: 1; row-span: 1; }
    #ports-cell   { column-span: 2; row-span: 1; }
    #imu-cell     { column-span: 1; row-span: 1; }
    #log-cell     { column-span: 2; row-span: 1; }
    #hub-log      { height: 1fr; border: round $accent; }
    """

    def __init__(self, hub: Hub, conn_label: str, **kwargs):
        super().__init__(**kwargs)
        self._hub = hub
        self._conn_label = conn_label
        self._log_handler: Optional[_TuiLogHandler] = None

    def compose(self) -> ComposeResult:
        yield HubStatusBar(self._hub, self._conn_label, id="status-bar")
        with Container(id="main-layout"):
            with Container(id="matrix-cell"):
                yield MatrixPanel(self._hub, id="matrix-panel")
            with Container(id="ports-cell"):
                yield PortsPanel(self._hub, id="ports-panel")
            with Container(id="imu-cell"):
                yield ImuPanel(self._hub, id="imu-panel")
            with Container(id="log-cell"):
                yield RichLog(id="hub-log", highlight=True, markup=True)
        yield Footer()

    def on_mount(self):
        # Route spike3 logs into the RichLog widget
        log_widget = self.query_one("#hub-log", RichLog)
        self._log_handler = _TuiLogHandler(log_widget)
        self._log_handler.setLevel(logging.DEBUG)
        logging.getLogger("spike3").addHandler(self._log_handler)

        # Fetch hub info in background
        self._init_hub()

        # Start refresh timer
        self.set_interval(0.2, self._refresh_all)

    def _refresh_all(self):
        try:
            self.query_one("#status-bar", HubStatusBar).refresh_data()
            self.query_one("#matrix-panel", MatrixPanel).refresh_data()
            self.query_one("#ports-panel", PortsPanel).refresh_data()
            self.query_one("#imu-panel", ImuPanel).refresh_data()
        except NoMatches:
            pass

    @work(thread=True)
    def _init_hub(self):
        """Fetch hub name and firmware info after connect (background thread)."""
        try:
            name = self._hub.get_hub_name()
            info = self._hub._info
            fw = f"{info.fw_major}.{info.fw_minor}.{info.fw_build}" if info else "?.?.?"
            self.app.call_from_thread(
                lambda: self.query_one("#status-bar", HubStatusBar).set_info(name, fw)
            )
        except Exception:
            pass

        # Start streaming notifications at 50ms
        try:
            self._hub.set_notification_interval(50)
        except Exception:
            pass

    def on_unmount(self):
        if self._log_handler:
            logging.getLogger("spike3").removeHandler(self._log_handler)

    # ── Actions ──────────────────────────────────────────────────────

    def action_motor(self):
        self.app.push_screen(MotorModal(self._hub))

    def action_display(self):
        self.app.push_screen(DisplayModal(self._hub))

    def action_sound(self):
        self.app.push_screen(SoundModal(self._hub))

    def action_programs(self):
        self.app.push_screen(ProgramsModal(self._hub))

    def action_light(self):
        self.app.push_screen(HubLightModal(self._hub))

    def action_repl(self):
        self.app.push_screen(ReplModal(self._hub))

    def action_battery(self):
        self.app.push_screen(BatteryModal(self._hub))

    def action_reset_yaw(self):
        try:
            self._hub.reset_yaw()
        except Exception as e:
            logger.warning(f"Reset yaw failed: {e}")

    def action_disconnect(self):
        try:
            self._hub.close()
        except Exception:
            pass
        self.app.pop_screen()


# ─────────────────────────────────────────────────────────────────────
# Connect Screen
# ─────────────────────────────────────────────────────────────────────

class ConnectScreen(Screen):
    """Initial connection picker screen."""

    BINDINGS = [
        Binding("r", "refresh", "Refresh USB"),
        Binding("q", "app.quit", "Quit"),
    ]

    CSS = """
    ConnectScreen {
        layout: vertical;
    }
    #logo-box {
        height: 8;
        background: $boost;
        content-align: center middle;
        padding: 1 4;
    }
    #tabs-container {
        height: 1fr;
        padding: 1 2;
    }
    #usb-list {
        height: 10;
        border: round $accent;
        margin-bottom: 1;
    }
    #ble-list {
        height: 8;
        border: round $accent;
        margin-bottom: 1;
    }
    #connect-btn {
        width: 20;
        margin-top: 1;
    }
    .section-label {
        color: $accent;
        text-style: bold;
        margin-bottom: 0;
    }
    .hint-text {
        color: $text-muted;
        margin-bottom: 1;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._usb_hubs: list[dict] = []
        self._ble_devices: list[dict] = []
        self._selected_usb: Optional[int] = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="logo-box"):
            yield Static(
                "[bold cyan]SPIKE 3 Hub TUI[/bold cyan]\n"
                "[dim]Real-time hub dashboard & control[/dim]",
                markup=True,
            )
        with Container(id="tabs-container"):
            with TabbedContent():
                with TabPane("🔌 USB", id="tab-usb"):
                    yield Label("Detected SPIKE hubs on USB:", classes="section-label")
                    yield Label("(Auto-scanned on startup. Press [r] to refresh.)", classes="hint-text")
                    yield ListView(id="usb-list")
                    yield Button("▶ Connect USB", id="btn-usb-connect", variant="success")

                with TabPane("📶 BLE", id="tab-ble"):
                    yield Label("BLE SPIKE hubs nearby:", classes="section-label")
                    yield Label("(Press Scan to discover Bluetooth hubs)", classes="hint-text")
                    yield ListView(id="ble-list")
                    with Horizontal():
                        yield Button("🔍 Scan BLE", id="btn-ble-scan", variant="primary")
                        yield Button("▶ Connect BLE", id="btn-ble-connect", variant="success")

                with TabPane("🖧 TCP", id="tab-tcp"):
                    yield Label("Connect to simulator or remote hub via TCP:", classes="section-label")
                    yield Label(
                        "Start simulator: [bold]python -m spike3.simulator --tcp[/bold]\n"
                        "On Windows: use --tcp flag (no PTY available)",
                        markup=True, classes="hint-text"
                    )
                    yield Label("Host:")
                    yield Input(placeholder="127.0.0.1", id="tcp-host", value="127.0.0.1")
                    yield Label("Port:")
                    yield Input(placeholder="51337", id="tcp-port", value="51337")
                    yield Button("▶ Connect TCP", id="btn-tcp-connect", variant="success")

        yield Footer()

    def on_mount(self):
        self._scan_usb()

    def _scan_usb(self):
        """Scan USB ports for SPIKE hubs and populate the list."""
        self._usb_hubs = Hub.find_hubs()
        lv = self.query_one("#usb-list", ListView)
        lv.clear()
        if self._usb_hubs:
            for h in self._usb_hubs:
                pid_name = h["product_id"].name if h.get("product_id") else f"PID={h['pid']}"
                lv.append(ListItem(
                    Label(f"[bold]{h['port']}[/bold]  {pid_name}  — {h['description']}", markup=True)
                ))
            self._selected_usb = 0
        else:
            lv.append(ListItem(Label("[dim]No SPIKE hubs found (check USB connection)[/dim]", markup=True)))
            self._selected_usb = None

    def action_refresh(self):
        self._scan_usb()

    @on(ListView.Highlighted, "#usb-list")
    def _usb_selected(self, event: ListView.Highlighted):
        self._selected_usb = event.list_view.index

    @on(Button.Pressed, "#btn-usb-connect")
    def _connect_usb(self):
        if self._selected_usb is None or self._selected_usb >= len(self._usb_hubs):
            return
        hub_info = self._usb_hubs[self._selected_usb]
        port = hub_info["port"]
        self._do_connect_usb(port)

    @work(thread=True)
    def _do_connect_usb(self, port: str):
        try:
            hub = Hub.connect_usb(port)
            conn_label = f"USB {port}"
            self.app.call_from_thread(lambda: self._open_dashboard(hub, conn_label))
        except Exception as e:
            self.app.call_from_thread(
                lambda: self.app.notify(f"USB connect failed: {e}", severity="error")
            )

    @on(Button.Pressed, "#btn-ble-scan")
    def _ble_scan(self):
        self.app.notify("Scanning BLE (10s)…")
        self._do_ble_scan()

    @work(thread=True)
    def _do_ble_scan(self):
        try:
            from .transport import BleTransport
            devices = asyncio.run(BleTransport.scan(timeout=10.0))
            self._ble_devices = devices
            self.app.call_from_thread(lambda: self._update_ble_list(devices))
        except Exception as e:
            self.app.call_from_thread(
                lambda: self.app.notify(f"BLE scan failed: {e}", severity="warning")
            )

    def _update_ble_list(self, devices: list[dict]):
        lv = self.query_one("#ble-list", ListView)
        lv.clear()
        if devices:
            for d in devices:
                lv.append(ListItem(
                    Label(f"[bold]{d['name']}[/bold]  {d['address']}  RSSI:{d.get('rssi','?')}", markup=True)
                ))
        else:
            lv.append(ListItem(Label("[dim]No SPIKE hubs found via BLE[/dim]", markup=True)))

    @on(Button.Pressed, "#btn-ble-connect")
    def _connect_ble(self):
        lv = self.query_one("#ble-list", ListView)
        idx = lv.index
        if idx is None or idx >= len(self._ble_devices):
            self.app.notify("Select a BLE device first", severity="warning")
            return
        addr = self._ble_devices[idx]["address"]
        self._do_connect_ble(addr)

    @work(thread=True)
    def _do_connect_ble(self, addr: str):
        try:
            hub = Hub.connect_ble(addr)
            conn_label = f"BLE {addr}"
            self.app.call_from_thread(lambda: self._open_dashboard(hub, conn_label))
        except Exception as e:
            self.app.call_from_thread(
                lambda: self.app.notify(f"BLE connect failed: {e}", severity="error")
            )

    @on(Button.Pressed, "#btn-tcp-connect")
    def _connect_tcp(self):
        host = self.query_one("#tcp-host", Input).value.strip() or "127.0.0.1"
        port_str = self.query_one("#tcp-port", Input).value.strip() or "51337"
        try:
            tcp_port = int(port_str)
        except ValueError:
            self.app.notify("Invalid port number", severity="error")
            return
        self._do_connect_tcp(host, tcp_port)

    @work(thread=True)
    def _do_connect_tcp(self, host: str, port: int):
        try:
            hub = Hub.connect_tcp(host, port)
            conn_label = f"TCP {host}:{port}"
            self.app.call_from_thread(lambda: self._open_dashboard(hub, conn_label))
        except Exception as e:
            self.app.call_from_thread(
                lambda: self.app.notify(f"TCP connect failed: {e}", severity="error")
            )

    def _open_dashboard(self, hub: Hub, conn_label: str):
        self.app.push_screen(DashboardScreen(hub, conn_label))


# ─────────────────────────────────────────────────────────────────────
# Log handler
# ─────────────────────────────────────────────────────────────────────

class _TuiLogHandler(logging.Handler):
    """Routes logging records into a Textual RichLog widget."""

    LEVEL_STYLES = {
        logging.DEBUG:    "dim",
        logging.INFO:     "green",
        logging.WARNING:  "yellow",
        logging.ERROR:    "bold red",
        logging.CRITICAL: "bold white on red",
    }

    def __init__(self, widget: RichLog):
        super().__init__()
        self._widget = widget
        self.setFormatter(logging.Formatter("%(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord):
        style = self.LEVEL_STYLES.get(record.levelno, "")
        msg = self.format(record)
        try:
            self._widget.write(Text(msg, style=style))
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────
# Main App
# ─────────────────────────────────────────────────────────────────────

class HubApp(App):
    """SPIKE 3 Hub TUI — connect to a real hub or simulator."""

    TITLE = "SPIKE 3 Hub TUI"
    SUB_TITLE = "Powered by spike3"

    CSS = """
    .modal-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    Horizontal {
        height: auto;
    }
    Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
    ]

    def on_mount(self):
        self.push_screen(ConnectScreen())


def run_hub_tui(
    port: Optional[str] = None,
    tcp_host: Optional[str] = None,
    tcp_port: Optional[int] = None,
):
    """Launch the hub TUI, optionally connecting immediately.

    Args:
        port: USB serial port to auto-connect (skips ConnectScreen).
        tcp_host: TCP host to auto-connect.
        tcp_port: TCP port to auto-connect.
    """
    if port:
        # Auto-connect USB: directly open dashboard
        hub = Hub.connect_usb(port)

        class _AutoApp(App):
            TITLE = "SPIKE 3 Hub TUI"
            CSS = HubApp.CSS

            def on_mount(self):
                self.push_screen(DashboardScreen(hub, f"USB {port}"))

        _AutoApp().run()
    elif tcp_host and tcp_port:
        hub = Hub.connect_tcp(tcp_host, tcp_port)

        class _AutoTcpApp(App):
            TITLE = "SPIKE 3 Hub TUI"
            CSS = HubApp.CSS

            def on_mount(self):
                self.push_screen(DashboardScreen(hub, f"TCP {tcp_host}:{tcp_port}"))

        _AutoTcpApp().run()
    else:
        HubApp().run()
