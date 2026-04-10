"""spike3 — Python communication library for LEGO SPIKE 3 hubs.

Implements the Atlantis binary protocol (USB + BLE) and MicroPython JSON-RPC
protocol (USB + Bluetooth Classic) as reverse-engineered from the SPIKE App
v3.6.0 deobfuscated JavaScript and .NET DLL metadata.

Quick start::

    from spike3 import Hub

    hub = Hub.connect_usb('/dev/ttyACM0')  # or 'COM3' on Windows
    info = hub.get_info()
    print(f"Firmware: {info.fw_major}.{info.fw_minor}.{info.fw_build}")
    hub.set_notification_interval(50)
    hub.close()
"""

__version__ = "0.1.0"

from .hub import Hub
from .enums import (
    LEGO_VENDOR_ID, ProductId, ConnectionType, HubType,
    ProductGroupDevice, MsgId, Status, ProgramAction,
    NotifSubId, Port, Orientation, Color, DeviceType, Gesture,
    MPNotification,
    ATLANTIS_SERVICE_UUID, ATLANTIS_TX_CHAR_UUID, ATLANTIS_RX_CHAR_UUID,
    LWP3_SERVICE_UUID, LWP3_CHAR_UUID,
)
from .transport import UsbTransport, BleTransport
from .cobs import encode as cobs_encode, decode as cobs_decode, FrameAccumulator
from .atlantis import (
    InfoRequest, InfoResponse,
    DeviceNotificationRequest, DeviceNotificationResponse,
    ProgramFlowRequest, ProgramFlowResponse,
    ProgramFlowNotification, ConsoleNotification,
    SetHubNameRequest, SetHubNameResponse,
    GetHubNameRequest, GetHubNameResponse,
    DeviceUuidRequest, DeviceUuidResponse,
    StartFileUploadRequest, StartFileUploadResponse,
    StartFileDownloadRequest, StartFileDownloadResponse,
    TransferChunkRequest, TransferChunkResponse,
    TunnelMessage, ClearSlotRequest, ClearSlotResponse,
    MoveSlotRequest, MoveSlotResponse,
    ListPathRequest, ListPathResponse,
    DeletePathRequest, DeletePathResponse,
    DeviceNotification,
    InfoHubNotif, ImuHubNotif, MatrixHubNotif, MotorNotif,
    ForceSensorNotif, ColorSensorNotif, DistanceSensorNotif,
    ColorMatrixNotif,
    encode_message, decode_message,
)
from .micropython import (
    RpcRequest, RpcResponse, RpcError, RpcNotification,
    parse_message as mp_parse_message,
    MessageAccumulator as MpMessageAccumulator,
)
from . import tunnel

__all__ = [
    "Hub",
    # Enums
    "LEGO_VENDOR_ID", "ProductId", "ConnectionType", "HubType",
    "ProductGroupDevice", "MsgId", "Status", "ProgramAction",
    "NotifSubId", "Port", "Orientation", "Color", "DeviceType", "Gesture",
    "MPNotification",
    # UUIDs
    "ATLANTIS_SERVICE_UUID", "ATLANTIS_TX_CHAR_UUID", "ATLANTIS_RX_CHAR_UUID",
    "LWP3_SERVICE_UUID", "LWP3_CHAR_UUID",
    # Transport
    "UsbTransport", "BleTransport",
    # COBS
    "cobs_encode", "cobs_decode", "FrameAccumulator",
    # Atlantis messages
    "InfoRequest", "InfoResponse",
    "DeviceNotificationRequest", "DeviceNotificationResponse",
    "ProgramFlowRequest", "ProgramFlowResponse",
    "ProgramFlowNotification", "ConsoleNotification",
    "SetHubNameRequest", "SetHubNameResponse",
    "GetHubNameRequest", "GetHubNameResponse",
    "DeviceUuidRequest", "DeviceUuidResponse",
    "StartFileUploadRequest", "StartFileUploadResponse",
    "TransferChunkRequest", "TransferChunkResponse",
    "TunnelMessage", "ClearSlotRequest", "ClearSlotResponse",
    "MoveSlotRequest", "MoveSlotResponse",
    "StartFileDownloadRequest", "StartFileDownloadResponse",
    "ListPathRequest", "ListPathResponse",
    "DeletePathRequest", "DeletePathResponse",
    "DeviceNotification",
    "InfoHubNotif", "ImuHubNotif", "MatrixHubNotif", "MotorNotif",
    "ForceSensorNotif", "ColorSensorNotif", "DistanceSensorNotif",
    "ColorMatrixNotif",
    "encode_message", "decode_message",
    # MicroPython
    "RpcRequest", "RpcResponse", "RpcError", "RpcNotification",
    "mp_parse_message", "MpMessageAccumulator",
    # Tunnel
    "tunnel",
]
