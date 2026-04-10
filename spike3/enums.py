"""Protocol constants and enumerations for SPIKE 3 communication.

All values extracted from SPIKE App 3 v3.6.0 deobfuscated JavaScript
and .NET DLL metadata analysis.
"""

from enum import IntEnum, Enum


# ── USB identification ──────────────────────────────────────────────

LEGO_VENDOR_ID = 0x0694  # 1684 decimal


class ProductId(IntEnum):
    FLIPPER = 9        # SPIKE Prime (Atlantis firmware)
    FLIPPER_MSD = 10   # SPIKE Prime (MSD/DFU mode)
    GECKO = 13         # SPIKE Essential (normal)
    GECKO_MSD = 14     # SPIKE Essential (MSD/DFU mode)
    MINDSTORMS = 16    # Robot Inventor


# ── Connection types ────────────────────────────────────────────────

class ConnectionType(str, Enum):
    USB = "usb"
    BTC = "bluetooth-classic"
    BLE = "bluetooth-lowenergy"
    HID = "usb-hid"
    VIRTUAL = "virtual"


# ── Hub types ───────────────────────────────────────────────────────

class HubType(str, Enum):
    FLIPPER = "flipper"              # SPIKE Prime, Atlantis FW
    FLIPPER_BLE = "flipper-ble"      # SPIKE Essential, LWP3 FW (legacy)
    FLIPPER_PT = "flipper-pt"        # SPIKE Prime, MicroPython FW (legacy)
    GECKO_ATLANTIS = "gecko-atlantis" # SPIKE Essential, Atlantis FW


class ProductGroupDevice(IntEnum):
    SPIKE_PRIME = 0
    SPIKE_ESSENTIAL = 1
    SPIKE_PRIME_H5 = 2


# ── BLE UUIDs ───────────────────────────────────────────────────────

# Atlantis BLE service (Flipper / GeckoAtlantis)
ATLANTIS_SERVICE_UUID = "0000fd02-0000-1000-8000-00805f9b34fb"
ATLANTIS_TX_CHAR_UUID = "0000fd02-0001-1000-8000-00805f9b34fb"  # host→hub
ATLANTIS_RX_CHAR_UUID = "0000fd02-0002-1000-8000-00805f9b34fb"  # hub→host
ATLANTIS_DESIRED_MTU = 512

# LWP3 BLE service (Gecko legacy)
LWP3_SERVICE_UUID = "00001623-1212-efde-1623-785feabcd123"
LWP3_CHAR_UUID = "00001624-1212-efde-1623-785feabcd123"  # bidirectional


# ── Atlantis message IDs ────────────────────────────────────────────

class MsgId(IntEnum):
    INFO_REQ = 0
    INFO_RESP = 1
    START_FW_UPLOAD_REQ = 10
    START_FW_UPLOAD_RESP = 11
    START_FILE_UPLOAD_REQ = 12
    START_FILE_UPLOAD_RESP = 13
    START_FILE_DOWNLOAD_REQ = 14
    START_FILE_DOWNLOAD_RESP = 15
    TRANSFER_CHUNK_REQ = 16
    TRANSFER_CHUNK_RESP = 17
    BEGIN_FW_UPDATE_REQ = 20
    BEGIN_FW_UPDATE_RESP = 21
    SET_HUB_NAME_REQ = 22
    SET_HUB_NAME_RESP = 23
    GET_HUB_NAME_REQ = 24
    GET_HUB_NAME_RESP = 25
    DEVICE_UUID_REQ = 26
    DEVICE_UUID_RESP = 27
    PROGRAM_FLOW_REQ = 30
    PROGRAM_FLOW_RESP = 31
    PROGRAM_FLOW_NOTIFICATION = 32
    CONSOLE_NOTIFICATION = 33
    DEVICE_NOTIFICATION_REQ = 40
    DEVICE_NOTIFICATION_RESP = 41
    TUNNEL_MESSAGE = 50
    DEVICE_NOTIFICATION = 60
    CLEAR_SLOT_REQ = 70
    CLEAR_SLOT_RESP = 71
    MOVE_SLOT_REQ = 72
    MOVE_SLOT_RESP = 73
    LIST_PATH_REQ = 74
    LIST_PATH_RESP = 75
    DELETE_PATH_REQ = 76
    DELETE_PATH_RESP = 77


# Response → Request mapping
RESPONSE_TO_REQUEST = {
    1: 0, 11: 10, 13: 12, 15: 14, 17: 16, 21: 20,
    23: 22, 25: 24, 27: 26, 31: 30, 41: 40,
    71: 70, 73: 72, 75: 74, 77: 76,
}


