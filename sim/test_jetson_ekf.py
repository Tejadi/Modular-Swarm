"""Tests for the Jetson fusion EKF, the 50 m peer gate, and the agent wiring.

    python3 sim/test_jetson_ekf.py
"""

from __future__ import annotations

import math
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "proto"))
sys.path.insert(0, os.path.join(HERE, "..", "olympus_link"))
sys.path.insert(0, os.path.join(HERE, "..", "jetson_agent"))

from ekf import JetsonEKF, MPD_LAT, DEG2RAD  # noqa: E402
from peers import gate_50m, PEER_GATE_M       # noqa: E402

LAT0, LON0 = 37.0, -122.0
MPD_LON = MPD_LAT * math.cos(LAT0 * DEG2RAD)


def _enu_to_ll(pN, pE):
    return LAT0 + pN / MPD_LAT, LON0 + pE / MPD_LON


def _err_m(lat, lon, tlat, tlon):
    return math.hypot((lat - tlat) * MPD_LAT, (lon - tlon) * MPD_LON)


def test_tracks_nrf_pose():
    """Following a clean nRF pose stream, the Jetson EKF should track it closely."""
    ekf = JetsonEKF()
    truth_pE = 0.0
    for k in range(20):
        truth_pE += 2.0  # 2 m/s east, 1 Hz
        if k > 0:
            ekf.predict(1.0)
        tlat, tlon = _enu_to_ll(0.0, truth_pE)
        ekf.update_nrf(tlat, tlon, heading_deg=90.0, vel_n=0.0, vel_e=2.0,
                       pos_std=2.0, hdg_std=3.0)
    fix = ekf.get_fix()
    tlat, tlon = _enu_to_ll(0.0, truth_pE)
    assert _err_m(fix["lat"], fix["lon"], tlat, tlon) < 2.0, fix
    assert abs(fix["vel_e"] - 2.0) < 0.4 and abs(fix["vel_n"]) < 0.4, fix
    assert abs(((fix["heading"] - 90 + 180) % 360) - 180) < 8.0, fix


def test_vio_dead_reckons_without_nrf():
    """With no fresh nRF fix, VIO motion still advances the position."""
    ekf = JetsonEKF()
    ekf.update_nrf(LAT0, LON0, 90.0, 0.0, 0.0, 2.0, 3.0)   # anchor, heading east
    start = ekf.get_fix()
    for _ in range(6):
        ekf.predict(1.0)
        # body forward 2 m, heading east -> +2 m east
        ekf.update_vio(d_north=0.0, d_east=2.0, d_yaw=0.0, dt=1.0)
    end = ekf.get_fix()
    moved = _err_m(end["lat"], end["lon"], start["lat"], start["lon"])
    assert moved > 6.0, f"VIO should have advanced position, moved {moved:.1f} m"
    assert abs(end["vel_e"] - 2.0) < 0.5, end


def test_50m_gate():
    neighbors = {"aa": 3000, "bb": 8000, "cc": -1, "dd": 1500}   # cm
    positions = {"aa": (37.0, -122.0), "bb": (37.0, -122.0),
                 "cc": (37.0, -122.0)}  # dd has no position
    usable = gate_50m(neighbors, positions)
    keep = {u["eui"] for u in usable}
    assert keep == {"aa"}, keep          # bb too far, cc no range, dd no position
    assert all(u["range_m"] <= PEER_GATE_M for u in usable)


def test_peer_range_pulls_position():
    """A range constraint to a known peer reduces the range residual."""
    ekf = JetsonEKF()
    ekf.update_nrf(*_enu_to_ll(0.0, 0.0), 90.0, 0.0, 0.0, 30.0, 10.0)  # weak fix at origin
    peer_pN, peer_pE = 0.0, 20.0          # peer 20 m east
    before = abs(math.hypot(ekf.x[0] - peer_pN, ekf.x[1] - peer_pE) - 12.0)
    for _ in range(5):
        ekf.update_range(peer_pN, peer_pE, range_m=12.0, std_m=2.0)
    after = abs(math.hypot(ekf.x[0] - peer_pN, ekf.x[1] - peer_pE) - 12.0)
    assert after < before, f"residual not reduced: {before:.2f} -> {after:.2f}"


def test_agent_module_imports():
    """The agent wiring (ekf/peers/vio integration) imports without error."""
    import agent
    assert hasattr(agent, "JetsonAgent")
    from vio import make_vio
    v = make_vio("none")                  # forced NullVio, no camera needed
    assert v.poll() is None


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
