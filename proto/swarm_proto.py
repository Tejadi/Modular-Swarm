"""Swarm overlay protocol — Python mirror of proto/swarm_protocol.h.

Single source of truth (Python side) for encoding/decoding the messages that
move between reconfigurable swarm modules, the Jetson companion, and the
command-station olympus_link service. The byte layout matches the C header
exactly; keep the two in lockstep.

On the wire the same compact little-endian payload is used on the RF mesh (as
the CoAP payload) and over USB-CDC serial. The serial link adds COBS framing
plus a CRC16/CCITT-FALSE trailer (see cobs_encode / frame_serial below).

Pure stdlib — no third-party deps — so the simulator, the Jetson agent, and
olympus_link can all import it without a build step.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum, IntFlag
from typing import Optional

# --- Constants (mirror swarm_protocol.h) ---

MAGIC = 0x53
VERSION = 0x01
HDR_LEN = 16
EUI64_LEN = 8
MAX_FRAME = 256
COAP_PORT = 5683
MCAST_ALL_NODES = "ff03::1"

URI_HELLO = "swm/hello"
URI_TELEMETRY = "swm/tlm"
URI_NEIGHBORS = "swm/nbr"
URI_RANGE = "swm/rng"
URI_ROUTE = "swm/rte"
URI_CMD = "swm/cmd"

EUI64_NONE = b"\xff" * EUI64_LEN


class MsgType(IntEnum):
    HELLO = 0x01
    TELEMETRY = 0x02
    NEIGHBORS = 0x03
    RANGE_REQ = 0x04
    RANGE_RESP = 0x05
    ROUTE = 0x10
    CMD = 0x11


class Flags(IntFlag):
    NONE = 0
    GATEWAY = 1 << 0
    RELAYED = 1 << 1


class Role(IntFlag):
    NONE = 0
    PROVIDER = 1 << 0
    CONSUMER = 1 << 1
    RELAY = 1 << 2


class Mount(IntEnum):
    STANDALONE = 0
    VEHICLE = 1


class Sensor(IntFlag):
    NONE = 0
    GPS = 1 << 0
    IMU = 1 << 1
    MAG = 1 << 2
    BARO = 1 << 3
    TEMP = 1 << 4
    HUMIDITY = 1 << 5
    RANGEFINDER = 1 << 6
    CAMERA = 1 << 7


class PosSource(IntEnum):
    NONE = 0
    GPS = 1
    RANGED = 2
    IMU = 3
    FUSED = 4


class Status(IntEnum):
    IDLE = 0
    SCANNING = 1
    TRANSITING = 2
    EXECUTING = 3
    RETURNING = 4
    CHARGING = 5
    EMERGENCY = 6
    OFFLINE = 7


class Channel(IntEnum):
    TEMP = 0x01
    HUMIDITY = 0x02
    PRESSURE = 0x03
    ACCEL_X = 0x10
    ACCEL_Y = 0x11
    ACCEL_Z = 0x12
    GYRO_X = 0x13
    GYRO_Y = 0x14
    GYRO_Z = 0x15
    RANGEFINDER = 0x20
    BATTERY_V = 0x30


class CmdOp(IntEnum):
    NOOP = 0
    SET_ROLE = 1
    IDENTIFY = 2
    SET_RATE = 3
    REBOOT = 4
    LIGHT = 5
    SET_MOUNT = 6


# Human-readable names for the dashboard sensor manifest.
SENSOR_NAMES = {
    Sensor.GPS: "gps",
    Sensor.IMU: "imu",
    Sensor.MAG: "magnetometer",
    Sensor.BARO: "barometer",
    Sensor.TEMP: "temperature",
    Sensor.HUMIDITY: "humidity",
    Sensor.RANGEFINDER: "rangefinder",
    Sensor.CAMERA: "camera",
}


def sensor_list(bitmap: int) -> list[str]:
    """Expand a sensor bitmap into the manifest of present sensor names."""
    return [name for bit, name in SENSOR_NAMES.items() if bitmap & bit]


def eui_str(eui: bytes) -> str:
    """Canonical lowercase hex module id, e.g. 'a1b2c3d4e5f60718'."""
    return eui.hex()


def eui_bytes(s: str) -> bytes:
    """Parse a hex module id back to 8 raw bytes."""
    raw = bytes.fromhex(s.replace(":", ""))
    if len(raw) != EUI64_LEN:
        raise ValueError(f"EUI64 must be 8 bytes, got {len(raw)}")
    return raw


# --- CRC16/CCITT-FALSE (poly 0x1021, init 0xFFFF) ---

def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


# --- COBS framing (Consistent Overhead Byte Stuffing) ---
# A 0x00 byte delimits frames; COBS guarantees the encoded payload is 0x00-free.

def cobs_encode(data: bytes) -> bytes:
    out = bytearray()
    code_idx = 0
    out.append(0)  # placeholder for the first code byte
    code = 1
    for byte in data:
        if byte == 0:
            out[code_idx] = code
            code_idx = len(out)
            out.append(0)
            code = 1
        else:
            out.append(byte)
            code += 1
            if code == 0xFF:
                out[code_idx] = code
                code_idx = len(out)
                out.append(0)
                code = 1
    out[code_idx] = code
    return bytes(out)


def cobs_decode(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        code = data[i]
        if code == 0:
            raise ValueError("zero code byte in COBS stream")
        i += 1
        for _ in range(code - 1):
            if i >= n:
                raise ValueError("truncated COBS frame")
            out.append(data[i])
            i += 1
        if code < 0xFF and i < n:
            out.append(0)
    return bytes(out)


def frame_serial(payload: bytes) -> bytes:
    """Wrap a protocol payload for the serial link: COBS(payload || crc16) + 0x00."""
    crc = crc16(payload)
    body = payload + struct.pack("<H", crc)
    return cobs_encode(body) + b"\x00"


def deframe_serial(frame: bytes) -> bytes:
    """Reverse frame_serial. Raises ValueError on CRC mismatch / corruption."""
    decoded = cobs_decode(frame)
    if len(decoded) < 2:
        raise ValueError("frame too short")
    payload, crc_rx = decoded[:-2], struct.unpack("<H", decoded[-2:])[0]
    if crc16(payload) != crc_rx:
        raise ValueError("CRC mismatch")
    return payload


class SerialReader:
    """Accumulates bytes from a serial stream and yields complete payloads.

    Tolerates partial reads and resyncs on the 0x00 delimiter, dropping any
    frame that fails CRC instead of corrupting the decode stream.
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        payloads: list[bytes] = []
        self._buf.extend(chunk)
        while True:
            try:
                idx = self._buf.index(0)
            except ValueError:
                break
            frame = bytes(self._buf[:idx])
            del self._buf[: idx + 1]
            if not frame:
                continue
            try:
                payloads.append(deframe_serial(frame))
            except ValueError:
                # Corrupt frame — skip it; the next delimiter resyncs us.
                continue
        return payloads


