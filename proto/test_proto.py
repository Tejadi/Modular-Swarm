"""Wire-format guard for proto/swarm_proto.py (mirror of swarm_protocol.h).

Locks the byte layout so the C firmware encoders (swarm_node) must match exactly,
and proves the append-only trailers stay backward compatible. Pure stdlib.

    python3 proto/test_proto.py        # prints PASS/FAIL, exits non-zero on failure
    pytest proto/test_proto.py         # also works (test_* functions)

These checks are self-contained on the Python side. The known-byte vectors below
are the contract a C harness (or the real firmware) is asserted against — change a
field and a vector breaks, which is the point.
"""

from __future__ import annotations

import struct
import sys

import swarm_proto as sp

EUI = bytes(range(8))                 # 00 01 02 03 04 05 06 07
EUI2 = bytes([0xaa] * 8)


# --- framing primitives ----------------------------------------------------

def test_crc16_known_answer():
    # CRC16/CCITT-FALSE("123456789") == 0x29B1 (the canonical check value).
    assert sp.crc16(b"123456789") == 0x29B1


def test_cobs_roundtrip():
    cases = [
        b"",
        b"\x00",
        b"\x00\x00\x00",
        b"hello",
        b"\x01\x02\x00\x03",
        bytes(range(256)),
        b"\x11" * 600,                # forces a 0xFF code-block split
        b"\x00" * 300,
    ]
    for c in cases:
        assert sp.cobs_decode(sp.cobs_encode(c)) == c, c


def test_serial_frame_roundtrip():
    payload = sp.Telemetry(eui=EUI, lat=1.0, lon=2.0).encode()
    frame = sp.frame_serial(payload)
    assert frame.endswith(b"\x00")
    assert 0 not in frame[:-1]        # COBS body is 0x00-free
    reader = sp.SerialReader()
    got = reader.feed(frame)
    assert got == [payload]


def test_serial_reader_resync_on_garbage():
    payload = sp.Hello(eui=EUI, role=1, mount=1, sensors=3).encode()
    good = sp.frame_serial(payload)
    reader = sp.SerialReader()
    # leading junk + a corrupt frame, then a good frame
    out = reader.feed(b"\xff\xff\x00" + b"\x02\x99\x00" + good)
    assert out == [payload]


# --- header layout ---------------------------------------------------------

def test_header_known_bytes():
    hdr = sp.Header(msg_type=sp.MsgType.TELEMETRY, eui=EUI, seq=1, flags=0).pack()
    assert hdr == bytes([0x53, 0x01, 0x02, 0x00]) + EUI + struct.pack("<I", 1)
    assert len(hdr) == sp.HDR_LEN == 16


def test_header_version_forward_compat():
    raw = bytearray(sp.Header(sp.MsgType.HELLO, EUI).pack())
    raw[1] = sp.VERSION                 # current version OK
    sp.Header.unpack(bytes(raw))
    raw[1] = sp.VERSION + 1             # newer version rejected
    try:
        sp.Header.unpack(bytes(raw))
        assert False, "newer version should raise"
    except ValueError:
        pass


# --- idempotent round-trips (float-quantization safe) ----------------------

def _idempotent(msg):
    payload = msg.encode()
    again = sp.decode(payload)
    assert again is not None, type(msg).__name__
    assert again.encode() == payload, type(msg).__name__
    return again


