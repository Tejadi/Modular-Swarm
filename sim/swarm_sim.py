"""Synthetic nRF swarm — stands in for real hardware.

Models a handful of reconfigurable modules with mixed sensor stacks (GPS+IMU
anchors, IMU-only and rangefinder-only consumers), some vehicle-mounted and some
standalone. Each module emits HELLO / TELEMETRY / NEIGHBORS exactly as the
firmware would, with realistic RSSI / RTT-range neighbor tables derived from the
true geometry, so olympus_link's localization and autorouter have real data to
chew on. A built-in timeline drops a module offline and reconfigures another so
the failover and re-registration paths get exercised.

Two ways to run it:
  * standalone over a PTY (drives a live olympus_link + dashboard):
        python sim/swarm_sim.py            # prints the device path to use
  * embedded by the end-to-end test, driven with a fake clock.

The core (SwarmSim) is transport-agnostic: it calls an emit(payload) callback
with raw protocol payloads; the caller frames/transports them.
"""

from __future__ import annotations

import math
import os
import random
import sys
import time
from dataclasses import dataclass, field

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "proto"))
import swarm_proto as sp  # noqa: E402

STATION_LAT = 37.7749
STATION_LON = -122.4194
_M_PER_DEG_LAT = 111320.0
COMM_RANGE_M = 250.0
GATEWAY_EUI = "0000000000000000"


def _enu_to_ll(east: float, north: float) -> tuple[float, float]:
    lat = STATION_LAT + north / _M_PER_DEG_LAT
    lon = STATION_LON + east / (_M_PER_DEG_LAT * math.cos(math.radians(STATION_LAT)))
    return lat, lon


@dataclass
class SimModule:
    eui: str
    name: str
    role: int
    mount: int
    attached_to: str
    sensors: int
    east: float          # meters from station
    north: float
    vx: float = 0.0
    vy: float = 0.0
    battery: float = 100.0
    online: bool = True
    seq: int = 0

    @property
    def has_gps(self) -> bool:
        return bool(self.sensors & sp.Sensor.GPS)

    @property
    def has_imu(self) -> bool:
        return bool(self.sensors & sp.Sensor.IMU)

    def eui_b(self) -> bytes:
        return sp.eui_bytes(self.eui)


def _default_fleet() -> list[SimModule]:
    R = sp.Role
    S = sp.Sensor
    prov_relay = R.PROVIDER | R.RELAY
    return [
        SimModule("a1a1a1a1a1a1a1a1", "node-alpha", prov_relay, sp.Mount.VEHICLE,
                  "rover-07", S.GPS | S.IMU | S.TEMP, 30, 20),
        SimModule("b2b2b2b2b2b2b2b2", "node-bravo", R.PROVIDER, sp.Mount.STANDALONE,
                  "", S.GPS | S.BARO, 120, -40),
        SimModule("c3c3c3c3c3c3c3c3", "node-charlie", prov_relay, sp.Mount.STANDALONE,
                  "", S.GPS | S.IMU, -60, 180),
        SimModule("f6f6f6f6f6f6f6f6", "node-foxtrot", R.PROVIDER, sp.Mount.STANDALONE,
                  "", S.GPS, 200, 120),
        # IMU-only consumer — no GPS; must be localized by ranging to anchors.
        SimModule("d4d4d4d4d4d4d4d4", "node-delta", R.CONSUMER, sp.Mount.STANDALONE,
                  "", S.IMU, 60, 90),
        # Rangefinder + IMU consumer on a vehicle — also localized by ranging.
        SimModule("e5e5e5e5e5e5e5e5", "node-echo", R.CONSUMER | R.RELAY, sp.Mount.VEHICLE,
                  "rover-09", S.IMU | S.RANGEFINDER, -30, 60),
    ]


@dataclass
class TimelineEvent:
    at_s: float
    kind: str            # "kill" | "revive" | "detach" | "set_role"
    eui: str
    arg: object = None
    done: bool = False


