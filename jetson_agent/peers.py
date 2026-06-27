"""50 m peer gate for the Jetson fusion EKF.

A vehicle's nRF reports neighbor ranges (coarse RTT/RSSI). When a peer's position
is also known — from peer telemetry the nRF forwards over serial, or from Olympus
over IP — a (range, peer-position) pair within 50 m becomes a range constraint for
the Jetson EKF. Beyond 50 m a peer adds negligible information and costs CPU, so it
is dropped; that single gate is the whole point of this module.
"""

from __future__ import annotations

PEER_GATE_M = 50.0


def gate_50m(neighbors: dict, peer_positions: dict) -> list[dict]:
    """Return the usable in-range peers as dicts {eui, range_m, lat, lon}.

    neighbors: eui -> range in cm, or any object exposing `.range_cm`.
    peer_positions: eui -> (lat, lon).
    """
    out: list[dict] = []
    for eui, nb in neighbors.items():
        range_cm = getattr(nb, "range_cm", nb)
        if not range_cm or range_cm <= 0:
            continue                     # unknown range — can't gate, skip
        r = range_cm / 100.0
        if r > PEER_GATE_M:
            continue                     # too far to help; drop (saves CPU)
        pos = peer_positions.get(eui)
        if pos is None:
            continue                     # no position for this peer yet
        out.append({"eui": eui, "range_m": r, "lat": pos[0], "lon": pos[1]})
    return out
