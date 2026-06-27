"""End-to-end autonomy glue: operator command -> mesh, and the Jetson agent's
override -> mission -> status loop.

    python3 sim/test_integration.py
"""

from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "proto"))
sys.path.insert(0, os.path.join(HERE, "..", "olympus_link"))
sys.path.insert(0, os.path.join(HERE, "..", "jetson_agent"))

import swarm_proto as sp        # noqa: E402
from config import Config       # noqa: E402

LAT0, LON0 = 37.0, -122.0
EUI = bytes(range(8))
EUI_HEX = EUI.hex()


# --- swarm-link: Olympus command -> swarm downlink ---

def test_command_translation():
    from commands import translate
    op, params, bcast = translate("GO_TO", {"latitude": 37.5, "longitude": -122.5, "altitude": 30})
    assert op == sp.CmdOp.SET_WAYPOINT and not bcast
    wp = sp.unpack_waypoint(params)
    assert abs(wp["lat"] - 37.5) < 1e-6 and wp["mission_type"] == sp.MissionType.GOTO
    assert translate("RETURN_TO_LAUNCH", {})[0] == sp.CmdOp.SET_MISSION
    assert translate("IDENTIFY", {})[0] == sp.CmdOp.IDENTIFY
    op, _, bcast = translate("EMERGENCY_STOP", {})
    assert bcast and op == sp.CmdOp.SET_MISSION          # fleet hold
    assert translate("NOT_A_COMMAND", {})[0] is None


def test_service_downlink_framing():
    from service import SwarmLinkService

    class FakeSerial:
        def __init__(self): self.sent = []
        def write_payload(self, p): self.sent.append(p)
        def close(self): pass

    cfg = Config(); cfg.sink = "dryrun"
    svc = SwarmLinkService(cfg)
    svc.serial = FakeSerial()

    svc.send_command(EUI_HEX, sp.CmdOp.SET_WAYPOINT, sp.pack_waypoint(37.5, -122.5, 0.0))
    cmd = sp.decode(svc.serial.sent[-1])
    assert isinstance(cmd, sp.Cmd) and cmd.op == sp.CmdOp.SET_WAYPOINT
    assert sp.eui_str(cmd.eui) == EUI_HEX and (cmd.flags & sp.Flags.GATEWAY)

    svc.send_broadcast(sp.CmdOp.SET_MISSION, sp.pack_mission(sp.MissionType.LOITER))
    bc = sp.decode(svc.serial.sent[-1])
    assert isinstance(bc, sp.Broadcast) and (bc.flags & sp.Flags.LEADER)
    assert sp.unpack_mission(bc.params)["mission_type"] == sp.MissionType.LOITER


# --- Jetson agent: forwarded command -> override -> mission status ---

def _make_agent():
    from agent import JetsonAgent
    cfg = Config(); cfg.sink = "dryrun"
    agent = JetsonAgent(cfg, "/dev/null")
    agent.vio = None
    agent.detections = None
    agent._ekf_last = time.monotonic() - 1.0
    return agent


def _own_telemetry():
    return sp.Telemetry(
        eui=EUI, status=sp.Status.SCANNING, pos_source=sp.PosSource.FUSED,
        lat=LAT0, lon=LON0, alt=0.0, heading=90.0, pos_quality=230,
        has_kinematics=True, vel_n=0.0, vel_e=0.0, pos_std=2.0, hdg_std=3.0,
        ekf_flags=sp.EkfFlag.GPS_USED | sp.EkfFlag.IMU_USED | sp.EkfFlag.CONVERGED,
        flags=0).encode()


def test_agent_override_drives_mission():
    agent = _make_agent()
    # the nRF's own fused telemetry establishes this module + anchors the EKF
    agent._on_payload(_own_telemetry())
    assert agent.own_eui == EUI_HEX
    m = agent.reg.modules[EUI_HEX]

    # no override yet -> coverage at home, not overridden
    agent._fuse_jetson_ekf(m)
    agent._run_mission(m)
    assert not agent.last_mission.get("overridden")

    # leader forwards a waypoint due north -> override active -> transit north
    cmd = sp.Cmd(eui=EUI, op=sp.CmdOp.SET_WAYPOINT,
                 params=sp.pack_waypoint(LAT0 + 0.001, LON0, 0.0, sp.MissionType.GOTO, 5, 0),
                 flags=int(sp.Flags.GATEWAY))
    agent._on_payload(cmd.encode())
    assert agent.override.active()

    agent._fuse_jetson_ekf(m)
    agent._run_mission(m)
    assert agent.last_mission["overridden"], agent.last_mission
    assert m.status == sp.Status.TRANSITING, m.status
    assert agent.last_mission["velocity"][0] > 0.3, agent.last_mission   # heading north


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
