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
URI_BROADCAST = "swm/bc"

EUI64_NONE = b"\xff" * EUI64_LEN

# A Jetson POSE_INJECT is treated as authoritative only while this fresh (ms).
POSE_FRESH_MS = 2000


class MsgType(IntEnum):
    HELLO = 0x01
    TELEMETRY = 0x02
    NEIGHBORS = 0x03
    RANGE_REQ = 0x04
    RANGE_RESP = 0x05
    POSE_INJECT = 0x06  # Jetson -> nRF (serial): authoritative fused pose
    MESH_SEND = 0x07    # Jetson -> nRF (serial): opaque frame to relay
    ROUTE = 0x10
    CMD = 0x11
    BROADCAST = 0x12    # leader -> all (multicast): command / override


class Flags(IntFlag):
    NONE = 0
    GATEWAY = 1 << 0
    RELAYED = 1 << 1
    LEADER = 1 << 2     # sender is the base-station leader
    OVERRIDE = 1 << 3   # command overrides decentralized policy


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
    VIO = 1 << 8       # Jetson visual-inertial odometry present


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
    VEL_N = 0x16
    VEL_E = 0x17
    VEL_D = 0x18
    POS_VAR = 0x19
    VEL_VAR = 0x1A
    HDG_VAR = 0x1B
    MAG_HDG = 0x1C
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
    SET_WAYPOINT = 7
    SET_MISSION = 8
    OVERRIDE = 9
    CLEAR_OVERRIDE = 10
    SET_PERMISSIONS = 11


class MissionType(IntEnum):
    EXPLORE = 0
    SEARCH = 1
    COVERAGE = 2
    PATROL = 3
    GOTO = 4
    LOITER = 5
    RTL = 6


class Capability(IntFlag):
    NONE = 0
    OVERRIDABLE = 1 << 0   # leader may override this node
    AUTONOMOUS = 1 << 1    # runs decentralized mission policy
    PASSIVE_RX = 1 << 2    # receive-only swarm member
    BEACON_TX = 1 << 3     # passive beacon / waypoint station
    RELAY_ONLY = 1 << 4    # forwards traffic only


class EkfFlag(IntFlag):
    NONE = 0
    GPS_USED = 1 << 0
    IMU_USED = 1 << 1
    VIO_USED = 1 << 2      # a Jetson POSE_INJECT was adopted
    PEER_USED = 1 << 3
    CONVERGED = 1 << 4


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
    Sensor.VIO: "vio",
}


def sensor_list(bitmap: int) -> list[str]:
    """Expand a sensor bitmap into the manifest of present sensor names."""
    return [name for bit, name in SENSOR_NAMES.items() if bitmap & bit]


CAPABILITY_NAMES = {
    Capability.OVERRIDABLE: "overridable",
    Capability.AUTONOMOUS: "autonomous",
    Capability.PASSIVE_RX: "passive_rx",
    Capability.BEACON_TX: "beacon_tx",
    Capability.RELAY_ONLY: "relay_only",
}


def capability_list(bitmap: int) -> list[str]:
    """Expand a capability bitmask into permission names for the dashboard."""
    return [name for bit, name in CAPABILITY_NAMES.items() if bitmap & bit]


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
        # Append-only forward compatibility: accept this version and older. A
        # newer field is always a trailer an older decoder simply ignores.
        if ver > VERSION:
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
    capabilities: int = 0   # enum Capability bitmask (trailer)
    seq: int = 0
    flags: int = 0

    def encode(self) -> bytes:
        body = struct.pack("<BBHHBI", self.role, self.mount, self.sensors,
                           self.fw_version, self.battery_pct, self.uptime_s)
        body += _pack_str(self.name, 24)
        body += _pack_str(self.attached_to, 32)
        body += struct.pack("<H", self.capabilities)
        return Header(MsgType.HELLO, self.eui, self.seq, self.flags).pack() + body

    @classmethod
    def decode(cls, hdr: Header, body: bytes) -> "Hello":
        role, mount, sensors, fw, batt, uptime = struct.unpack("<BBHHBI", body[:11])
        off = 11
        name, off = _read_str(body, off)
        attached, off = _read_str(body, off)
        # Capabilities trailer is optional (older firmware omits it).
        caps = 0
        if off + 2 <= len(body):
            (caps,) = struct.unpack("<H", body[off:off + 2])
            off += 2
        return cls(eui=hdr.eui, role=role, mount=mount, sensors=sensors,
                   fw_version=fw, battery_pct=batt, uptime_s=uptime,
                   name=name, attached_to=attached, capabilities=caps,
                   seq=hdr.seq, flags=hdr.flags)


