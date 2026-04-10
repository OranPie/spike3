"""Atlantis binary protocol: message types, serialization, deserialization.

Implements the SPIKE 3 Atlantis protocol exactly as found in the
deobfuscated JS (module 53120 in 165.06627f63.deobf.js).
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field
from typing import Any

from .enums import (
    MsgId, Status, ProgramAction, NotifSubId, Port, Orientation, Color,
)

logger = logging.getLogger("spike3.atlantis")


# ── Primitive helpers (all little-endian) ───────────────────────────

def _u8(v: int) -> bytes:
    return struct.pack("<B", v & 0xFF)

def _i8(v: int) -> bytes:
    return struct.pack("<b", v)

def _u16(v: int) -> bytes:
    return struct.pack("<H", v & 0xFFFF)

def _i16(v: int) -> bytes:
    return struct.pack("<h", v)

def _u32(v: int) -> bytes:
    return struct.pack("<I", v & 0xFFFFFFFF)

def _i32(v: int) -> bytes:
    return struct.pack("<i", v)

def _str_nul(s: str) -> bytes:
    return s.encode("utf-8") + b"\x00"


# ── Message dataclasses ────────────────────────────────────────────

@dataclass
class InfoRequest:
    msg_id: int = MsgId.INFO_REQ

    def to_bytes(self) -> bytes:
        return _u8(self.msg_id)


@dataclass
class InfoResponse:
    msg_id: int = MsgId.INFO_RESP
    rpc_major: int = 0
    rpc_minor: int = 0
    rpc_build: int = 0
    fw_major: int = 0
    fw_minor: int = 0
    fw_build: int = 0
    max_packet_size: int = 0
    max_message_size: int = 0
    max_chunk_size: int = 0
    product_group_device: int = 0


@dataclass
class DeviceNotificationRequest:
    msg_id: int = MsgId.DEVICE_NOTIFICATION_REQ
    delay_ms: int = 0

    def to_bytes(self) -> bytes:
        return _u8(self.msg_id) + _u16(self.delay_ms)


@dataclass
class DeviceNotificationResponse:
    msg_id: int = MsgId.DEVICE_NOTIFICATION_RESP
    status: int = Status.ACK


@dataclass
class ProgramFlowRequest:
    msg_id: int = MsgId.PROGRAM_FLOW_REQ
    action: int = ProgramAction.START
    slot: int = 0

    def to_bytes(self) -> bytes:
        return _u8(self.msg_id) + _u8(self.action) + _u8(self.slot)


@dataclass
class ProgramFlowResponse:
    msg_id: int = MsgId.PROGRAM_FLOW_RESP
    status: int = Status.ACK


@dataclass
class ProgramFlowNotification:
    msg_id: int = MsgId.PROGRAM_FLOW_NOTIFICATION
    timestamp: int = 0


@dataclass
class ConsoleNotification:
    msg_id: int = MsgId.CONSOLE_NOTIFICATION
    text: str = ""

    def to_bytes(self) -> bytes:
        # null-terminated UTF-8 string, max 255 chars
        return _u8(self.msg_id) + self.text.encode("utf-8")[:255] + b"\x00"


@dataclass
class SetHubNameRequest:
    msg_id: int = MsgId.SET_HUB_NAME_REQ
    name: str = ""

    def to_bytes(self) -> bytes:
        return _u8(self.msg_id) + _str_nul(self.name)


@dataclass
class SetHubNameResponse:
    msg_id: int = MsgId.SET_HUB_NAME_RESP
    status: int = Status.ACK


@dataclass
class GetHubNameRequest:
    msg_id: int = MsgId.GET_HUB_NAME_REQ

    def to_bytes(self) -> bytes:
        return _u8(self.msg_id)


@dataclass
class GetHubNameResponse:
    msg_id: int = MsgId.GET_HUB_NAME_RESP
    name: str = ""


@dataclass
class DeviceUuidRequest:
    msg_id: int = MsgId.DEVICE_UUID_REQ

    def to_bytes(self) -> bytes:
        return _u8(self.msg_id)


@dataclass
class DeviceUuidResponse:
    msg_id: int = MsgId.DEVICE_UUID_RESP
    uuid: str = ""


@dataclass
class StartFileUploadRequest:
    msg_id: int = MsgId.START_FILE_UPLOAD_REQ
    filename: str = ""
    slot: int = 0
    file_crc: int = 0

    def to_bytes(self) -> bytes:
        return (
            _u8(self.msg_id)
            + _str_nul(self.filename)
            + _u8(self.slot)
            + _u32(self.file_crc)
        )


@dataclass
class StartFileUploadResponse:
    msg_id: int = MsgId.START_FILE_UPLOAD_RESP
    status: int = Status.ACK


@dataclass
class TransferChunkRequest:
    msg_id: int = MsgId.TRANSFER_CHUNK_REQ
    running_crc: int = 0
    chunk_data: bytes = b""

    def to_bytes(self) -> bytes:
        return _u8(self.msg_id) + _u32(self.running_crc) + self.chunk_data


@dataclass
class TransferChunkResponse:
    msg_id: int = MsgId.TRANSFER_CHUNK_RESP
    status: int = Status.ACK


@dataclass
class TunnelMessage:
    msg_id: int = MsgId.TUNNEL_MESSAGE
    data: bytes = b""

    def to_bytes(self) -> bytes:
        return _u8(self.msg_id) + _u16(len(self.data)) + self.data


@dataclass
class ClearSlotRequest:
    msg_id: int = MsgId.CLEAR_SLOT_REQ
    slot: int = 0

    def to_bytes(self) -> bytes:
        return _u8(self.msg_id) + _u8(self.slot)


@dataclass
class ClearSlotResponse:
    msg_id: int = MsgId.CLEAR_SLOT_RESP
    status: int = Status.ACK


@dataclass
class MoveSlotRequest:
    """Move a program from one slot to another."""
    msg_id: int = MsgId.MOVE_SLOT_REQ
    slot_from: int = 0
    slot_to: int = 0

    def to_bytes(self) -> bytes:
        return _u8(self.msg_id) + _u8(self.slot_from) + _u8(self.slot_to)


@dataclass
class MoveSlotResponse:
    msg_id: int = MsgId.MOVE_SLOT_RESP
    status: int = Status.ACK


@dataclass
class ListPathRequest:
    msg_id: int = MsgId.LIST_PATH_REQ
    path: str = ""
    slot: int = 0

    def to_bytes(self) -> bytes:
        # Path is NUL-terminated (max 31 bytes) + slot number
        path_bytes = self.path.encode("utf-8")[:31] + b"\x00"
        return _u8(self.msg_id) + path_bytes + _u8(self.slot)


@dataclass
class ListPathResponse:
    msg_id: int = MsgId.LIST_PATH_RESP
    items: list = field(default_factory=list)


@dataclass
class DeletePathRequest:
    msg_id: int = MsgId.DELETE_PATH_REQ
    path: str = ""
    slot: int = 0

    def to_bytes(self) -> bytes:
        path_bytes = self.path.encode("utf-8")[:31] + b"\x00"
        return _u8(self.msg_id) + path_bytes + _u8(self.slot)


@dataclass
class DeletePathResponse:
    msg_id: int = MsgId.DELETE_PATH_RESP
    status: int = Status.ACK


@dataclass
class StartFileDownloadRequest:
    """Request to download a file from the hub."""
    msg_id: int = MsgId.START_FILE_DOWNLOAD_REQ
    filename: str = ""
    slot: int = 0

    def to_bytes(self) -> bytes:
        fn_bytes = self.filename.encode("utf-8")[:31] + b"\x00"
        return _u8(self.msg_id) + fn_bytes + _u8(self.slot)


@dataclass
class StartFileDownloadResponse:
    msg_id: int = MsgId.START_FILE_DOWNLOAD_RESP
    status: int = Status.ACK
    file_crc: int = 0


# ── Device notification sub-types ───────────────────────────────────

@dataclass
class InfoHubNotif:
    """Battery/hub status. Size: 2 bytes (sub_id + u8 battery_level)."""
    sub_id: int = NotifSubId.INFO_HUB
    battery_level: int = 0


@dataclass
class ImuHubNotif:
    """IMU data. Size: 21 bytes (sub_id + 2×u8 + 9×i16)."""
    sub_id: int = NotifSubId.IMU_HUB
    orientation: int = 0
    yaw_face: int = 0
    yaw: int = 0
    pitch: int = 0
    roll: int = 0
    accel_x: int = 0
    accel_y: int = 0
    accel_z: int = 0
    gyro_x: int = 0
    gyro_y: int = 0
    gyro_z: int = 0


@dataclass
class MatrixHubNotif:
    """5×5 LED matrix state on the hub. Size: 26 bytes (sub_id + 25 pixels).

    Each pixel is a brightness value (0-100). Row-major, top-left first.
    """
    sub_id: int = NotifSubId.MATRIX_HUB
    image: list = field(default_factory=lambda: [0] * 25)

    def get_pixel(self, x: int, y: int) -> int:
        """Get brightness at (x, y) where (0,0) is top-left."""
        return self.image[y * 5 + x]


@dataclass
class MotorNotif:
    """Motor port data. Size: 12 bytes."""
    sub_id: int = NotifSubId.MOTOR
    port: int = 0
    device_id: int = 0
    absolute_pos: int = 0
    power: int = 0
    speed: int = 0
    position: int = 0


@dataclass
class ForceSensorNotif:
    """Force sensor data. Size: 4 bytes."""
    sub_id: int = NotifSubId.FORCE_SENSOR
    port: int = 0
    force: int = 0
    touch: int = 0


@dataclass
class ColorSensorNotif:
    """Color sensor data. Size: 10 bytes."""
    sub_id: int = NotifSubId.COLOR_SENSOR
    port: int = 0
    color: int = Color.NONE
    reflection: int = 0
    raw_red: int = 0
    raw_green: int = 0
    raw_blue: int = 0


@dataclass
class DistanceSensorNotif:
    """Distance sensor data. Size: 4 bytes."""
    sub_id: int = NotifSubId.DISTANCE_SENSOR
    port: int = 0
    distance: int = 0


@dataclass
class ColorMatrixNotif:
    """3×3 color matrix sensor (SPIKE Essential). Size: 11 bytes (sub_id + port + 9 pixels)."""
    sub_id: int = NotifSubId.COLOR_MATRIX
    port: int = 0
    image: list = field(default_factory=lambda: [0] * 9)


@dataclass
class DeviceNotification:
    msg_id: int = MsgId.DEVICE_NOTIFICATION
    notifications: list = field(default_factory=list)


# ── Serialization (message → bytes) ────────────────────────────────

def encode_message(msg) -> bytes:
    """Encode an Atlantis message to raw bytes (before COBS framing)."""
    if hasattr(msg, "to_bytes"):
        return msg.to_bytes()
    raise TypeError(f"Cannot encode message type: {type(msg).__name__}")


# ── Deserialization (bytes → message) ───────────────────────────────

def _read_nul_str(data: bytes, offset: int) -> tuple[str, int]:
    """Read NUL-terminated UTF-8 string, return (string, next_offset)."""
    end = data.index(0, offset)
    return data[offset:end].decode("utf-8", errors="replace"), end + 1


def _decode_sub_notification(data: bytes, offset: int):
    """Decode one device notification sub-type, return (notif, bytes_consumed).

    Known sub-notification sizes (verified against real hub data):
      INFO_HUB(0)=2, IMU_HUB(1)=21, MATRIX_HUB(2)=26,
      MOTOR(10)=12, FORCE_SENSOR(11)=4, COLOR_SENSOR(12)=10,
      DISTANCE_SENSOR(13)=4, COLOR_MATRIX(14)=11
    """
    if offset >= len(data):
        return None, 0
    sub_id = data[offset]

    if sub_id == NotifSubId.INFO_HUB:
        return InfoHubNotif(battery_level=data[offset + 1]), 2

    elif sub_id == NotifSubId.IMU_HUB:
        o = offset + 1
        orientation = data[o]; o += 1
        yaw_face = data[o]; o += 1
        yaw, pitch, roll = struct.unpack_from("<hhh", data, o); o += 6
        ax, ay, az = struct.unpack_from("<hhh", data, o); o += 6
        gx, gy, gz = struct.unpack_from("<hhh", data, o); o += 6
        return ImuHubNotif(
            orientation=orientation, yaw_face=yaw_face,
            yaw=yaw, pitch=pitch, roll=roll,
            accel_x=ax, accel_y=ay, accel_z=az,
            gyro_x=gx, gyro_y=gy, gyro_z=gz,
        ), 21  # 1 + 2 + 18

    elif sub_id == NotifSubId.MATRIX_HUB:
        # 5×5 LED matrix: sub_id(1) + 25 brightness bytes
        image = list(data[offset + 1:offset + 26])
        return MatrixHubNotif(image=image), 26

    elif sub_id == NotifSubId.MOTOR:
        o = offset + 1
        port = data[o]; o += 1
        device_id = data[o]; o += 1
        abs_pos, = struct.unpack_from("<h", data, o); o += 2
        power, = struct.unpack_from("<h", data, o); o += 2
        speed, = struct.unpack_from("<b", data, o); o += 1
        position, = struct.unpack_from("<i", data, o); o += 4
        return MotorNotif(
            port=port, device_id=device_id,
            absolute_pos=abs_pos, power=power,
            speed=speed, position=position,
        ), 12

    elif sub_id == NotifSubId.FORCE_SENSOR:
        o = offset + 1
        return ForceSensorNotif(
            port=data[o], force=data[o + 1], touch=data[o + 2]
        ), 4

    elif sub_id == NotifSubId.COLOR_SENSOR:
        o = offset + 1
        port = data[o]; o += 1
        color, = struct.unpack_from("<b", data, o); o += 1
        reflection = data[o]; o += 1
        rr, rg, rb = struct.unpack_from("<HHH", data, o); o += 6
        return ColorSensorNotif(
            port=port, color=color, reflection=reflection,
            raw_red=rr, raw_green=rg, raw_blue=rb,
        ), 10

    elif sub_id == NotifSubId.DISTANCE_SENSOR:
        o = offset + 1
        port = data[o]; o += 1
        dist, = struct.unpack_from("<h", data, o)
        return DistanceSensorNotif(port=port, distance=dist), 4

    elif sub_id == NotifSubId.COLOR_MATRIX:
        # 3×3 color matrix: sub_id(1) + port(1) + 9 pixel bytes
        o = offset + 1
        port = data[o]; o += 1
        image = list(data[o:o + 9])
        return ColorMatrixNotif(port=port, image=image), 11

    else:
        # Unknown sub-notification — skip 1 byte and try to resync
        logger.warning(f"Unknown sub_id={sub_id} at offset {offset}, skipping 1 byte")
        return None, 1


def decode_message(data: bytes):
    """Decode raw Atlantis message bytes (after COBS decode) into a message object.

    Args:
        data: Raw message bytes where data[0] is the message ID.

    Returns:
        Decoded message dataclass instance.

    Raises:
        ValueError: If message ID is unknown or data is too short.
    """
    if not data:
        raise ValueError("Empty message data")

    msg_id = data[0]
    payload = data[1:]

    if msg_id == MsgId.INFO_RESP:
        if len(payload) < 16:
            raise ValueError(f"InfoResponse too short: {len(payload)} bytes")
        vals = struct.unpack_from("<BBH BBH HHH H", payload, 0)
        return InfoResponse(
            rpc_major=vals[0], rpc_minor=vals[1], rpc_build=vals[2],
            fw_major=vals[3], fw_minor=vals[4], fw_build=vals[5],
            max_packet_size=vals[6], max_message_size=vals[7],
            max_chunk_size=vals[8], product_group_device=vals[9],
        )

    elif msg_id == MsgId.DEVICE_NOTIFICATION_RESP:
        status = payload[0] if payload else Status.ACK
        return DeviceNotificationResponse(status=status)

    elif msg_id == MsgId.PROGRAM_FLOW_RESP:
        status = payload[0] if payload else Status.ACK
        return ProgramFlowResponse(status=status)

    elif msg_id == MsgId.PROGRAM_FLOW_NOTIFICATION:
        ts = struct.unpack_from("<I", payload, 0)[0] if len(payload) >= 4 else 0
        return ProgramFlowNotification(timestamp=ts)

    elif msg_id == MsgId.CONSOLE_NOTIFICATION:
        text = payload.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
        return ConsoleNotification(text=text)

    elif msg_id == MsgId.GET_HUB_NAME_RESP:
        name, _ = _read_nul_str(payload + b"\x00", 0)
        return GetHubNameResponse(name=name)

    elif msg_id == MsgId.SET_HUB_NAME_RESP:
        return SetHubNameResponse(status=payload[0] if payload else Status.ACK)

    elif msg_id == MsgId.DEVICE_UUID_RESP:
        uuid_str, _ = _read_nul_str(payload + b"\x00", 0)
        return DeviceUuidResponse(uuid=uuid_str)

    elif msg_id == MsgId.START_FILE_UPLOAD_RESP:
        return StartFileUploadResponse(status=payload[0] if payload else Status.ACK)

    elif msg_id == MsgId.TRANSFER_CHUNK_RESP:
        return TransferChunkResponse(status=payload[0] if payload else Status.ACK)

    elif msg_id == MsgId.CLEAR_SLOT_RESP:
        return ClearSlotResponse(status=payload[0] if payload else Status.ACK)

    elif msg_id == MsgId.MOVE_SLOT_RESP:
        return MoveSlotResponse(status=payload[0] if payload else Status.ACK)

    elif msg_id == MsgId.DELETE_PATH_RESP:
        return DeletePathResponse(status=payload[0] if payload else Status.ACK)

    elif msg_id == MsgId.LIST_PATH_RESP:
        # Format: u16(total_len) + NUL-terminated strings
        if len(payload) < 2:
            return ListPathResponse()
        total_len = struct.unpack_from("<H", payload, 0)[0]
        data = payload[2:2 + total_len]
        items = []
        if data:
            # Split by NUL bytes, filter empty
            parts = data.split(b"\x00")
            items = [p.decode("utf-8", errors="replace") for p in parts if p]
        return ListPathResponse(items=items)

    elif msg_id == MsgId.START_FILE_DOWNLOAD_RESP:
        status = payload[0] if payload else Status.ACK
        file_crc = struct.unpack_from("<I", payload, 1)[0] if len(payload) >= 5 else 0
        return StartFileDownloadResponse(status=status, file_crc=file_crc)

    elif msg_id == MsgId.DEVICE_NOTIFICATION:
        if len(payload) < 2:
            return DeviceNotification()
        total_len = struct.unpack_from("<H", payload, 0)[0]
        notifs = []
        offset = 2
        end = min(2 + total_len, len(payload))
        while offset < end:
            sub, consumed = _decode_sub_notification(payload, offset)
            if sub is not None:
                notifs.append(sub)
            offset += consumed
        return DeviceNotification(notifications=notifs)

    elif msg_id == MsgId.TUNNEL_MESSAGE:
        data_len = struct.unpack_from("<H", payload, 0)[0] if len(payload) >= 2 else 0
        tunnel_data = payload[2:2 + data_len] if len(payload) >= 2 + data_len else payload[2:]
        return TunnelMessage(data=tunnel_data)

    elif msg_id == MsgId.INFO_REQ:
        return InfoRequest()

    else:
        # Return raw for unknown message types
        @dataclass
        class RawMessage:
            msg_id: int = msg_id
            payload: bytes = payload
        return RawMessage()