class SwarmSim:
    def __init__(self, emit, fleet: list[SimModule] | None = None,
                 gateway_eui: str = GATEWAY_EUI, seed: int = 1) -> None:
        self.emit = emit
        self.modules = {m.eui: m for m in (fleet or _default_fleet())}
        self.gateway_eui = gateway_eui
        self.rng = random.Random(seed)
        self._last_hello = 0.0
        self._last_nbr = 0.0
        self.timeline: list[TimelineEvent] = [
            TimelineEvent(6.0, "kill", "b2b2b2b2b2b2b2b2"),     # anchor drops out
            TimelineEvent(10.0, "detach", "a1a1a1a1a1a1a1a1"),  # module leaves its vehicle
            TimelineEvent(14.0, "revive", "b2b2b2b2b2b2b2b2"),  # anchor returns
        ]

    # --- geometry / link model ---

    def _dist(self, a: SimModule, b: SimModule) -> float:
        return math.hypot(a.east - b.east, a.north - b.north)

    def _dist_station(self, a: SimModule) -> float:
        return math.hypot(a.east, a.north)

    def _rssi(self, d: float) -> int:
        return int(max(-100, min(-30, -40 - 0.2 * d)))

    def _lq(self, d: float) -> int:
        return int(max(0, min(255, 255 - d)))

    def _range_cm(self, d: float) -> int:
        # RTT/RSSI range estimate: true distance + coarse noise (~1.5 m sigma).
        return max(0, int((d + self.rng.gauss(0, 1.5)) * 100))

    # --- simulation step ---

    def step(self, dt: float, now: float) -> None:
        for ev in self.timeline:
            if not ev.done and now >= ev.at_s:
                self._apply_event(ev)
                ev.done = True
        for m in self.modules.values():
            if not m.online:
                continue
            # Gentle random walk, bounded near the station.
            m.vx += self.rng.gauss(0, 0.15)
            m.vy += self.rng.gauss(0, 0.15)
            m.vx = max(-1.5, min(1.5, m.vx))
            m.vy = max(-1.5, min(1.5, m.vy))
            m.east += m.vx * dt
            m.north += m.vy * dt
            m.battery = max(0.0, m.battery - 0.02 * dt)

    def _apply_event(self, ev: TimelineEvent) -> None:
        m = self.modules.get(ev.eui)
        if not m:
            return
        if ev.kind == "kill":
            m.online = False
        elif ev.kind == "revive":
            m.online = True
        elif ev.kind == "detach":
            m.mount = sp.Mount.STANDALONE
            m.attached_to = ""
        elif ev.kind == "set_role":
            m.role = int(ev.arg)

    # --- message emission ---

    def emit_round(self, now: float, hello: bool = False, nbr: bool = False) -> None:
        for m in self.modules.values():
            if not m.online:
                continue
            if hello:
                self._emit_hello(m, now)
            self._emit_telemetry(m, now)
            if nbr:
                self._emit_neighbors(m)
        if nbr:
            self._emit_gateway_neighbors()

    def _emit_hello(self, m: SimModule, now: float) -> None:
        m.seq += 1
        msg = sp.Hello(eui=m.eui_b(), role=m.role, mount=m.mount,
                       sensors=m.sensors, fw_version=1, battery_pct=int(m.battery),
                       uptime_s=int(now), name=m.name, attached_to=m.attached_to,
                       seq=m.seq, flags=int(sp.Flags.RELAYED))
        self.emit(msg.encode())

    def _emit_telemetry(self, m: SimModule, now: float) -> None:
        m.seq += 1
        readings = []
        if m.sensors & sp.Sensor.TEMP:
            readings.append(sp.Reading(sp.Channel.TEMP, 20 + 4 * math.sin(now / 5)))
        if m.sensors & sp.Sensor.RANGEFINDER:
            readings.append(sp.Reading(sp.Channel.RANGEFINDER, 1.0 + self.rng.random()))

        if m.has_gps:
            lat, lon = _enu_to_ll(m.east, m.north)
            src, q = sp.PosSource.GPS, 220
        elif m.has_imu:
            # IMU-only: report a drifting dead-reckoned guess (low quality). The
            # command station refines this by ranging against the GPS anchors.
            drift = 8.0
            lat, lon = _enu_to_ll(m.east + self.rng.gauss(0, drift),
                                  m.north + self.rng.gauss(0, drift))
            src, q = sp.PosSource.IMU, 60
        else:
            lat = lon = 0.0
            src, q = sp.PosSource.NONE, 0

        heading = (math.degrees(math.atan2(m.vy, m.vx))) % 360
        msg = sp.Telemetry(eui=m.eui_b(), status=sp.Status.SCANNING, pos_source=src,
                           lat=lat, lon=lon, alt=0.0, heading=heading,
                           battery_pct=int(m.battery), pos_quality=q,
                           readings=readings, seq=m.seq, flags=int(sp.Flags.RELAYED))
        self.emit(msg.encode())

    def _emit_neighbors(self, m: SimModule) -> None:
        links = []
        for other in self.modules.values():
            if other.eui == m.eui or not other.online:
                continue
            d = self._dist(m, other)
            if d > COMM_RANGE_M:
                continue
            links.append(sp.NeighborLink(other.eui_b(), self._rssi(d),
                                         self._range_cm(d), self._lq(d)))
        # Gateway is a neighbor too if the station is in range.
        ds = self._dist_station(m)
        if ds <= COMM_RANGE_M:
            links.append(sp.NeighborLink(sp.eui_bytes(self.gateway_eui),
                                         self._rssi(ds), self._range_cm(ds), self._lq(ds)))
        m.seq += 1
        self.emit(sp.Neighbors(eui=m.eui_b(), links=links, seq=m.seq,
                               flags=int(sp.Flags.RELAYED)).encode())

    def _emit_gateway_neighbors(self) -> None:
        """The gateway reports which modules it hears directly (tree roots)."""
        links = []
        for m in self.modules.values():
            if not m.online:
                continue
            d = self._dist_station(m)
            if d <= COMM_RANGE_M:
                links.append(sp.NeighborLink(m.eui_b(), self._rssi(d),
                                             self._range_cm(d), self._lq(d)))
        self.emit(sp.Neighbors(eui=sp.eui_bytes(self.gateway_eui), links=links,
                               seq=int(time.monotonic()) & 0xFFFFFFFF,
                               flags=int(sp.Flags.GATEWAY)).encode())


