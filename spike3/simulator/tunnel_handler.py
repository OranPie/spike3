"""Tunnel command handler for the SPIKE 3 simulator.

Parses incoming JSON-RPC scratch.* commands from TunnelMessages and
dispatches them to the appropriate simulated devices. Sends JSON-RPC
responses back through the tunnel.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from .hub_state import HubState
from .devices import Motor
from .responder import ProtocolResponder

logger = logging.getLogger("spike3.simulator.tunnel")


class TunnelHandler:
    """Handles scratch.* JSON-RPC commands arriving via tunnel."""

    def __init__(self, hub: HubState, responder: ProtocolResponder):
        self.hub = hub
        self.responder = responder
        # Wire ourselves into the responder's tunnel callback
        responder.on_tunnel = self.on_tunnel_data

    def on_tunnel_data(self, data: bytes):
        """Called when a TunnelMessage arrives from the host."""
        try:
            text = data.rstrip(b"\r\n\x00").decode("utf-8", errors="replace")
            if not text.startswith("{"):
                logger.debug(f"Non-JSON tunnel data ({len(data)}B)")
                return
            msg = json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(f"Failed to parse tunnel JSON: {e}")
            return

        msg_id = msg.get("i")
        method = msg.get("m", "")
        params = msg.get("p", {})

        logger.info(f"Tunnel: {method}({params})" +
                     (f" [id={msg_id}]" if msg_id else ""))

        # Dispatch to handler
        handler = self._METHODS.get(method)
        if handler:
            result = handler(self, params)
            if msg_id is not None:
                self._send_response(msg_id, result)
        else:
            logger.warning(f"Unknown tunnel method: {method}")
            if msg_id is not None:
                self._send_error(msg_id, f"Unknown method: {method}")

    def _send_response(self, msg_id: str, result=None):
        """Send a JSON-RPC response back through the tunnel."""
        resp = {"i": msg_id, "r": result if result is not None else 0}
        data = (json.dumps(resp, separators=(",", ":")) + "\r").encode("utf-8")
        self.responder.send_tunnel_response(data)

    def _send_error(self, msg_id: str, message: str):
        resp = {"i": msg_id, "e": message}
        data = (json.dumps(resp, separators=(",", ":")) + "\r").encode("utf-8")
        self.responder.send_tunnel_response(data)

    # ── Motor commands ─────────────────────────────────────────────

    def _motor_start(self, p: dict):
        port = p.get("port", 0)
        speed = p.get("speed", 0)
        stall = p.get("stall", True)
        motor = self.hub.get_motor(port)
        if motor:
            motor.start(speed, stall)
            logger.debug(f"Motor {port} started at speed {speed}")
        else:
            logger.warning(f"Motor start: no motor on port {port}")
        return 0

    def _motor_stop(self, p: dict):
        port = p.get("port", 0)
        stop = p.get("stop", 1)
        motor = self.hub.get_motor(port)
        if motor:
            motor.stop(stop)
            logger.debug(f"Motor {port} stopped (mode={stop})")
        return 0

    def _motor_run_degrees(self, p: dict):
        port = p.get("port", 0)
        speed = p.get("speed", 50)
        degrees = p.get("degrees", 360)
        stall = p.get("stall", True)
        stop = p.get("stop", 1)
        motor = self.hub.get_motor(port)
        if motor:
            motor.run_degrees(speed, degrees, stop)
        return 0

    def _motor_run_timed(self, p: dict):
        port = p.get("port", 0)
        speed = p.get("speed", 50)
        time_ms = p.get("time", 1000)
        stop = p.get("stop", 1)
        motor = self.hub.get_motor(port)
        if motor:
            motor.run_timed(speed, time_ms, stop)
        return 0

    def _motor_go_to_position(self, p: dict):
        port = p.get("port", 0)
        speed = p.get("speed", 50)
        position = p.get("position", 0)
        direction = p.get("direction", "shortest")
        stop = p.get("stop", 1)
        motor = self.hub.get_motor(port)
        if motor:
            motor.go_to_position(speed, position, stop)
        return 0

    def _motor_go_relative(self, p: dict):
        return self._motor_go_to_position(p)

    def _motor_set_position(self, p: dict):
        port = p.get("port", 0)
        offset = p.get("offset", 0)
        motor = self.hub.get_motor(port)
        if motor:
            motor.set_position_offset(offset)
        return 0

    def _motor_pwm(self, p: dict):
        port = p.get("port", 0)
        power = p.get("power", 0)
        motor = self.hub.get_motor(port)
        if motor:
            motor.start(power)  # Approximate: PWM ≈ speed
        return 0

    # ── Display commands ───────────────────────────────────────────

    def _display_image(self, p: dict):
        image = p.get("image", "0" * 25)
        self.hub.matrix.set_image(image)
        logger.debug(f"Display image set")
        return 0

    def _display_image_for(self, p: dict):
        image = p.get("image", "0" * 25)
        duration = p.get("duration", 1000)
        self.hub.matrix.set_image(image)
        # In real hub, would auto-clear after duration — we just set it
        return 0

    def _display_text(self, p: dict):
        text = p.get("text", "")
        # Simulate: just show first char or clear
        if text:
            logger.info(f"Display text: {text}")
            # Show first character as a simple pattern
            self.hub.matrix.clear()
        return 0

    def _display_set_pixel(self, p: dict):
        x = p.get("x", 0)
        y = p.get("y", 0)
        brightness = p.get("brightness", 100)
        self.hub.matrix.set_pixel(x, y, brightness)
        return 0

    def _display_clear(self, p: dict):
        self.hub.matrix.clear()
        return 0

    def _display_rotate(self, p: dict):
        return 0  # No-op for now

    # ── Sound commands ─────────────────────────────────────────────

    def _sound_beep(self, p: dict):
        self.hub.sound_playing = True
        self.hub.sound_note = p.get("note", 60)
        self.hub.sound_volume = p.get("volume", 100)
        logger.debug(f"Sound beep: note={self.hub.sound_note} vol={self.hub.sound_volume}")
        return 0

    def _sound_beep_for_time(self, p: dict):
        self.hub.sound_playing = True
        self.hub.sound_note = p.get("note", 60)
        self.hub.sound_volume = p.get("volume", 100)
        duration = p.get("duration", 1000)
        # In real hub, sound auto-stops — we just set the flag
        return 0

    def _sound_off(self, p: dict):
        self.hub.sound_playing = False
        self.hub.sound_note = 0
        self.hub.sound_volume = 0
        return 0

    # ── Sensor commands ────────────────────────────────────────────

    def _ultrasonic_light_up(self, p: dict):
        return 0  # No-op — ultrasonic LEDs not simulated

    # ── Motor pair commands ────────────────────────────────────────

    def _move_tank_degrees(self, p: dict):
        lp = p.get("left_port", 0)
        rp = p.get("right_port", 1)
        ls = p.get("lspeed", 50)
        rs = p.get("rspeed", 50)
        deg = p.get("degrees", 360)
        stop = p.get("stop", 1)
        lm = self.hub.get_motor(lp)
        rm = self.hub.get_motor(rp)
        if lm:
            lm.run_degrees(ls, deg, stop)
        if rm:
            rm.run_degrees(rs, deg, stop)
        return 0

    def _move_tank_timed(self, p: dict):
        lp = p.get("left_port", 0)
        rp = p.get("right_port", 1)
        ls = p.get("lspeed", 50)
        rs = p.get("rspeed", 50)
        time_ms = p.get("time", 1000)
        stop = p.get("stop", 1)
        lm = self.hub.get_motor(lp)
        rm = self.hub.get_motor(rp)
        if lm:
            lm.run_timed(ls, time_ms, stop)
        if rm:
            rm.run_timed(rs, time_ms, stop)
        return 0

    def _move_start_powers(self, p: dict):
        lp = p.get("left_port", 0)
        rp = p.get("right_port", 1)
        lm = self.hub.get_motor(lp)
        rm = self.hub.get_motor(rp)
        if lm:
            lm.start(p.get("left_power", 0))
        if rm:
            rm.start(p.get("right_power", 0))
        return 0

    def _move_start_speeds(self, p: dict):
        lp = p.get("left_port", 0)
        rp = p.get("right_port", 1)
        lm = self.hub.get_motor(lp)
        rm = self.hub.get_motor(rp)
        if lm:
            lm.start(p.get("left_speed", 0))
        if rm:
            rm.start(p.get("right_speed", 0))
        return 0

    def _move_stop(self, p: dict):
        lp = p.get("left_port", 0)
        rp = p.get("right_port", 1)
        stop = p.get("stop", 1)
        lm = self.hub.get_motor(lp)
        rm = self.hub.get_motor(rp)
        if lm:
            lm.stop(stop)
        if rm:
            rm.stop(stop)
        return 0

    # ── Hub control commands ───────────────────────────────────────

    def _reset_yaw(self, p: dict):
        self.hub.imu.yaw = 0
        return 0

    def _set_orientation(self, p: dict):
        # Just accept it — orientation reference change
        return 0

    def _hub_light_on(self, p: dict):
        logger.debug(f"Hub light color: {p.get('color', 0)}")
        return 0

    def _hub_light_off(self, p: dict):
        return 0

    def _play_sound(self, p: dict):
        self.hub.sound_playing = True
        self.hub.sound_volume = p.get("volume", 100)
        logger.debug(f"Play sound: {p.get('path', '?')}")
        return 0

    # ── Color matrix commands ──────────────────────────────────────

    def _color_matrix_set_image(self, p: dict):
        return 0  # No-op for now

    def _color_matrix_set_pixel(self, p: dict):
        return 0

    def _color_matrix_clear(self, p: dict):
        return 0

    # ── Program commands ───────────────────────────────────────────

    def _program_terminate(self, p: dict):
        self.hub.stop_program()
        return 0

    # ── Dispatch table ─────────────────────────────────────────────

    _METHODS = {
        "scratch.motor_start": _motor_start,
        "scratch.motor_stop": _motor_stop,
        "scratch.motor_run_for_degrees": _motor_run_degrees,
        "scratch.motor_run_timed": _motor_run_timed,
        "scratch.motor_go_direction_to_position": _motor_go_to_position,
        "scratch.motor_go_to_relative_position": _motor_go_relative,
        "scratch.motor_set_position": _motor_set_position,
        "scratch.motor_pwm": _motor_pwm,
        "scratch.display_image": _display_image,
        "scratch.display_image_for": _display_image_for,
        "scratch.display_text": _display_text,
        "scratch.display_set_pixel": _display_set_pixel,
        "scratch.display_clear": _display_clear,
        "scratch.display_rotate_direction": _display_rotate,
        "scratch.display_rotate_orientation": _display_rotate,
        "scratch.sound_beep": _sound_beep,
        "scratch.sound_beep_for_time": _sound_beep_for_time,
        "scratch.sound_off": _sound_off,
        "scratch.play_sound": _play_sound,
        "scratch.ultrasonic_light_up": _ultrasonic_light_up,
        "scratch.move_tank_degrees": _move_tank_degrees,
        "scratch.move_tank_time": _move_tank_timed,
        "scratch.move_start_powers": _move_start_powers,
        "scratch.move_start_speeds": _move_start_speeds,
        "scratch.move_stop": _move_stop,
        "scratch.color_matrix_set_image": _color_matrix_set_image,
        "scratch.color_matrix_set_pixel": _color_matrix_set_pixel,
        "scratch.color_matrix_clear": _color_matrix_clear,
        "scratch.hub_light_on": _hub_light_on,
        "scratch.hub_light_off": _hub_light_off,
        "scratch.reset_yaw": _reset_yaw,
        "scratch.set_orientation": _set_orientation,
        "program_terminate": _program_terminate,
    }
