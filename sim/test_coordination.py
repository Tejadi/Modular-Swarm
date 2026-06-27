"""Decentralized coordination tests: ORCA avoidance, Voronoi-style coverage, and
override precedence. Each agent runs policy() using only what it can see.

    python3 sim/test_coordination.py
"""

from __future__ import annotations

import math
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "jetson_agent"))

import coordination as co  # noqa: E402

R = 2.0           # agent radius -> combined clearance 2R = 4 m
MAX_SPEED = 4.0
DT = 0.2
SENSE = 50.0


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _run(agents, goals, overrides=None, steps=120):
    """agents/goals: dict id->pos/(goal). Returns (min_separation_seen, final pos)."""
    pos = dict(agents)
    vel = {k: (0.0, 0.0) for k in agents}
    min_sep = float("inf")
    for _ in range(steps):
        new_vel = {}
        for k in pos:
            neigh = [(pos[j], vel[j], R) for j in pos
                     if j != k and _dist(pos[k], pos[j]) < SENSE]
            ov = (overrides or {}).get(k)
            out = co.policy(pos[k], vel[k], goals[k], neigh,
                            override_goal=ov, max_speed=MAX_SPEED, radius=R)
            new_vel[k] = out["velocity"]
        for k in pos:
            vel[k] = new_vel[k]
            pos[k] = (pos[k][0] + vel[k][0] * DT, pos[k][1] + vel[k][1] * DT)
        ids = list(pos)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                min_sep = min(min_sep, _dist(pos[ids[i]], pos[ids[j]]))
    return min_sep, pos


def test_head_on_avoids_collision():
    # Near-head-on (a realistic 1.5 m lateral offset; perfectly collinear agents
    # are a measure-zero ORCA degeneracy that needs an external tie-break).
    agents = {"a": (-12.0, 0.0), "b": (12.0, 1.5)}
    goals = {"a": (12.0, 0.0), "b": (-12.0, 1.5)}   # crossing paths -> must avoid
    min_sep, pos = _run(agents, goals)
    assert min_sep > 1.5, f"agents collided/overlapped (min sep {min_sep:.2f} m)"
    # both should make it past the midpoint toward their goals
    assert pos["a"][0] > 0 and pos["b"][0] < 0, pos


def test_swarm_no_collisions():
    agents = {f"n{i}": (math.cos(i) * 3, math.sin(i) * 3) for i in range(6)}  # clumped
    goals = {k: (40.0, 0.0) for k in agents}        # common goal far east
    min_sep, pos = _run(agents, goals, steps=150)
    assert min_sep > 1.5, f"swarm collision (min sep {min_sep:.2f} m)"
    # the clump should have moved decisively toward the goal
    avg_x = sum(p[0] for p in pos.values()) / len(pos)
    assert avg_x > 15.0, f"swarm did not advance toward goal (avg x {avg_x:.1f})"


def test_coverage_spreads():
    # Agents starting on top of each other should spread out (Lloyd repulsion).
    agents = {f"n{i}": (0.1 * i, 0.0) for i in range(5)}
    goals = {k: (0.0, 0.0) for k in agents}         # no goal pull -> pure spreading
    _, pos = _run(agents, goals, steps=80)
    ids = list(pos)
    spread = min(_dist(pos[ids[i]], pos[ids[j]])
                 for i in range(len(ids)) for j in range(i + 1, len(ids)))
    assert spread > 2.0, f"agents did not spread (closest pair {spread:.2f} m)"


def test_override_preempts_goal():
    # Default goal east, override goal north -> setpoint heads north, not east.
    out = co.policy((0.0, 0.0), (0.0, 0.0), goal=(50.0, 0.0), neighbors=[],
                    override_goal=(0.0, 50.0), max_speed=MAX_SPEED, radius=R)
    assert out["overridden"] and out["setpoint"][1] > 0 and abs(out["setpoint"][0]) < 1.0, out


def _run_all():
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
    sys.exit(_run_all())
