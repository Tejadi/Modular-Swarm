"""Base-station override state for the Jetson mission layer.

Consumes the action commands the nRF forwards over serial (and that also arrive
on olympus/command/{eui}): SET_WAYPOINT, SET_MISSION, OVERRIDE, CLEAR_OVERRIDE.
While active, an override preempts the decentralized mission policy; it expires
after its ttl (or is sticky until CLEAR_OVERRIDE), after which decentralized
behavior resumes on its own.
"""

from __future__ import annotations

import time

import swarm_proto as sp


class Override:
    def __init__(self) -> None:
        self._goal_ll: "tuple | None" = None   # (lat, lon)
        self._mission: "int | None" = None      # swarm MissionType
        self._set_at = 0.0
        self._ttl = 0.0
        self._sticky = False                     # OVERRIDE / ttl=0 -> until cleared

    def apply_cmd(self, op: int, params: bytes) -> bool:
        """Update from a decoded Cmd (op, params). Returns True if it was an
        override-related op."""
        now = time.monotonic()
        if op in (sp.CmdOp.SET_WAYPOINT, sp.CmdOp.OVERRIDE):
            wp = sp.unpack_waypoint(params)
            self._goal_ll = (wp["lat"], wp["lon"])
            self._mission = wp["mission_type"]
            self._set_at = now
            self._ttl = wp["ttl_s"]
            self._sticky = (op == sp.CmdOp.OVERRIDE) or wp["ttl_s"] == 0
            return True
        if op == sp.CmdOp.SET_MISSION:
            m = sp.unpack_mission(params)
            self._mission = m["mission_type"]
            self._set_at = now
            self._ttl = m["ttl_s"]
            self._sticky = m["ttl_s"] == 0
            return True
        if op == sp.CmdOp.CLEAR_OVERRIDE:
            self.clear()
            return True
        return False

    def active(self) -> bool:
        if self._goal_ll is None and self._mission is None:
            return False
        if self._sticky:
            return True
        return (time.monotonic() - self._set_at) < self._ttl

    def goal_ll(self) -> "tuple | None":
        return self._goal_ll if self.active() else None

    def mission(self) -> "int | None":
        return self._mission if self.active() else None

    def clear(self) -> None:
        self._goal_ll = None
        self._mission = None
        self._sticky = False