# --- Message dataclasses ---

@dataclass
class Header:
    msg_type: int
    eui: bytes
    seq: int = 0
    flags: int = 0

    def pack(self) -> bytes:
        return struct.pack("<BBBB8sI", MAGIC, VERSION, self.msg_type,
                           self.flags, self.eui, self.seq)

    @classmethod
    def unpack(cls, data: bytes) -> "Header":
        if len(data) < HDR_LEN:
            raise ValueError("short header")
        magic, ver, mtype, flags, eui, seq = struct.unpack("<BBBB8sI", data[:HDR_LEN])
        if magic != MAGIC:
            raise ValueError(f"bad magic 0x{magic:02x}")
        if ver != VERSION:
            raise ValueError(f"unsupported version {ver}")
        return cls(msg_type=mtype, eui=eui, seq=seq, flags=flags)


def _pack_str(s: str, maxlen: int = 32) -> bytes:
    raw = s.encode("utf-8")[:maxlen]
    return bytes([len(raw)]) + raw


def _read_str(data: bytes, off: int) -> tuple[str, int]:
    n = data[off]
    off += 1
    s = data[off:off + n].decode("utf-8", "replace")
    return s, off + n


@dataclass
class Hello:
    eui: bytes
    role: int
    mount: int
    sensors: int
    fw_version: int = 1
    battery_pct: int = 100
    uptime_s: int = 0
    name: str = ""
    attached_to: str = ""
    seq: int = 0
    flags: int = 0

    def encode(self) -> bytes:
        body = struct.pack("<BBHHBI", self.role, self.mount, self.sensors,
                           self.fw_version, self.battery_pct, self.uptime_s)
        body += _pack_str(self.name, 24)
        body += _pack_str(self.attached_to, 32)
        return Header(MsgType.HELLO, self.eui, self.seq, self.flags).pack() + body

    @classmethod
    def decode(cls, hdr: Header, body: bytes) -> "Hello":
        role, mount, sensors, fw, batt, uptime = struct.unpack("<BBHHBI", body[:11])
        off = 11
        name, off = _read_str(body, off)
        attached, off = _read_str(body, off)
        return cls(eui=hdr.eui, role=role, mount=mount, sensors=sensors,
                   fw_version=fw, battery_pct=batt, uptime_s=uptime,
                   name=name, attached_to=attached, seq=hdr.seq, flags=hdr.flags)