def test_roundtrip_all_messages():
    msgs = [
        sp.Hello(eui=EUI, role=sp.Role.PROVIDER | sp.Role.RELAY, mount=1,
                 sensors=sp.Sensor.GPS | sp.Sensor.IMU, name="agent-3",
                 attached_to="vehicle_01",
                 capabilities=sp.Capability.AUTONOMOUS | sp.Capability.OVERRIDABLE),
        sp.Telemetry(eui=EUI, status=sp.Status.SCANNING, pos_source=sp.PosSource.FUSED,
                     lat=37.1234567, lon=-122.1234567, alt=12.34, heading=90.0,
                     pos_quality=220,
                     readings=[sp.Reading(sp.Channel.PRESSURE, 1013.2),
                               sp.Reading(sp.Channel.VEL_N, 1.5)],
                     has_kinematics=True, vel_n=1.5, vel_e=-0.25,
                     pos_std=2.5, hdg_std=3.0,
                     ekf_flags=sp.EkfFlag.GPS_USED | sp.EkfFlag.IMU_USED | sp.EkfFlag.CONVERGED),
        sp.Neighbors(eui=EUI, links=[sp.NeighborLink(EUI2, -75, 4200, 180)]),
        sp.RangeReq(eui=EUI, target=EUI2, t1_us=123456),
        sp.RangeResp(eui=EUI, initiator=EUI2, t1_us=1, t2_us=2, t3_us=3),
        sp.Route(eui=EUI, primary_parent=EUI2, subscriptions=[EUI2]),
        sp.Cmd(eui=EUI, op=sp.CmdOp.SET_WAYPOINT,
               params=sp.pack_waypoint(37.5, -122.5, 30.0, sp.MissionType.GOTO, 5, 60)),
        sp.Broadcast(eui=EUI, op=sp.CmdOp.OVERRIDE, flags=sp.Flags.LEADER | sp.Flags.OVERRIDE,
                     params=sp.pack_waypoint(1.0, 2.0, 3.0, sp.MissionType.SEARCH, 9, 0)),
        sp.PoseInject(eui=EUI, lat=37.1, lon=-122.1, alt=10.0, heading=45.0,
                      vel_n=1.0, vel_e=2.0, pos_std=1.2, hdg_std=2.0,
                      src_flags=sp.EkfFlag.VIO_USED | sp.EkfFlag.CONVERGED, ts_ms=123456),
        sp.MeshSend(eui=EUI, payload=sp.Telemetry(eui=EUI2, lat=1.0, lon=1.0).encode()),
    ]
    for m in msgs:
        again = _idempotent(m)
        assert type(again) is type(m)


def test_dispatch_types():
    assert isinstance(sp.decode(sp.PoseInject(eui=EUI).encode()), sp.PoseInject)
    assert isinstance(sp.decode(sp.Broadcast(eui=EUI).encode()), sp.Broadcast)
    assert isinstance(sp.decode(sp.MeshSend(eui=EUI, payload=b"x").encode()), sp.MeshSend)
    # Unknown message type decodes to None (forward compatible).
    raw = bytearray(sp.Header(sp.MsgType.HELLO, EUI).pack())
    raw[2] = 0x7e
    assert sp.decode(bytes(raw)) is None


# --- trailer sizes + back-compat -------------------------------------------

def test_telemetry_trailer_size_and_optional():
    base = sp.Telemetry(eui=EUI, lat=1.0, lon=2.0)
    kin = sp.Telemetry(eui=EUI, lat=1.0, lon=2.0, has_kinematics=True,
                       vel_n=1.0, vel_e=2.0, pos_std=1.0, hdg_std=1.0,
                       ekf_flags=sp.EkfFlag.GPS_USED)
    assert len(kin.encode()) - len(base.encode()) == 9    # fused trailer is 9 B
    # No-trailer telemetry decodes with has_kinematics False.
    d0 = sp.decode(base.encode())
    assert d0.has_kinematics is False and d0.vel_n == 0.0
    d1 = sp.decode(kin.encode())
    assert d1.has_kinematics is True and abs(d1.vel_n - 1.0) < 1e-6


def test_hello_caps_trailer_backcompat():
    h = sp.Hello(eui=EUI, role=1, mount=1, sensors=3, name="n", attached_to="v",
                 capabilities=sp.Capability.OVERRIDABLE)
    payload = h.encode()
    # Strip the 2-byte caps trailer => an "old firmware" HELLO.
    old = payload[:-2]
    hdr = sp.Header.unpack(old)
    d = sp.Hello.decode(hdr, old[sp.HDR_LEN:])
    assert d.capabilities == 0 and d.name == "n" and d.attached_to == "v"
    # Full payload keeps the capability.
    assert sp.decode(payload).capabilities == int(sp.Capability.OVERRIDABLE)


# --- param codecs ----------------------------------------------------------

def test_param_codecs():
    wp = sp.pack_waypoint(37.123, -122.456, 25.0, sp.MissionType.PATROL, 7, 120)
    assert len(wp) == 16
    u = sp.unpack_waypoint(wp)
    assert abs(u["lat"] - 37.123) < 1e-6 and u["mission_type"] == sp.MissionType.PATROL
    assert u["priority"] == 7 and u["ttl_s"] == 120

    m = sp.pack_mission(sp.MissionType.EXPLORE, 300)
    assert sp.unpack_mission(m) == {"mission_type": 0, "ttl_s": 300}

    p = sp.pack_permissions(sp.Capability.AUTONOMOUS | sp.Capability.OVERRIDABLE)
    assert sp.unpack_permissions(p) == int(sp.Capability.AUTONOMOUS | sp.Capability.OVERRIDABLE)


def test_pose_inject_body_size():
    body = sp.PoseInject(eui=EUI).encode()[sp.HDR_LEN:]
    assert len(body) == 27


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  FAIL {fn.__name__}: {e!r}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run())