# --- standalone PTY runner ---

def _run_pty(duration: float) -> None:
    master_fd, slave_fd = os.openpty()
    slave_path = os.ttyname(slave_fd)
    reader = sp.SerialReader()

    def emit(payload: bytes) -> None:
        os.write(master_fd, sp.frame_serial(payload))

    sim = SwarmSim(emit)

    print("=" * 64)
    print(" nRF swarm simulator running")
    print(f"   point olympus_link at:  --port {slave_path}")
    print(f"   gateway EUI:            {sim.gateway_eui}")
    print(f"   modules:                {len(sim.modules)}")
    print("=" * 64, flush=True)

    os.set_blocking(master_fd, False)
    start = time.monotonic()
    last = start
    last_hello = last_nbr = 0.0
    try:
        while time.monotonic() - start < duration:
            now = time.monotonic() - start
            dt = now - (last - start)
            last = time.monotonic()
            sim.step(dt, now)
            do_hello = now - last_hello >= 3.0
            do_nbr = now - last_nbr >= 2.0
            if do_hello:
                last_hello = now
            if do_nbr:
                last_nbr = now
            sim.emit_round(now, hello=do_hello or now < 1.0, nbr=do_nbr)
            # Drain any downlink (ROUTE/CMD) from olympus_link and show it.
            try:
                data = os.read(master_fd, 4096)
                for payload in reader.feed(data):
                    msg = sp.decode(payload)
                    if msg is not None:
                        print(f"  <- downlink {type(msg).__name__} for "
                              f"{sp.eui_str(msg.eui)}", flush=True)
            except (BlockingIOError, OSError):
                pass
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        os.close(master_fd)
        os.close(slave_fd)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="nRF swarm simulator (PTY)")
    ap.add_argument("--duration", type=float, default=600.0)
    args = ap.parse_args()
    _run_pty(args.duration)