@dataclass
class Reading:
    channel: int
    value: float


@dataclass
class Telemetry:
    eui: bytes
    status: int = Status.IDLE
    pos_source: int = PosSource.NONE
    lat: float = 0.0          # decimal degrees
    lon: float = 0.0
    alt: float = 0.0          # meters
    heading: float = 0.0      # degrees
    battery_pct: int = 100
    pos_quality: int = 0      # 0..255 (255 best)
    readings: list[Reading] = field(default_factory=list)
    seq: int = 0
    flags: int = 0

    def encode(self) -> bytes:
        lat_e7 = int(round(self.lat * 1e7))
        lon_e7 = int(round(self.lon * 1e7))
        alt_cm = int(round(self.alt * 100))
        hdg_cdeg = int(round(self.heading * 100)) % 36000
        body = struct.pack("<BBiiiHBB", self.status, self.pos_source,
                           lat_e7, lon_e7, alt_cm, hdg_cdeg,
                           self.battery_pct, self.pos_quality)
        body += bytes([len(self.readings)])
        for r in self.readings:
            body += struct.pack("<Bf", r.channel, r.value)
        return Header(MsgType.TELEMETRY, self.eui, self.seq, self.flags).pack() + body

    @classmethod
    def decode(cls, hdr: Header, body: bytes) -> "Telemetry":
        status, src, lat_e7, lon_e7, alt_cm, hdg, batt, q = struct.unpack("<BBiiiHBB", body[:18])
        off = 18
        count = body[off]
        off += 1
        readings = []
        for _ in range(count):
            ch, val = struct.unpack("<Bf", body[off:off + 5])
            readings.append(Reading(ch, val))
            off += 5
        return cls(eui=hdr.eui, status=status, pos_source=src,
                   lat=lat_e7 / 1e7, lon=lon_e7 / 1e7, alt=alt_cm / 100.0,
                   heading=hdg / 100.0, battery_pct=batt, pos_quality=q,
                   readings=readings, seq=hdr.seq, flags=hdr.flags)


@dataclass
class NeighborLink:
    eui: bytes
    rssi: int          # dBm (signed)
    range_cm: int      # estimated distance, 0 = unknown
    link_quality: int  # 0..255


@dataclass
class Neighbors:
    eui: bytes
    links: list[NeighborLink] = field(default_factory=list)
    seq: int = 0
    flags: int = 0

    def encode(self) -> bytes:
        body = bytes([len(self.links)])
        for n in self.links:
            body += n.eui + struct.pack("<bHB", n.rssi, n.range_cm, n.link_quality)
        return Header(MsgType.NEIGHBORS, self.eui, self.seq, self.flags).pack() + body

    @classmethod
    def decode(cls, hdr: Header, body: bytes) -> "Neighbors":
        count = body[0]
        off = 1
        links = []
        for _ in range(count):
            eui = body[off:off + 8]
            rssi, rng, lq = struct.unpack("<bHB", body[off + 8:off + 12])
            links.append(NeighborLink(eui, rssi, rng, lq))
            off += 12
        return cls(eui=hdr.eui, links=links, seq=hdr.seq, flags=hdr.flags)