@dataclass
class Reading:
    channel: int
    value: float


def _clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v


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
    # Fused-kinematics trailer (EKF output). Present iff has_kinematics.
    has_kinematics: bool = False
    vel_n: float = 0.0        # m/s, north
    vel_e: float = 0.0        # m/s, east
    pos_std: float = 0.0      # m, 1-sigma horizontal position std
    hdg_std: float = 0.0      # deg, 1-sigma heading std
    ekf_flags: int = 0        # enum EkfFlag
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
        if self.has_kinematics:
            body += struct.pack(
                "<hhHHB",
                _clamp(int(round(self.vel_n * 100)), -32768, 32767),
                _clamp(int(round(self.vel_e * 100)), -32768, 32767),
                _clamp(int(round(self.pos_std * 100)), 0, 65535),
                _clamp(int(round(self.hdg_std * 100)), 0, 65535),
                self.ekf_flags & 0xFF)
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
        # Optional fused-kinematics trailer (9 bytes: hhHHB).
        kin = dict(has_kinematics=False, vel_n=0.0, vel_e=0.0,
                   pos_std=0.0, hdg_std=0.0, ekf_flags=0)
        if off + 9 <= len(body):
            vn, ve, ps, hs, ef = struct.unpack("<hhHHB", body[off:off + 9])
            kin = dict(has_kinematics=True, vel_n=vn / 100.0, vel_e=ve / 100.0,
                       pos_std=ps / 100.0, hdg_std=hs / 100.0, ekf_flags=ef)
            off += 9
        return cls(eui=hdr.eui, status=status, pos_source=src,
                   lat=lat_e7 / 1e7, lon=lon_e7 / 1e7, alt=alt_cm / 100.0,
                   heading=hdg / 100.0, battery_pct=batt, pos_quality=q,
                   readings=readings, seq=hdr.seq, flags=hdr.flags, **kin)


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


@dataclass
class Broadcast:
    """Leader -> all (multicast). Same op/params shape as Cmd."""
    eui: bytes          # leader's eui
    op: int = CmdOp.NOOP
    params: bytes = b""
    seq: int = 0
    flags: int = 0

    def encode(self) -> bytes:
        body = bytes([self.op, len(self.params)]) + self.params
        return Header(MsgType.BROADCAST, self.eui, self.seq, self.flags).pack() + body

    @classmethod
    def decode(cls, hdr: Header, body: bytes) -> "Broadcast":
        op = body[0]
        plen = body[1]
        params = body[2:2 + plen]
        return cls(eui=hdr.eui, op=op, params=params, seq=hdr.seq, flags=hdr.flags)


