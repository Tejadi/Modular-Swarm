"""Override parsing + decentralized mission FSM precedence.

    python3 sim/test_mission.py
"""

from __future__ import annotations

import math
import os
import sys
import time

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "proto"))
sys.path.insert(0, os.path.join(HERE, "..", "jetson_agent"))

import swarm_proto as sp       # noqa: E402
from ekf import JetsonEKF      # noqa: E402
from override import Override  # noqa: E402
from mission import MissionPlanner, MissionMode  # noqa: E402

HOME = (37.0, -122.0)


def _anchored_ekf(north_m: float = 0.0, east_m: float = 0.0) -> JetsonEKF:
    e = JetsonEKF()
    e.update_nrf(HOME[0], HOME[1], 90.0, 0.0, 0.0, 2.0, 3.0)  # anchor at home
    e.x[0] += north_m
    e.x[1] += east_m
    return e


def test_override_parse_and_clear():
    ov = Override()
    ov.apply_cmd(sp.CmdOp.SET_WAYPOINT,
                 sp.pack_waypoint(37.5, -122.5, 30.0, sp.MissionType.GOTO, 5, 0))
    assert ov.active()
    lat, lon = ov.goal_ll()
    assert abs(lat - 37.5) < 1e-6 and abs(lon + 122.5) < 1e-6
    assert ov.mission() == sp.MissionType.GOTO
    ov.apply_cmd(sp.CmdOp.CLEAR_OVERRIDE, b"")
    assert not ov.active() and ov.goal_ll() is None


def test_override_ttl_expiry():
    ov = Override()
    ov.apply_cmd(sp.CmdOp.SET_MISSION, sp.pack_mission(sp.MissionType.SEARCH, 1))
    assert ov.active()
    ov._set_at = time.monotonic() - 2.0   # simulate 2 s elapsed, ttl was 1 s
    assert not ov.active()
    # A sticky override (ttl=0) never expires on its own.
    ov.apply_cmd(sp.CmdOp.OVERRIDE,
                 sp.pack_waypoint(37.1, -122.0, 0.0, sp.MissionType.GOTO, 0, 0))
    ov._set_at = time.monotonic() - 9999.0
    assert ov.active()


def test_mission_override_preempts():
    ekf = _anchored_ekf()
    planner = MissionPlanner(home_ll=HOME, default_mode=MissionMode.COVERAGE)
    planner.set_goal(*HOME)                      # default: sit at home
    ov = Override()

    out = planner.step(ekf, [], ov)
    assert not out["overridden"]

    # Override to a waypoint due north -> transit north.
    ov.apply_cmd(sp.CmdOp.OVERRIDE,
                 sp.pack_waypoint(37.001, -122.0, 0.0, sp.MissionType.GOTO, 0, 0))
    out = planner.step(ekf, [], ov)
    assert out["overridden"], out
    assert out["status"] == sp.Status.TRANSITING, out
    assert out["velocity"][0] > 0.5, out          # heading north (positive ENU north)


def test_rtl_returns_home():
    ekf = _anchored_ekf(north_m=60.0)            # 60 m north of home
    planner = MissionPlanner(home_ll=HOME, default_mode=MissionMode.RTL)
    out = planner.step(ekf, [], Override())
    assert out["status"] == sp.Status.RETURNING, out
    assert out["velocity"][0] < -0.5, out         # heading south, back toward home


def test_emergency_outranks_override():
    # Boxed in by neighbors on all sides -> no safe velocity -> EMERGENCY even
    # though an override is active.
    ekf = _anchored_ekf()
    planner = MissionPlanner(home_ll=HOME, default_mode=MissionMode.GOTO)
    ov = Override()
    ov.apply_cmd(sp.CmdOp.OVERRIDE,
                 sp.pack_waypoint(37.01, -122.0, 0.0, sp.MissionType.GOTO, 0, 0))
    # Neighbors right on top of us (1 m << agent radius) -> collision -> EMERGENCY.
    ring = [((1.0 * math.cos(t), 1.0 * math.sin(t)), (0.0, 0.0), 3.0)
            for t in [i * math.pi / 4 for i in range(8)]]
    out = planner.step(ekf, ring, ov)
    assert out["status"] == sp.Status.EMERGENCY, out


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