class Status(IntEnum):
    ACK = 0
    NACK = 1


class ProgramAction(IntEnum):
    START = 0
    STOP = 1


# ── Atlantis device notification sub-types ──────────────────────────

class NotifSubId(IntEnum):
    INFO_HUB = 0           # battery level
    IMU_HUB = 1            # orientation, accel, gyro
    MATRIX_HUB = 2         # 5×5 LED matrix
    MOTOR = 10             # motor port data
    FORCE_SENSOR = 11      # force sensor
    COLOR_SENSOR = 12      # color, reflection, RGB
    DISTANCE_SENSOR = 13   # distance
    COLOR_MATRIX = 14      # 3×3 color matrix


class Port(IntEnum):
    A = 0
    B = 1
    C = 2
    D = 3
    E = 4
    F = 5


class Orientation(IntEnum):
    TOP = 0
    FRONT = 1
    RIGHT = 2
    BOTTOM = 3
    BACK = 4
    LEFT = 5


class Color(IntEnum):
    NONE = -1
    BLACK = 0
    MAGENTA = 1
    PURPLE = 2
    BLUE = 3
    AZURE = 4
    TURQUOISE = 5
    GREEN = 6
    YELLOW = 7
    ORANGE = 8
    RED = 9
    WHITE = 10


class DeviceType(IntEnum):
    """LPF2 device type IDs (appear in MotorNotification.device_id)."""
    MOTOR_MEDIUM = 48       # SPIKE Prime Medium Motor (0x30)
    MOTOR_LARGE = 49        # SPIKE Prime Large Motor  (0x31)
    ACCELERATION = 57
    GYRO = 58
    ORIENTATION_SENSOR = 59
    COLOR_SENSOR = 61       # SPIKE Prime Color Sensor
    DISTANCE_SENSOR = 62    # SPIKE Prime Ultrasonic Sensor
    FORCE_SENSOR = 63       # SPIKE Prime Force Sensor
    MOTOR_SMALL = 65        # SPIKE Essential Small Motor
    MOTOR_MEDIUM_GREY = 75  # Stone-grey Medium Motor
    MOTOR_LARGE_GREY = 76   # Stone-grey Large Motor


class Gesture(IntEnum):
    """IMU gesture IDs returned by hub.motion_sensor.get_gesture()."""
    NONE = 0
    SHAKE = 1
    FREEFALL = 2
    TAPPED = 3
    DOUBLE_TAPPED = 4


# ── MicroPython JSON-RPC notification IDs ───────────────────────────

class MPNotification(IntEnum):
    SENSOR_DATA = 0
    STORAGE_STATUS = 1
    BATTERY_STATUS = 2
    BUTTON_EVENT = 3
    GESTURE_STATUS = 4
    DISPLAY_STATUS = 5
    FIRMWARE_STATUS = 6
    STACK_START = 7
    STACK_STOP = 8
    INFO_STATUS = 9
    ERROR = 10
    VM_STATE = 11
    PROGRAM_RUNNING = 12
    LINEGRAPH_TIMER_RESET = 13
    ORIENTATION_STATUS = 14
    HUB_SIGNAL = 15


# ── COBS framing constants ──────────────────────────────────────────

COBS_XOR_KEY = 3
COBS_MAX_RUN = 83
COBS_CODE_BASE = 3
COBS_IMPLICIT_ZERO_STRIDE = 2
COBS_OVERFLOW_CODE = 255
COBS_PLACEHOLDER = 1000
COBS_HIGH_PRIORITY = 1
COBS_END_FRAME = 2

# ── Simple serial framing constants ─────────────────────────────────

FRAME_DELIMITER = 0x7E
FRAME_ESCAPE = 0x7D
FRAME_XOR_MASK = 0x20
FRAME_SPECIAL_BYTES = {0x7D, 0x7E, 0x03, 0x04, 0x20}

# ── Firmware ────────────────────────────────────────────────────────

FIRMWARE_VERSION = "1.8.149"
FIRMWARE_SHA = "0c40f8431f84a950bb2143d09e700e854c4427cc"

# ── Timeouts ────────────────────────────────────────────────────────

DEFAULT_TIMEOUT = 20.0   # seconds for request/response
SETUP_TIMEOUT = 5.0      # seconds for setup handshake (JS uses 2s + 1 retry = 4s)
POST_OPEN_DELAY = 0.5    # seconds to wait after serial port open before sending
