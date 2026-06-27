"""Decentralized swarm coordination — runs on each vehicle's Jetson.

Two pieces, both decentralized (each agent uses only its own pose, mission goal,
and the neighbors it can see within the 50 m gate):

  * coverage()  — Lloyd/Voronoi-style spreading: pull toward the mission goal /
                  unexplored direction, push off crowding neighbors, so a team
                  spreads to cover an area instead of clumping.
  * orca()      — reciprocal velocity-obstacle collision avoidance: given a
                  preferred velocity and neighbor (position, velocity, radius),
                  return the nearest collision-free velocity. Ported from the
                  Olympus brain's orca.py to pure Python (no numpy) so it runs in
                  the agent with no extra deps and is unit-testable.

policy() composes them and lets a base-station override preempt the goal; if no
safe velocity exists it flags EMERGENCY (which outranks even an override).

Coordinates are local ENU meters (use JetsonEKF.to_enu). All decentralized: a
command/override is the ONLY thing that overrides the local decision.
"""

from __future__ import annotations

import math

Vec = tuple

# Avoidance tuning (mirrors olympus_brain/orca.py defaults).
DEFAULT_RADIUS = 5.0
TIME_HORIZON = 5.0
COVERAGE_REPULSION = 12.0      # m, neighbor spacing the coverage term aims for


def _sub(a, b): return (a[0] - b[0], a[1] - b[1])
def _add(a, b): return (a[0] + b[0], a[1] + b[1])
def _scale(a, s): return (a[0] * s, a[1] * s)
def _dot(a, b): return a[0] * b[0] + a[1] * b[1]
def _det(a, b): return a[0] * b[1] - a[1] * b[0]
def _norm(a): return math.hypot(a[0], a[1])


def _normalize(a):
    n = _norm(a)
    return (1.0, 0.0) if n < 1e-9 else (a[0] / n, a[1] / n)


def _clip_speed(v, max_speed):
    s = _norm(v)
    return _scale(v, max_speed / s) if s > max_speed else v


# --- ORCA reciprocal collision avoidance (pure-Python port of orca.py) ---

def orca(my_pos: Vec, preferred: Vec, neighbors: list,
         max_speed: float = 8.0, radius: float = DEFAULT_RADIUS,
         tau: float = TIME_HORIZON) -> tuple:
    """Return (safe_velocity, adjusted). neighbors: [(pos, vel, radius)]."""
    lines = []  # each (direction, point): the half-plane v must stay left of
    for npos, nvel, nrad in neighbors:
        rel_pos = _sub(npos, my_pos)
        rel_vel = _sub(preferred, nvel)
        dist = _norm(rel_pos)
        comb_r = radius + nrad
        if dist < 1e-3:
            d = (1.0, 0.0)
            lines.append((d, _scale(d, max_speed * 0.5)))
            continue
        if dist < comb_r:
            # already overlapping: push apart along the time-horizon cone
            w = _sub(rel_vel, _scale(rel_pos, 1.0 / tau))
            wl = _norm(w)
            direction = _normalize(w) if wl > 1e-3 else _normalize(rel_pos)
            u = _scale(direction, comb_r / tau - wl)
            line_dir = (-direction[1], direction[0])
            lines.append((line_dir, _add(preferred, _scale(u, 0.5))))
            continue
        # tangent legs of the velocity obstacle
        leg = math.sqrt(dist * dist - comb_r * comb_r)
        rpu = _scale(rel_pos, 1.0 / dist)
        ct, st = leg / dist, comb_r / dist
        left = (rpu[0] * ct - rpu[1] * st, rpu[0] * st + rpu[1] * ct)
        right = (rpu[0] * ct + rpu[1] * st, -rpu[0] * st + rpu[1] * ct)
        if _det(rel_pos, rel_vel) > 0:
            direction, line_dir = left, (left[1], -left[0])
        else:
            direction, line_dir = right, (-right[1], right[0])
        proj = max(0.0, _dot(rel_vel, direction))
        u = _sub(_scale(direction, proj), rel_vel)
        u = _add(u, _scale(direction, comb_r / tau))
        lines.append((line_dir, _add(preferred, _scale(u, 0.5))))

    result = preferred
    for direction, point in lines:
        if _det(direction, _sub(result, point)) < 0.0:
            t = _dot(direction, _sub(preferred, point))
            result = _clip_speed(_add(point, _scale(direction, t)), max_speed)
    result = _clip_speed(result, max_speed)
    adjusted = _norm(_sub(result, preferred)) > 0.1
    return result, adjusted


