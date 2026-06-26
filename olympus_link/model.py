"""In-memory state for the swarm as seen by the command station.

The registry is the authoritative live picture: every module that has announced
itself, what sensors it carries, where it is, whether it contributes to or only
consumes from the swarm, who its neighbors are, and the route the autorouter has
assigned it. The autorouter and the Olympus push layer both read from here.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import swarm_proto as sp


@dataclass
class Position:
    lat: float = 0.0
    lon: float = 0.0
    alt: float = 0.0
    heading: float = 0.0
    source: int = sp.PosSource.NONE  # how it was derived
    quality: int = 0                 # 0..255, 255 best
    fixed_at: float = 0.0            # monotonic time of last update

    @property
    def valid(self) -> bool:
        return self.source != sp.PosSource.NONE and (self.lat != 0.0 or self.lon != 0.0)


@dataclass
class Neighbor:
    eui: str
    rssi: int
    range_cm: int
    link_quality: int
    last_seen: float


@dataclass
class RouteAssignment:
    primary: str = ""
    secondary: str = ""
    subscriptions: list[str] = field(default_factory=list)
    # Pushed to the module already? Avoids re-sending identical routes.
    pushed_hash: int = 0


@dataclass
class ModuleState:
    eui: str
    name: str = ""
    role: int = 0
    mount: int = sp.Mount.STANDALONE
    attached_to: str = ""
    sensors: int = 0
    fw_version: int = 0
    battery_pct: int = 100
    status: int = sp.Status.IDLE
    position: Position = field(default_factory=Position)
    readings: dict[int, float] = field(default_factory=dict)
    neighbors: dict[str, Neighbor] = field(default_factory=dict)
    route: RouteAssignment = field(default_factory=RouteAssignment)
    first_seen: float = field(default_factory=time.monotonic)
    last_seen: float = field(default_factory=time.monotonic)
    last_seq: int = -1
    online: bool = True
    registered: bool = False  # pushed to vehicle-api yet?

    # --- role helpers (the contributor-vs-consumer question) ---
    @property
    def is_provider(self) -> bool:
        return bool(self.role & sp.Role.PROVIDER)

    @property
    def is_consumer(self) -> bool:
        return bool(self.role & sp.Role.CONSUMER)

    @property
    def is_relay(self) -> bool:
        return bool(self.role & sp.Role.RELAY)

    @property
    def is_anchor(self) -> bool:
        """A trustworthy position reference for localizing others."""
        return self.position.valid and self.position.source in (
            sp.PosSource.GPS, sp.PosSource.FUSED) and self.position.quality >= 160

    @property
    def has_gps(self) -> bool:
        return bool(self.sensors & sp.Sensor.GPS)

    @property
    def has_imu(self) -> bool:
        return bool(self.sensors & sp.Sensor.IMU)

    def sensor_names(self) -> list[str]:
        return sp.sensor_list(self.sensors)

    def contribution(self) -> str:
        """One-word summary for the command center."""
        if self.is_provider and self.is_consumer:
            return "provider+consumer"
        if self.is_provider:
            return "provider"
        if self.is_consumer:
            return "consumer"
        return "passive"


class Registry:
    def __init__(self, gateway_eui: str) -> None:
        self.modules: dict[str, ModuleState] = {}
        self.gateway_eui = gateway_eui
        # New modules seen since the last drain (so the service can register them).
        self._newly_seen: list[str] = []

    # --- ingest from decoded protocol messages ---

    def _touch(self, eui: str) -> ModuleState:
        m = self.modules.get(eui)
        if m is None:
            m = ModuleState(eui=eui)
            self.modules[eui] = m
            self._newly_seen.append(eui)
        m.last_seen = time.monotonic()
        if not m.online:
            m.online = True
            self._newly_seen.append(eui)  # came back — re-register
        return m

    def on_hello(self, msg: sp.Hello) -> ModuleState:
        eui = sp.eui_str(msg.eui)
        m = self._touch(eui)
        # A reconfiguration (role/mount/sensors/attachment change) must re-register.
        changed = (m.role != msg.role or m.mount != msg.mount
                   or m.sensors != msg.sensors or m.attached_to != msg.attached_to)
        m.name = msg.name or m.name
        m.role = msg.role
        m.mount = msg.mount
        m.attached_to = msg.attached_to
        m.sensors = msg.sensors
        m.fw_version = msg.fw_version
        m.battery_pct = msg.battery_pct
        m.last_seq = msg.seq
        if changed:
            m.registered = False
            if eui not in self._newly_seen:
                self._newly_seen.append(eui)
        return m

    def on_telemetry(self, msg: sp.Telemetry) -> ModuleState:
        eui = sp.eui_str(msg.eui)
        m = self._touch(eui)
        m.status = msg.status
        m.battery_pct = msg.battery_pct
        if msg.pos_source != sp.PosSource.NONE:
            m.position = Position(
                lat=msg.lat, lon=msg.lon, alt=msg.alt, heading=msg.heading,
                source=msg.pos_source, quality=msg.pos_quality,
                fixed_at=time.monotonic())
        m.readings = {r.channel: r.value for r in msg.readings}
        return m

    def on_neighbors(self, msg: sp.Neighbors) -> ModuleState:
        eui = sp.eui_str(msg.eui)
        m = self._touch(eui)
        now = time.monotonic()
        for link in msg.links:
            nb = sp.eui_str(link.eui)
            m.neighbors[nb] = Neighbor(nb, link.rssi, link.range_cm,
                                       link.link_quality, now)
        # Drop stale neighbor entries.
        m.neighbors = {k: v for k, v in m.neighbors.items() if now - v.last_seen < 30}
        return m

    # --- liveness ---

    def expire(self, ttl_s: float) -> list[str]:
        """Mark modules offline past their TTL. Returns the ids that flipped."""
        now = time.monotonic()
        flipped = []
        for m in self.modules.values():
            if m.online and now - m.last_seen > ttl_s:
                m.online = False
                m.status = sp.Status.OFFLINE
                m.registered = False  # re-register if/when it comes back
                flipped.append(m.eui)
        return flipped

    def online_modules(self) -> list[ModuleState]:
        return [m for m in self.modules.values() if m.online]

    def drain_newly_seen(self) -> list[ModuleState]:
        out, self._newly_seen = self._newly_seen, []
        # De-dup while preserving order.
        seen, result = set(), []
        for eui in out:
            if eui not in seen and eui in self.modules:
                seen.add(eui)
                result.append(self.modules[eui])
        return result