@dataclass
class PoseInject:
    """Jetson -> nRF (serial): authoritative fused pose the nRF adopts+broadcasts."""
    eui: bytes
    lat: float = 0.0
    lon: float = 0.0
    alt: float = 0.0
    heading: float = 0.0    # degrees
    vel_n: float = 0.0      # m/s
    vel_e: float = 0.0      # m/s
    pos_std: float = 0.0    # m
    hdg_std: float = 0.0    # deg
    src_flags: int = 0      # enum EkfFlag
    ts_ms: int = 0
    seq: int = 0
    flags: int = 0

    def encode(self) -> bytes:
        body = struct.pack(
            "<iiiHhhHHBI",
            int(round(self.lat * 1e7)), int(round(self.lon * 1e7)),
            int(round(self.alt * 100)), int(round(self.heading * 100)) % 36000,
            _clamp(int(round(self.vel_n * 100)), -32768, 32767),
            _clamp(int(round(self.vel_e * 100)), -32768, 32767),
            _clamp(int(round(self.pos_std * 100)), 0, 65535),
            _clamp(int(round(self.hdg_std * 100)), 0, 65535),
            self.src_flags & 0xFF, self.ts_ms & 0xFFFFFFFF)
        return Header(MsgType.POSE_INJECT, self.eui, self.seq, self.flags).pack() + body

    @classmethod
    def decode(cls, hdr: Header, body: bytes) -> "PoseInject":
        (lat_e7, lon_e7, alt_cm, hdg, vn, ve, ps, hs, sf, ts) = struct.unpack(
            "<iiiHhhHHBI", body[:27])
        return cls(eui=hdr.eui, lat=lat_e7 / 1e7, lon=lon_e7 / 1e7,
                   alt=alt_cm / 100.0, heading=hdg / 100.0,
                   vel_n=vn / 100.0, vel_e=ve / 100.0, pos_std=ps / 100.0,
                   hdg_std=hs / 100.0, src_flags=sf, ts_ms=ts,
                   seq=hdr.seq, flags=hdr.flags)


@dataclass
class MeshSend:
    """Jetson -> nRF (serial): an opaque swarm frame the nRF relays onto the mesh."""
    eui: bytes
    payload: bytes = b""    # a complete swarm_proto frame (header+body)
    seq: int = 0
    flags: int = 0

    def encode(self) -> bytes:
        return Header(MsgType.MESH_SEND, self.eui, self.seq, self.flags).pack() + self.payload

    @classmethod
    def decode(cls, hdr: Header, body: bytes) -> "MeshSend":
        return cls(eui=hdr.eui, payload=bytes(body), seq=hdr.seq, flags=hdr.flags)


# --- Command param codecs (SET_WAYPOINT / OVERRIDE / SET_MISSION / SET_PERMISSIONS) ---

def pack_waypoint(lat: float, lon: float, alt: float,
                  mission_type: int = MissionType.GOTO,
                  priority: int = 0, ttl_s: int = 0) -> bytes:
    return struct.pack("<iiiBBH", int(round(lat * 1e7)), int(round(lon * 1e7)),
                       int(round(alt * 100)), mission_type & 0xFF,
                       priority & 0xFF, ttl_s & 0xFFFF)


def unpack_waypoint(params: bytes) -> dict:
    lat_e7, lon_e7, alt_cm, mt, pr, ttl = struct.unpack("<iiiBBH", params[:16])
    return dict(lat=lat_e7 / 1e7, lon=lon_e7 / 1e7, alt=alt_cm / 100.0,
                mission_type=mt, priority=pr, ttl_s=ttl)


def pack_mission(mission_type: int, ttl_s: int = 0) -> bytes:
    return struct.pack("<BH", mission_type & 0xFF, ttl_s & 0xFFFF)


def unpack_mission(params: bytes) -> dict:
    mt, ttl = struct.unpack("<BH", params[:3])
    return dict(mission_type=mt, ttl_s=ttl)


def pack_permissions(capabilities: int) -> bytes:
    return struct.pack("<H", capabilities & 0xFFFF)


def unpack_permissions(params: bytes) -> int:
    return struct.unpack("<H", params[:2])[0]


_DECODERS = {
    MsgType.HELLO: Hello.decode,
    MsgType.TELEMETRY: Telemetry.decode,
    MsgType.NEIGHBORS: Neighbors.decode,
    MsgType.RANGE_REQ: RangeReq.decode,
    MsgType.RANGE_RESP: RangeResp.decode,
    MsgType.POSE_INJECT: PoseInject.decode,
    MsgType.MESH_SEND: MeshSend.decode,
    MsgType.ROUTE: Route.decode,
    MsgType.CMD: Cmd.decode,
    MsgType.BROADCAST: Broadcast.decode,
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