@dataclass
class RangeReq:
    eui: bytes          # initiator
    target: bytes
    t1_us: int
    seq: int = 0
    flags: int = 0

    def encode(self) -> bytes:
        body = self.target + struct.pack("<I", self.t1_us)
        return Header(MsgType.RANGE_REQ, self.eui, self.seq, self.flags).pack() + body

    @classmethod
    def decode(cls, hdr: Header, body: bytes) -> "RangeReq":
        target = body[:8]
        (t1,) = struct.unpack("<I", body[8:12])
        return cls(eui=hdr.eui, target=target, t1_us=t1, seq=hdr.seq, flags=hdr.flags)


@dataclass
class RangeResp:
    eui: bytes          # responder
    initiator: bytes
    t1_us: int
    t2_us: int
    t3_us: int
    seq: int = 0
    flags: int = 0

    def encode(self) -> bytes:
        body = self.initiator + struct.pack("<III", self.t1_us, self.t2_us, self.t3_us)
        return Header(MsgType.RANGE_RESP, self.eui, self.seq, self.flags).pack() + body

    @classmethod
    def decode(cls, hdr: Header, body: bytes) -> "RangeResp":
        initiator = body[:8]
        t1, t2, t3 = struct.unpack("<III", body[8:20])
        return cls(eui=hdr.eui, initiator=initiator, t1_us=t1, t2_us=t2,
                   t3_us=t3, seq=hdr.seq, flags=hdr.flags)


@dataclass
class Route:
    eui: bytes          # the module this route is for
    primary_parent: bytes = EUI64_NONE
    secondary_parent: bytes = EUI64_NONE
    role_override: int = 0xFF  # 0xFF = no override
    subscriptions: list[bytes] = field(default_factory=list)
    seq: int = 0
    flags: int = 0

    def encode(self) -> bytes:
        body = self.primary_parent + self.secondary_parent + bytes([self.role_override])
        body += bytes([len(self.subscriptions)])
        for s in self.subscriptions:
            body += s
        return Header(MsgType.ROUTE, self.eui, self.seq, self.flags).pack() + body

    @classmethod
    def decode(cls, hdr: Header, body: bytes) -> "Route":
        primary = body[:8]
        secondary = body[8:16]
        role_override = body[16]
        count = body[17]
        off = 18
        subs = [body[off + i * 8:off + i * 8 + 8] for i in range(count)]
        return cls(eui=hdr.eui, primary_parent=primary, secondary_parent=secondary,
                   role_override=role_override, subscriptions=subs,
                   seq=hdr.seq, flags=hdr.flags)


@dataclass
class Cmd:
    eui: bytes          # target module
    op: int = CmdOp.NOOP
    params: bytes = b""
    seq: int = 0
    flags: int = 0

    def encode(self) -> bytes:
        body = bytes([self.op, len(self.params)]) + self.params
        return Header(MsgType.CMD, self.eui, self.seq, self.flags).pack() + body

    @classmethod
    def decode(cls, hdr: Header, body: bytes) -> "Cmd":
        op = body[0]
        plen = body[1]
        params = body[2:2 + plen]
        return cls(eui=hdr.eui, op=op, params=params, seq=hdr.seq, flags=hdr.flags)


_DECODERS = {
    MsgType.HELLO: Hello.decode,
    MsgType.TELEMETRY: Telemetry.decode,
    MsgType.NEIGHBORS: Neighbors.decode,
    MsgType.RANGE_REQ: RangeReq.decode,
    MsgType.RANGE_RESP: RangeResp.decode,
    MsgType.ROUTE: Route.decode,
    MsgType.CMD: Cmd.decode,
}


def decode(payload: bytes):
    """Decode a raw protocol payload (header + body) into a message dataclass.

    Returns None for an unknown message type rather than raising, so a forward-
    compatible peer can ignore messages it does not understand.
    """
    hdr = Header.unpack(payload)
    body = payload[HDR_LEN:]
    fn = _DECODERS.get(hdr.msg_type)
    if fn is None:
        return None
    return fn(hdr, body)
