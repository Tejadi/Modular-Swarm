"""Operator command downlink: Olympus -> swarm mesh.

Subscribes to the Olympus command key (`{prefix}/command/**`, which vehicle-api
publishes when an operator/dashboard issues a command) and translates each
Olympus command into a swarm downlink — a unicast Cmd to one module, or a fleet
Broadcast. This is the missing half of the loop: telemetry already flows up;
this carries commands back down through the leader nRF onto the mesh.

Uses a native zenoh subscriber (the swarm-link image installs eclipse-zenoh). If
the zenoh lib is unavailable it logs once and no-ops — telemetry still works, you
just can't command from the dashboard until zenoh is present.
"""

from __future__ import annotations

import json
import logging

import swarm_proto as sp

log = logging.getLogger("olympus_link.commands")

# Fleet targets (the {id} segment of the command key) routed as a Broadcast.
_FLEET = {"broadcast", "all", "fleet", "*"}


def translate(command: str, params: dict):
    """Map an Olympus command -> (swarm CmdOp, params_bytes, broadcast?).

    Returns (None, None, False) for commands with no swarm mapping."""
    c = (command or "").upper()
    p = params or {}
    lat = p.get("latitude", p.get("lat"))
    lon = p.get("longitude", p.get("lon"))
    alt = p.get("altitude", p.get("alt", 0.0)) or 0.0
    pri = int(p.get("priority", 5))
    ttl = int(p.get("ttl_s", 0))

    if c == "GO_TO" and lat is not None and lon is not None:
        return sp.CmdOp.SET_WAYPOINT, sp.pack_waypoint(lat, lon, alt, sp.MissionType.GOTO, pri, ttl), False
    if c in ("RETURN_TO_LAUNCH", "RTL"):
        return sp.CmdOp.SET_MISSION, sp.pack_mission(sp.MissionType.RTL, ttl), False
    if c == "START_SCAN":
        return sp.CmdOp.SET_MISSION, sp.pack_mission(sp.MissionType.SEARCH, ttl), False
    if c in ("PAUSE", "LOITER"):
        return sp.CmdOp.SET_MISSION, sp.pack_mission(sp.MissionType.LOITER, ttl), False
    if c == "RESUME":
        return sp.CmdOp.SET_MISSION, sp.pack_mission(sp.MissionType.COVERAGE, ttl), False
    if c == "IDENTIFY":
        return sp.CmdOp.IDENTIFY, b"", False
    if c == "CLEAR_OVERRIDE":
        return sp.CmdOp.CLEAR_OVERRIDE, b"", False
    if c in ("EMERGENCY_STOP", "ABORT_ALL"):
        return sp.CmdOp.SET_MISSION, sp.pack_mission(sp.MissionType.LOITER, 0), True   # fleet hold
    return None, None, False


class CommandListener:
    def __init__(self, cfg, service, loop) -> None:
        self.cfg = cfg
        self.service = service
        self.loop = loop
        self._sub = None
        self._own_session = None

    def start(self) -> None:
        try:
            import zenoh
        except Exception as e:  # pragma: no cover - optional dep
            log.warning("command downlink disabled (no zenoh lib: %s)", e)
            return
        sess = getattr(self.service.olympus, "_zenoh_session", None)
        if sess is None:
            try:
                sess = zenoh.open(zenoh.Config())
                self._own_session = sess
            except Exception as e:  # pragma: no cover
                log.warning("command downlink: zenoh.open failed: %s", e)
                return
        key = f"{self.cfg.key_prefix.rstrip('/')}/command/**"
        try:
            self._sub = sess.declare_subscriber(key, self._on_sample)
            log.info("command downlink: subscribed %s", key)
        except Exception as e:  # pragma: no cover
            log.warning("command downlink subscribe failed: %s", e)

    def _on_sample(self, sample) -> None:  # pragma: no cover - needs a live zenoh
        try:
            key = str(sample.key_expr)
            payload = getattr(sample, "payload", None)
            raw = bytes(payload) if payload is not None else bytes(sample.value.payload)
            obj = json.loads(raw.decode("utf-8"))
        except Exception as e:
            log.debug("bad command sample: %s", e)
            return
        target = key.rstrip("/").rsplit("/", 1)[-1]
        op, params, bcast = translate(obj.get("command", ""), obj.get("params", {}))
        if op is None:
            log.debug("no swarm mapping for command %r", obj.get("command"))
            return
        self.loop.call_soon_threadsafe(self._dispatch, target, int(op), params, bcast)

    def _dispatch(self, target: str, op: int, params: bytes, bcast: bool) -> None:
        if bcast or target.lower() in _FLEET:
            self.service.send_broadcast(op, params)
        else:
            self.service.send_command(target, op, params)

    def stop(self) -> None:
        for fn in (lambda: self._sub and self._sub.undeclare(),
                   lambda: self._own_session and self._own_session.close()):
            try:
                fn()
            except Exception:
                pass