# --- Lloyd/Voronoi-style coverage spreading ---

def coverage(my_pos: Vec, goal: Vec, neighbor_positions: list,
             cruise: float = 4.0) -> Vec:
    """Preferred velocity: toward the goal, pushed off crowding neighbors."""
    to_goal = _sub(goal, my_pos)
    pref = _scale(_normalize(to_goal), cruise) if _norm(to_goal) > 1.0 else (0.0, 0.0)
    repel = (0.0, 0.0)
    for npos in neighbor_positions:
        away = _sub(my_pos, npos)
        d = _norm(away)
        if 1e-3 < d < COVERAGE_REPULSION:
            # strength grows as neighbors get closer than the target spacing
            repel = _add(repel, _scale(_normalize(away),
                                       cruise * (COVERAGE_REPULSION - d) / COVERAGE_REPULSION))
    return _clip_speed(_add(pref, repel), cruise)


# --- exploration / search frontier ---

def frontier_target(my_pos: Vec, neighbor_positions: list,
                    step_m: float = 30.0, prev_dir: "Vec | None" = None) -> Vec:
    """A coverage/search target: step toward less-covered space, i.e. away from
    the neighbor centroid. With no neighbors, keep heading in prev_dir (default
    +x). This is the decentralized 'explore'/'search' goal the mission FSM drives
    toward; Olympus's belief-map search can replace it later for the same hook."""
    direction = prev_dir or (1.0, 0.0)
    if neighbor_positions:
        cx = sum(p[0] for p in neighbor_positions) / len(neighbor_positions)
        cy = sum(p[1] for p in neighbor_positions) / len(neighbor_positions)
        away = _sub(my_pos, (cx, cy))
        if _norm(away) > 1e-3:
            direction = _normalize(away)
    return _add(my_pos, _scale(direction, step_m))


# --- composed decentralized policy ---

def policy(my_pos: Vec, my_vel: Vec, goal: Vec, neighbors: list,
           override_goal: "Vec | None" = None, max_speed: float = 8.0,
           radius: float = DEFAULT_RADIUS) -> dict:
    """Decentralized step. neighbors: [(pos, vel, radius)].

    Precedence: an active base-station override replaces the goal; the result is
    always run through ORCA, so avoidance is never bypassed. Returns the safe
    velocity, the next setpoint, and an emergency flag (no safe velocity found).
    """
    effective_goal = override_goal if override_goal is not None else goal
    npos = [n[0] for n in neighbors]
    preferred = coverage(my_pos, effective_goal, npos, cruise=min(4.0, max_speed))
    safe, adjusted = orca(my_pos, preferred, neighbors, max_speed, radius)

    # Emergency, highest priority: a neighbor's body is within our own radius (an
    # imminent/active collision avoidance has failed to prevent), or we are fully
    # hemmed in (a meaningful preferred velocity forced to ~zero).
    nearest = min((_norm(_sub(n[0], my_pos)) for n in neighbors), default=float("inf"))
    emergency = nearest < radius or (_norm(preferred) > 0.5 and _norm(safe) < 0.05)
    setpoint = _add(my_pos, _scale(safe, 1.0))   # 1 s look-ahead waypoint
    return {
        "velocity": safe,
        "setpoint": setpoint,
        "adjusted": adjusted,
        "emergency": emergency,
        "overridden": override_goal is not None,
    }
