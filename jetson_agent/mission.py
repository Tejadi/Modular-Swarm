"""Decentralized mission layer (runs on each vehicle's Jetson).

Combines the mission mode (explore / search / coverage / patrol / goto / loiter /
rtl) with the decentralized coordination policy (coordination.py) and a base
station override (override.py). It is a pure decision function: inputs (own fused
pose, neighbors, override, optional frontier goal) -> a setpoint the vehicle
controller drives toward. The nRF stays comms+sensing only.

Precedence, highest first:
  1. EMERGENCY  — coordination found no safe velocity (outranks everything)
  2. OVERRIDE   — a fresh base-station waypoint/mission
  3. decentralized mission policy (explore/search/coverage/...)

Status mirrors the swarm_status enum so it maps 1:1 onto Olympus.
"""

from __future__ import annotations

import math
from enum import IntEnum

import swarm_proto as sp
import coordination as co


class MissionMode(IntEnum):
    EXPLORE = sp.MissionType.EXPLORE
    SEARCH = sp.MissionType.SEARCH
    COVERAGE = sp.MissionType.COVERAGE
    PATROL = sp.MissionType.PATROL
    GOTO = sp.MissionType.GOTO
    LOITER = sp.MissionType.LOITER
    RTL = sp.MissionType.RTL


class MissionPlanner:
    def __init__(self, home_ll: tuple, default_mode: MissionMode = MissionMode.COVERAGE) -> None:
        self.home = home_ll
        self.mode = default_mode
        self.goal_ll = home_ll
        self.status = sp.Status.IDLE

    def set_goal(self, lat: float, lon: float) -> None:
        self.goal_ll = (lat, lon)

    def step(self, ekf, neighbors_enu: list, override,
             frontier_goal: "tuple | None" = None) -> dict:
        """ekf: JetsonEKF. neighbors_enu: [(pos_NE, vel_NE, radius)]."""
        mode = self.mode
        goal_ll = self.goal_ll

        # Override preempts mode + goal while active.
        if override.active():
            if override.mission() is not None:
                mode = MissionMode(override.mission())
            if override.goal_ll() is not None:
                goal_ll = override.goal_ll()

        # Mode-specific goal selection.
        if mode == MissionMode.RTL:
            goal_ll = self.home
        elif mode in (MissionMode.EXPLORE, MissionMode.SEARCH) and frontier_goal:
            goal_ll = frontier_goal

        fix = ekf.get_fix()
        my = ekf.to_enu(fix["lat"], fix["lon"])
        my_vel = (fix["vel_n"], fix["vel_e"])
        goal = ekf.to_enu(*goal_ll)
        ov_goal = ekf.to_enu(*override.goal_ll()) if override.goal_ll() else None

        out = co.policy(my, my_vel, goal, neighbors_enu, override_goal=ov_goal)
        self.status = self._status(mode, my, goal, out)
        out.update(mode=int(mode), status=int(self.status), goal_ll=goal_ll)
        return out

    @staticmethod
    def _status(mode: MissionMode, my: tuple, goal: tuple, out: dict) -> int:
        if out["emergency"]:
            return sp.Status.EMERGENCY                 # outranks override
        if mode == MissionMode.RTL:
            return sp.Status.RETURNING
        if math.hypot(goal[0] - my[0], goal[1] - my[1]) > 5.0:
            return sp.Status.TRANSITING
        if mode in (MissionMode.EXPLORE, MissionMode.SEARCH,
                    MissionMode.COVERAGE, MissionMode.PATROL):
            return sp.Status.SCANNING
        if mode == MissionMode.LOITER:
            return sp.Status.IDLE
        return sp.Status.EXECUTING
