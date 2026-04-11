"""Atlantis protocol responder for the SPIKE 3 simulator.

Decodes incoming Atlantis request messages and generates appropriate
response bytes. Handles all known message types including:
  - InfoRequest → InfoResponse
  - GetHubName → GetHubNameResponse
  - SetHubName → SetHubNameResponse
  - DeviceUUID → DeviceUuidResponse
  - DeviceNotificationRequest → DeviceNotificationResponse (+ starts periodic notifications)
  - ProgramFlow → ProgramFlowResponse (+ notifications)
  - File upload/download flow
  - Slot management (clear, move, list, delete)
  - TunnelMessage → dispatched to tunnel_handler
"""

from __future__ import annotations

import logging
import struct
import time
from typing import Callable, Optional

from ..enums import MsgId, Status
from .. import cobs, atlantis
from .hub_state import HubState

logger = logging.getLogger("spike3.simulator.responder")


class ProtocolResponder:
    """Processes decoded Atlantis messages and generates responses.

    Args:
        hub: The HubState instance to read/write.
        send_fn: Callback to send raw bytes back to the client
                 (already COBS-encoded + framed).
    """

    def __init__(self, hub: HubState,
                 send_fn: Callable[[bytes], None]):
        self.hub = hub
        self._send = send_fn
        self.on_tunnel: Optional[Callable[[bytes], None]] = None
        self.on_console: Optional[Callable[[str], None]] = None

    def send_response(self, raw: bytes):
        """COBS-encode and send response bytes."""
        framed = cobs.encode(raw)
        logger.debug(f"SIM TX ({len(framed)}): {framed.hex(' ')}")
        self._send(framed)

    def handle_message(self, data: bytes):
        """Process a decoded (post-COBS) Atlantis message.

        Args:
            data: Raw message bytes where data[0] is the msg_id.
        """
        if not data:
            return

        msg_id = data[0]
        payload = data[1:]

        logger.debug(f"SIM RX msg_id={msg_id} payload({len(payload)}): {payload.hex(' ') if payload else '(empty)'}")

        handler = self._HANDLERS.get(msg_id)
        if handler:
            handler(self, payload)
        else:
            logger.warning(f"Unknown msg_id={msg_id}, ignoring")

    # ── Individual message handlers ────────────────────────────────

    def _handle_info_req(self, payload: bytes):
        resp = struct.pack("<B BBH BBH HHH H",
                           MsgId.INFO_RESP,
                           self.hub.rpc_major, self.hub.rpc_minor, self.hub.rpc_build,
                           self.hub.fw_major, self.hub.fw_minor, self.hub.fw_build,
                           self.hub.max_packet_size, self.hub.max_message_size,
                           self.hub.max_chunk_size, self.hub.product_group_device)
        self.send_response(resp)

    def _handle_get_hub_name(self, payload: bytes):
        name_bytes = self.hub.name.encode("utf-8") + b"\x00"
        resp = bytes([MsgId.GET_HUB_NAME_RESP]) + name_bytes
        self.send_response(resp)

    def _handle_set_hub_name(self, payload: bytes):
        # payload = NUL-terminated name string
        if payload:
            name = payload.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
            self.hub.name = name
            logger.info(f"Hub name set to: {name}")
        self.send_response(bytes([MsgId.SET_HUB_NAME_RESP, Status.ACK]))

    def _handle_device_uuid(self, payload: bytes):
        uuid_bytes = self.hub.uuid.encode("utf-8") + b"\x00"
        resp = bytes([MsgId.DEVICE_UUID_RESP]) + uuid_bytes
        self.send_response(resp)

    def _handle_device_notification_req(self, payload: bytes):
        interval_ms = struct.unpack_from("<H", payload, 0)[0] if len(payload) >= 2 else 0
        self.hub.enable_notifications(interval_ms)
        logger.info(f"Notifications {'enabled' if interval_ms > 0 else 'disabled'} "
                     f"(interval={interval_ms}ms)")
        self.send_response(bytes([MsgId.DEVICE_NOTIFICATION_RESP, Status.ACK]))

    def _handle_program_flow(self, payload: bytes):
        if len(payload) < 2:
            self.send_response(bytes([MsgId.PROGRAM_FLOW_RESP, Status.NACK]))
            return
        action = payload[0]
        slot = payload[1]
        if action == 0:  # START
            ok = self.hub.start_program(slot)
            self.send_response(bytes([MsgId.PROGRAM_FLOW_RESP,
                                      Status.ACK if ok else Status.NACK]))
            if ok:
                # Send ProgramFlowNotification (program started)
                # Official: uint8(msg_type) + uint8(action), action=0 for Start
                notif = bytes([MsgId.PROGRAM_FLOW_NOTIFICATION, 0])  # 0 = Start
                self.send_response(notif)
        elif action == 1:  # STOP
            self.hub.stop_program()
            self.send_response(bytes([MsgId.PROGRAM_FLOW_RESP, Status.ACK]))
        else:
            self.send_response(bytes([MsgId.PROGRAM_FLOW_RESP, Status.NACK]))

    def _handle_start_file_upload(self, payload: bytes):
        # Official: string[32](filename) + u8(slot) + u32(crc) = 37 bytes
        try:
            # Fixed 32-byte filename field (null-terminated, padded)
            filename_raw = payload[:32]
            nul_idx = filename_raw.index(0) if 0 in filename_raw else 32
            filename = filename_raw[:nul_idx].decode("utf-8", errors="replace")
            slot = payload[32]
            file_crc = struct.unpack_from("<I", payload, 33)[0]
            ok = self.hub.storage.begin_upload(filename, slot, file_crc)
            logger.info(f"Upload started: {filename} → slot {slot} (crc=0x{file_crc:08X})")
            self.send_response(bytes([MsgId.START_FILE_UPLOAD_RESP,
                                      Status.ACK if ok else Status.NACK]))
        except Exception as e:
            logger.error(f"Upload start error: {e}")
            self.send_response(bytes([MsgId.START_FILE_UPLOAD_RESP, Status.NACK]))

    def _handle_transfer_chunk(self, payload: bytes):
        # Official: u32(running_crc) + u16(chunk_size) + u8[chunk_size](data) = 6 + chunk_size
        if len(payload) < 6:
            self.send_response(bytes([MsgId.TRANSFER_CHUNK_RESP, Status.NACK]))
            return
        running_crc = struct.unpack_from("<I", payload, 0)[0]
        chunk_size = struct.unpack_from("<H", payload, 4)[0]
        chunk_data = payload[6:6 + chunk_size]
        if chunk_data:
            ok = self.hub.storage.append_chunk(running_crc, chunk_data)
        else:
            # Empty chunk = upload complete
            ok = self.hub.storage.finish_upload()
            if ok:
                logger.info("Upload completed successfully")
        self.send_response(bytes([MsgId.TRANSFER_CHUNK_RESP,
                                  Status.ACK if ok else Status.NACK]))

    def _handle_start_file_download(self, payload: bytes):
        try:
            nul_idx = payload.index(0)
            filename = payload[:nul_idx].decode("utf-8", errors="replace")
            slot = payload[nul_idx + 1] if len(payload) > nul_idx + 1 else 0
            result = self.hub.storage.begin_download(filename, slot)
            if result:
                status, crc = result
                resp = bytes([MsgId.START_FILE_DOWNLOAD_RESP, status]) + struct.pack("<I", crc)
                self.send_response(resp)
            else:
                self.send_response(bytes([MsgId.START_FILE_DOWNLOAD_RESP, Status.NACK,
                                          0, 0, 0, 0]))
        except Exception as e:
            logger.error(f"Download start error: {e}")
            self.send_response(bytes([MsgId.START_FILE_DOWNLOAD_RESP, Status.NACK,
                                      0, 0, 0, 0]))

    def _handle_clear_slot(self, payload: bytes):
        slot = payload[0] if payload else 0
        ok = self.hub.storage.clear_slot(slot)
        self.send_response(bytes([MsgId.CLEAR_SLOT_RESP,
                                  Status.ACK if ok else Status.NACK]))

    def _handle_move_slot(self, payload: bytes):
        if len(payload) < 2:
            self.send_response(bytes([MsgId.MOVE_SLOT_RESP, Status.NACK]))
            return
        from_slot, to_slot = payload[0], payload[1]
        ok = self.hub.storage.move_slot(from_slot, to_slot)
        self.send_response(bytes([MsgId.MOVE_SLOT_RESP,
                                  Status.ACK if ok else Status.NACK]))

    def _handle_list_path(self, payload: bytes):
        try:
            nul_idx = payload.index(0)
            path = payload[:nul_idx].decode("utf-8", errors="replace")
            slot = payload[nul_idx + 1] if len(payload) > nul_idx + 1 else 0
        except (ValueError, IndexError):
            path, slot = "", 0
        data = self.hub.storage.list_path_response_data(path, slot)
        resp = bytes([MsgId.LIST_PATH_RESP]) + data
        self.send_response(resp)

    def _handle_delete_path(self, payload: bytes):
        try:
            nul_idx = payload.index(0)
            path = payload[:nul_idx].decode("utf-8", errors="replace")
            slot = payload[nul_idx + 1] if len(payload) > nul_idx + 1 else 0
        except (ValueError, IndexError):
            path, slot = "", 0
        ok = self.hub.storage.delete_path(path, slot)
        self.send_response(bytes([MsgId.DELETE_PATH_RESP,
                                  Status.ACK if ok else Status.NACK]))

    def _handle_tunnel_message(self, payload: bytes):
        if len(payload) < 2:
            return
        data_len = struct.unpack_from("<H", payload, 0)[0]
        tunnel_data = payload[2:2 + data_len]
        if self.on_tunnel:
            self.on_tunnel(tunnel_data)

    def _handle_console_notification(self, payload: bytes):
        """ConsoleNotification (msg_id=33): Python REPL code from host."""
        # Payload: UTF-8 text + optional null terminator
        text = payload.rstrip(b"\x00").decode("utf-8", errors="replace")
        if self.on_console:
            self.on_console(text)

    def _handle_begin_fw_update(self, payload: bytes):
        # Firmware update — just ACK it (we don't actually update)
        self.send_response(bytes([MsgId.BEGIN_FW_UPDATE_RESP, Status.ACK]))

    def _handle_start_fw_upload(self, payload: bytes):
        self.send_response(bytes([MsgId.START_FW_UPLOAD_RESP, Status.ACK]))

    # ── Send unsolicited notifications ─────────────────────────────

    def send_device_notification(self):
        """Build and send a DeviceNotification with current sensor states."""
        notif_payload = self.hub.build_notification_payload()
        raw = bytes([MsgId.DEVICE_NOTIFICATION]) + notif_payload
        self.send_response(raw)

    def send_console_text(self, text: str):
        """Send a ConsoleNotification."""
        raw = bytes([MsgId.CONSOLE_NOTIFICATION]) + text.encode("utf-8") + b"\x00"
        self.send_response(raw)

    def send_tunnel_response(self, data: bytes):
        """Send a TunnelMessage back to the host."""
        raw = bytes([MsgId.TUNNEL_MESSAGE]) + struct.pack("<H", len(data)) + data
        self.send_response(raw)

    # ── Handler dispatch table ─────────────────────────────────────

    _HANDLERS = {
        MsgId.INFO_REQ: _handle_info_req,
        MsgId.GET_HUB_NAME_REQ: _handle_get_hub_name,
        MsgId.SET_HUB_NAME_REQ: _handle_set_hub_name,
        MsgId.DEVICE_UUID_REQ: _handle_device_uuid,
        MsgId.DEVICE_NOTIFICATION_REQ: _handle_device_notification_req,
        MsgId.PROGRAM_FLOW_REQ: _handle_program_flow,
        MsgId.START_FILE_UPLOAD_REQ: _handle_start_file_upload,
        MsgId.TRANSFER_CHUNK_REQ: _handle_transfer_chunk,
        MsgId.START_FILE_DOWNLOAD_REQ: _handle_start_file_download,
        MsgId.CLEAR_SLOT_REQ: _handle_clear_slot,
        MsgId.MOVE_SLOT_REQ: _handle_move_slot,
        MsgId.LIST_PATH_REQ: _handle_list_path,
        MsgId.DELETE_PATH_REQ: _handle_delete_path,
        MsgId.TUNNEL_MESSAGE: _handle_tunnel_message,
        MsgId.CONSOLE_NOTIFICATION: _handle_console_notification,
        MsgId.BEGIN_FW_UPDATE_REQ: _handle_begin_fw_update,
        MsgId.START_FW_UPLOAD_REQ: _handle_start_fw_upload,
    }
