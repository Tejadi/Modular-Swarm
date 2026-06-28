"""The command-station service: glue between the mesh and Olympus.

Reads decoded swarm messages off the gateway serial link, keeps the live
registry, runs localization + the autorouter every tick, registers new modules
with Olympus, streams telemetry, publishes the topology, and pushes route
assignments back down to the mesh through the gateway.
"""

from __future__ import annotations

import asyncio
import logging

import swarm_proto as sp
from autorouter import Autorouter
from commands import CommandListener
from config import Config
from localization import localize
from model import Registry
from olympus_client import OlympusClient
from serial_link import AsyncSerial

log = logging.getLogger("olympus_link.service")


class SwarmLinkService:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.reg = Registry(cfg.gateway_eui)
        self.router = Autorouter(self.reg, cfg)
        self.olympus = OlympusClient(cfg)
        self.commands: CommandListener | None = None
        self.serial: AsyncSerial | None = None
        self._seq = 0
        self._stop = asyncio.Event()

    # --- inbound: decode + dispatch ---

    def _on_payload(self, payload: bytes) -> None:
        try:
            msg = sp.decode(payload)
        except Exception as e:
            log.debug("undecodable payload (%d B): %s", len(payload), e)
            return
        if msg is None:
            return
        # The gateway tags its own messages; adopt its EUI as the tree root so
        # the operator never has to hand-configure it.
        if msg.flags & sp.Flags.GATEWAY and sp.eui_str(msg.eui) != self.reg.gateway_eui:
            self.reg.gateway_eui = sp.eui_str(msg.eui)
            log.info("gateway discovered: %s", self.reg.gateway_eui)
        if isinstance(msg, sp.Hello):
            m = self.reg.on_hello(msg)
            log.info("HELLO %s '%s' role=%s mount=%s sensors=%s",
                     m.eui, m.name, m.contribution(),
                     "vehicle" if m.mount == sp.Mount.VEHICLE else "standalone",
                     m.sensor_names())
        elif isinstance(msg, sp.Telemetry):
            self.reg.on_telemetry(msg)
        elif isinstance(msg, sp.Neighbors):
            self.reg.on_neighbors(msg)
        elif isinstance(msg, (sp.RangeReq, sp.RangeResp)):
            pass  # ranging is consumed via neighbor reports; nothing to do here
        # ROUTE / CMD are downlink-only; we never receive them.

    # --- periodic work ---

    async def _tick(self) -> None:
        # 1. Liveness — flip stale modules offline, push a final OFFLINE status
        #    so the command center sees it, and reroute around them next.
        flipped = self.reg.expire(self.cfg.module_ttl_s)
        for eui in flipped:
            log.warning("module %s went OFFLINE (TTL)", eui)
            if eui != self.reg.gateway_eui:
                await self.olympus.publish_telemetry(self.reg.modules[eui])

        # 2. Register modules that just appeared or were reconfigured. A module
        #    that timed out has registered=False, so a revival re-registers it.
        for m in self.reg.drain_newly_seen():
            if m.eui == self.reg.gateway_eui:
                continue  # the gateway is infrastructure, not a swarm module
            if not m.registered:
                await self.olympus.register_module(m)
                m.registered = True

        # 3. Localize modules that lack their own GPS fix.
        fixed = localize(self.reg)
        if fixed:
            log.debug("localized %d module(s) via ranging: %s", len(fixed), fixed)

        # 4. Recompute the overlay routes.
        changed = self.router.compute()
        for m in changed:
            self._push_route(m)
        orphans = self.router.orphans()
        if orphans:
            log.warning("orphaned modules (no path to gateway): %s", orphans)

        # 5. Stream telemetry for every online module + publish the topology.
        #    The gateway is published too: a leader is a gateway AND a real
        #    GPS+IMU node, so it belongs on the map as a swarm node.
        for m in self.reg.online_modules():
            await self.olympus.publish_telemetry(m)
        await self.olympus.publish_topology(self.router.topology())

    def _push_route(self, m) -> None:
        if self.serial is None or m.eui == self.reg.gateway_eui:
            return
        self._seq += 1
        route = self.router.route_message(m, self._seq)
        # Mark as gateway-originated so the firmware knows it is authoritative.
        route.flags = int(sp.Flags.GATEWAY)
        self.serial.write_payload(route.encode())
        log.info("ROUTE %s -> parent=%s secondary=%s subs=%s",
                 m.eui, m.route.primary or "-", m.route.secondary or "-",
                 m.route.subscriptions)

    # --- public command downlink (operator -> module) ---

    def send_command(self, eui: str, op: int, params: bytes = b"") -> None:
        if self.serial is None:
            return
        self._seq += 1
        cmd = sp.Cmd(eui=sp.eui_bytes(eui), op=op, params=params,
                     seq=self._seq, flags=int(sp.Flags.GATEWAY))
        self.serial.write_payload(cmd.encode())
        log.info("CMD %s op=%s", eui, op)

    def send_broadcast(self, op: int, params: bytes = b"") -> None:
        """Fleet-wide multicast command (leader override). The leader nRF re-emits
        it to ff03::1; only OVERRIDABLE nodes act on action ops."""
        if self.serial is None:
            return
        self._seq += 1
        leader = self.reg.gateway_eui or "0" * 16
        bc = sp.Broadcast(eui=sp.eui_bytes(leader), op=op, params=params,
                          seq=self._seq, flags=int(sp.Flags.LEADER | sp.Flags.OVERRIDE))
        self.serial.write_payload(bc.encode())
        log.info("BROADCAST op=%s", op)

    # --- lifecycle ---

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        log.info("opening gateway serial %s", self.cfg.serial_port)
        self.serial = AsyncSerial(self.cfg.serial_port, self._on_payload,
                                  self.cfg.serial_baud)
        self.serial.start(loop)
        # Operator command downlink: Olympus -> mesh (needs the zenoh lib).
        self.commands = CommandListener(self.cfg, self, loop)
        self.commands.start()
        log.info("olympus_link up: prefix=%s sink=%s gateway=%s",
                 self.cfg.key_prefix, self.cfg.sink, self.cfg.gateway_eui)
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
            if self.commands is not None:
                self.commands.stop()
            self.serial.close()
            self.olympus.close()

    def stop(self) -> None:
        self._stop.set()
