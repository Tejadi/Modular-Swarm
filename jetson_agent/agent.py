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
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "proto"))
sys.path.insert(0, os.path.join(HERE, "..", "olympus_link"))

import swarm_proto as sp  # noqa: E402
from config import Config  # noqa: E402  (olympus_link.config)
from model import Registry  # noqa: E402
from olympus_client import OlympusClient  # noqa: E402
from serial_link import AsyncSerial  # noqa: E402

from sensors import make_gps_source  # noqa: E402

log = logging.getLogger("jetson_agent")


class JetsonAgent:
    def __init__(self, cfg: Config, nrf_port: str) -> None:
        self.cfg = cfg
        self.nrf_port = nrf_port
        self.reg = Registry(gateway_eui="")  # routing is central; this just holds our module
        self.olympus = OlympusClient(cfg)
        self.gps = make_gps_source()
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

    async def _tick(self) -> None:
        if self.own_eui is None or self.own_eui not in self.reg.modules:
            return
        m = self.reg.modules[self.own_eui]
        self._fuse_jetson_gps(m)
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
            self.serial.close()
            self.olympus.close()
            self.gps.stop()

    def stop(self) -> None:
        self._stop.set()
