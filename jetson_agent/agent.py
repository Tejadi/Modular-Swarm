"""Per-agent Jetson companion.

Runs on each agent next to its local nRF module. It reads the module's own
messages off the serial link (the firmware mirrors its announce/telemetry/
neighbor reports there), fuses in any Jetson-side GPS/IMU, and publishes the
module to Olympus over IP — a redundant path to the RF gateway, so an agent
with WiFi/5G stays visible even if the mesh backhaul is degraded. It also
forwards operator commands from Olympus down to the nRF over the same serial.

Reuses olympus_link's model + Olympus client; only one module (this agent's) is
tracked, and routing stays central on the command station.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import sys
import time

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "proto"))
sys.path.insert(0, os.path.join(HERE, "..", "olympus_link"))

import swarm_proto as sp  # noqa: E402
from config import Config  # noqa: E402  (olympus_link.config)
from model import Registry  # noqa: E402
from olympus_client import OlympusClient  # noqa: E402
from serial_link import AsyncSerial  # noqa: E402

from sensors import make_gps_source  # noqa: E402
from ekf import JetsonEKF  # noqa: E402
from peers import gate_50m  # noqa: E402
from vio import make_vio  # noqa: E402
from override import Override  # noqa: E402
from mission import MissionPlanner, MissionMode  # noqa: E402
from detections import DetectionReader  # noqa: E402
import coordination as co  # noqa: E402

log = logging.getLogger("jetson_agent")


class JetsonAgent:
    def __init__(self, cfg: Config, nrf_port: str) -> None:
        self.cfg = cfg
        self.nrf_port = nrf_port
        self.reg = Registry(gateway_eui="")  # routing is central; this just holds our module
        self.olympus = OlympusClient(cfg)
        self.gps = make_gps_source()
        self.ekf = JetsonEKF()
        self.vio = None
        self._ekf_last: float | None = None
        # Decentralized autonomy: base-station override + mission FSM + perception.
        self.override = Override()
        self.mission: MissionPlanner | None = None   # created at the first fix (home)
        self.detections: DetectionReader | None = None
        self._frontier_dir = (1.0, 0.0)
        self.last_mission: dict = {}
        self.own_eui: str | None = None
        self.serial: AsyncSerial | None = None
        self._seq = 0
        self._stop = asyncio.Event()

    def _on_payload(self, payload: bytes) -> None:
        try:
            msg = sp.decode(payload)
        except Exception:
            return
        if msg is None:
            return
        # Over a node's serial we only ever see that node's own messages.
        if self.own_eui is None and not (msg.flags & (sp.Flags.GATEWAY | sp.Flags.RELAYED)):
            self.own_eui = sp.eui_str(msg.eui)
            log.info("local module is %s", self.own_eui)
        if isinstance(msg, sp.Hello):
            self.reg.on_hello(msg)
        elif isinstance(msg, sp.Telemetry):
            self.reg.on_telemetry(msg)
        elif isinstance(msg, sp.Neighbors):
            self.reg.on_neighbors(msg)
        elif isinstance(msg, (sp.Cmd, sp.Broadcast)):
            # An action command the nRF forwarded up (leader override / waypoint).
            # The override layer consumes the mission ops; config ops were already
            # applied on the nRF.
            if self.override.apply_cmd(msg.op, msg.params):
                log.info("override: op=%s from %s", msg.op,
                         "broadcast" if isinstance(msg, sp.Broadcast) else "leader")

    def _fuse_jetson_gps(self, m) -> None:
        """If GPS lives on the Jetson, fold its fix into the module's position."""
        fix = self.gps.read()
        if not fix:
            return
        lat, lon, alt, quality = fix
        from model import Position
        # Prefer a real Jetson GPS fix over an IMU-only estimate.
        if not m.has_gps or m.position.source != sp.PosSource.GPS:
            m.sensors |= sp.Sensor.GPS  # advertise GPS in the manifest
            m.position = Position(lat=lat, lon=lon, alt=alt, heading=m.position.heading,
                                  source=sp.PosSource.GPS, quality=quality)

    def _fuse_jetson_ekf(self, m) -> None:
        """Refine the nRF fix with camera VIO + nearby-peer ranges, then inject the
        result back to the nRF so it broadcasts the better pose on the mesh."""
        pos = m.position
        if not pos.valid:
            return
        now = time.monotonic()
        if self._ekf_last is not None:
            self.ekf.predict(now - self._ekf_last)
        self._ekf_last = now

        # Primary: the nRF's OWN fused pose (the serial stream carries the EKF fix,
        # never our injected pose — so this is an independent absolute reference).
        # Skip if this telemetry is our own refined fix echoed back (no loop).
        own_nrf = not (pos.ekf_flags & (int(sp.EkfFlag.VIO_USED) | int(sp.EkfFlag.PEER_USED)))
        if own_nrf:
            self.ekf.update_nrf(pos.lat, pos.lon, pos.heading, pos.vel_n, pos.vel_e,
                                pos.pos_std or 5.0, pos.hdg_std or 10.0)
        flags = int(sp.EkfFlag.GPS_USED | sp.EkfFlag.IMU_USED)

        # Camera VIO (body-frame -> ENU via the current heading; same convention as ekf.c).
        if self.vio is not None:
            d = self.vio.poll()
            if d and d.tracking_ok and d.dt > 0:
                psi = math.radians(90.0 - self.ekf.get_fix()["heading"])
                left = -d.d_right
                dN = d.d_forward * math.sin(psi) + left * math.cos(psi)
                dE = d.d_forward * math.cos(psi) - left * math.sin(psi)
                self.ekf.update_vio(dN, dE, d.d_yaw, d.dt, d.cov)
                m.sensors |= int(sp.Sensor.VIO)
                flags |= int(sp.EkfFlag.VIO_USED)

        # Nearby peers (<= 50 m) as range constraints.
        peer_pos = {e: (mm.position.lat, mm.position.lon)
                    for e, mm in self.reg.modules.items()
                    if e != self.own_eui and mm.position.valid}
        used_peer = False
        for p in gate_50m(m.neighbors, peer_pos):
            pN, pE = self.ekf.to_enu(p["lat"], p["lon"])
            if self.ekf.update_range(pN, pE, p["range_m"]):
                used_peer = True
        if used_peer:
            flags |= int(sp.EkfFlag.PEER_USED)

        fix = self.ekf.get_fix()
        if fix["converged"]:
            flags |= int(sp.EkfFlag.CONVERGED)
        from model import Position
        m.position = Position(
            lat=fix["lat"], lon=fix["lon"], alt=pos.alt, heading=fix["heading"],
            source=sp.PosSource.FUSED,
            quality=max(1, min(255, int(255 / (1 + fix["pos_std"] / 5.0)))),
            vel_n=fix["vel_n"], vel_e=fix["vel_e"],
            pos_std=fix["pos_std"], hdg_std=fix["hdg_std"], ekf_flags=flags,
            fixed_at=now)
        self._inject_pose(m, fix, flags)

    def _inject_pose(self, m, fix: dict, flags: int) -> None:
        """Send POSE_INJECT to the nRF over serial — the authoritative pose to broadcast."""
        if self.serial is None or self.own_eui is None:
            return
        self._seq += 1
        pi = sp.PoseInject(
            eui=sp.eui_bytes(self.own_eui), lat=fix["lat"], lon=fix["lon"],
            alt=m.position.alt, heading=fix["heading"],
            vel_n=fix["vel_n"], vel_e=fix["vel_e"],
            pos_std=fix["pos_std"], hdg_std=fix["hdg_std"],
            src_flags=flags & 0xFF, ts_ms=int(time.monotonic() * 1000) & 0xFFFFFFFF,
            seq=self._seq)
        self.serial.write_payload(pi.encode())

    def _run_mission(self, m) -> None:
        """Decentralized mission step: coverage/explore/search + ORCA avoidance,
        preempted by a base-station override (EMERGENCY outranks both). Sets the
        module status; the resulting setpoint drives the vehicle controller, which
        is vehicle-specific and lives outside this repo."""
        if not m.position.valid:
            return
        fix = self.ekf.get_fix()
        if self.mission is None:
            self.mission = MissionPlanner(home_ll=(fix["lat"], fix["lon"]))

        # Neighbors (ENU) for coordination, from the peer telemetry the nRF forwards.
        neigh = []
        for e, mm in self.reg.modules.items():
            if e == self.own_eui or not mm.position.valid:
                continue
            p = self.ekf.to_enu(mm.position.lat, mm.position.lon)
            neigh.append((p, (mm.position.vel_n, mm.position.vel_e), co.DEFAULT_RADIUS))

        # Explore/search drive toward a decentralized frontier (spread to cover).
        mode = self.mission.mode
        if self.override.active() and self.override.mission() is not None:
            mode = MissionMode(self.override.mission())
        frontier = None
        if mode in (MissionMode.EXPLORE, MissionMode.SEARCH):
            my = self.ekf.to_enu(fix["lat"], fix["lon"])
            tgt = co.frontier_target(my, [n[0] for n in neigh], prev_dir=self._frontier_dir)
            dx, dy = tgt[0] - my[0], tgt[1] - my[1]
            n = math.hypot(dx, dy)
            if n > 1e-3:
                self._frontier_dir = (dx / n, dy / n)
            frontier = self.ekf.to_ll(*tgt)

        out = self.mission.step(self.ekf, neigh, self.override, frontier_goal=frontier)
        m.status = out["status"]
        if self.detections is not None:
            out["detections"] = len(self.detections.poll())
        self.last_mission = out

    async def _tick(self) -> None:
        if self.own_eui is None or self.own_eui not in self.reg.modules:
            return
        m = self.reg.modules[self.own_eui]
        self._fuse_jetson_gps(m)
        self._fuse_jetson_ekf(m)
        self._run_mission(m)
        if not m.registered:
            await self.olympus.register_module(m)
            m.registered = True
        await self.olympus.publish_telemetry(m)

    def send_command(self, op: int, params: bytes = b"") -> None:
        if self.serial is None or self.own_eui is None:
            return
        self._seq += 1
        cmd = sp.Cmd(eui=sp.eui_bytes(self.own_eui), op=op, params=params, seq=self._seq)
        self.serial.write_payload(cmd.encode())

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        log.info("jetson_agent: nRF=%s -> Olympus prefix=%s sink=%s",
                 self.nrf_port, self.cfg.key_prefix, self.cfg.sink)
        self.serial = AsyncSerial(self.nrf_port, self._on_payload, self.cfg.serial_baud)
        self.serial.start(loop)
        self.vio = make_vio()           # auto-detect camera (NullVio if none)
        self.detections = DetectionReader()   # reads the perception process (if running)
        self._ekf_last = time.monotonic()
        try:
            while not self._stop.is_set():
                try:
                    await self._tick()
                except Exception:
                    log.exception("tick failed")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.cfg.tick_s)
                except asyncio.TimeoutError:
                    pass
        finally:
            if self.vio is not None:
                self.vio.stop()
            if self.detections is not None:
                self.detections.close()
            self.serial.close()
            self.olympus.close()
            self.gps.stop()

    def stop(self) -> None:
        self._stop.set()
