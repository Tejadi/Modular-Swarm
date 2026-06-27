"""Push the swarm picture into Olympus over the network.

olympus_link never imports Olympus source. It talks to the command center the
same way any external system would:

  * module registration  -> POST /api/v1/vehicles/register   (vehicle-api REST)
  * live telemetry        -> PUT  {prefix}/swarm/{id}/telemetry (Zenoh REST)
  * mesh topology         -> PUT  {prefix}/swarm/topology       (Zenoh REST)
  * registry event        -> PUT  {prefix}/registry/{id}        (Zenoh REST)

The telemetry payload uses the exact field names the dashboard's
useZenohPolling hook already routes (position.latitude, battery.percentage,
status, mesh_rssi, role), so modules show up on the map with no dashboard
change, while extra `swarm` fields feed the optional swarm panel.

Three sink modes (config.sink): "rest" (default, stdlib urllib), "zenoh"
(zenoh python lib if installed, falling back to rest), and "dryrun" (record
only — used by the test harness). Everything degrades gracefully: a failed push
is logged, never fatal.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone

import swarm_proto as sp
from config import Config
from model import ModuleState

log = logging.getLogger("olympus_link.client")

_STATUS_NAMES = {
    sp.Status.IDLE: "idle", sp.Status.SCANNING: "scanning",
    sp.Status.TRANSITING: "transiting", sp.Status.EXECUTING: "executing",
    sp.Status.RETURNING: "returning", sp.Status.CHARGING: "charging",
    sp.Status.EMERGENCY: "emergency", sp.Status.OFFLINE: "offline",
}
_POS_SOURCE_NAMES = {
    sp.PosSource.NONE: "none", sp.PosSource.GPS: "gps",
    sp.PosSource.RANGED: "ranged", sp.PosSource.IMU: "imu",
    sp.PosSource.FUSED: "fused",
}
_CHANNEL_NAMES = {c.value: c.name.lower() for c in sp.Channel}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class OlympusClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.prefix = cfg.key_prefix.rstrip("/")
        self._zenoh_session = None
        # dryrun bookkeeping for tests
        self.registered: set[str] = set()
        self.published: dict[str, dict] = {}
        self.last_topology: dict | None = None

        if cfg.sink == "zenoh":
            try:
                import zenoh  # type: ignore
                self._zenoh_session = zenoh.open(zenoh.Config())
                log.info("Using zenoh python session for publishing")
            except Exception as e:  # pragma: no cover - optional dep
                log.warning("zenoh lib unavailable (%s); falling back to REST", e)
                self.cfg.sink = "rest"

    # --- public API ---

    async def register_module(self, m: ModuleState) -> bool:
        body = self._register_body(m)
        if self.cfg.sink == "dryrun":
            self.registered.add(m.eui)
            log.info("[dryrun] register %s role=%s sensors=%s attached=%s",
                     m.eui, m.contribution(), m.sensor_names(), m.attached_to or "-")
            return True
        ok = await asyncio.to_thread(self._post_register, body)
        if ok:
            self.registered.add(m.eui)
        # Mirror the registration to a Zenoh registry key regardless, so the
        # event is visible even if vehicle-api is down.
        await self.publish(f"{self.prefix}/registry/{m.eui}", body)
        return ok

    async def publish_telemetry(self, m: ModuleState) -> None:
        payload = self._telemetry_body(m)
        key = f"{self.prefix}/swarm/{m.eui}/telemetry"
        self.published[m.eui] = payload
        await self.publish(key, payload)
        # Neighbor table on its own key for the optional swarm panel.
        await self.publish(f"{self.prefix}/swarm/{m.eui}/neighbors",
                           {"id": m.eui, "neighbors": payload["swarm"]["neighbors"]})

    async def publish_topology(self, topo: dict) -> None:
        self.last_topology = topo
        await self.publish(f"{self.prefix}/swarm/topology", topo)

    async def publish(self, key: str, obj: dict) -> None:
        if self.cfg.sink == "dryrun":
            return
        if self._zenoh_session is not None:
            try:
                self._zenoh_session.put(key, json.dumps(obj))
                return
            except Exception as e:  # pragma: no cover
                log.warning("zenoh put failed (%s); falling back to REST", e)
        await asyncio.to_thread(self._put_zenoh_rest, key, obj)

    # --- payload builders ---

    def _telemetry_body(self, m: ModuleState) -> dict:
        best_rssi = max((n.rssi for n in m.neighbors.values()), default=-60)
        readings = {_CHANNEL_NAMES.get(ch, str(ch)): v for ch, v in m.readings.items()}
        return {
            "drone_id": m.eui,
            "id": m.eui,
            "role": "swarm_module",
            "position": {
                "latitude": m.position.lat,
                "longitude": m.position.lon,
                "altitude": m.position.alt,
            },
            "heading": m.position.heading,
            "battery": {"percentage": int(m.battery_pct)},
            "status": _STATUS_NAMES.get(m.status, "idle"),
            "mesh_rssi": best_rssi,
            "timestamp": _now_iso(),
            "readings": readings,
            "swarm": {
                "name": m.name,
                "contribution": m.contribution(),
                "is_provider": m.is_provider,
                "is_consumer": m.is_consumer,
                "is_relay": m.is_relay,
                "mount": "vehicle" if m.mount == sp.Mount.VEHICLE else "standalone",
                "attached_to": m.attached_to,
                "sensors": m.sensor_names(),
                "position_source": _POS_SOURCE_NAMES.get(m.position.source, "none"),
                "position_quality": m.position.quality,
                "velocity": {"north": round(m.position.vel_n, 3),
                             "east": round(m.position.vel_e, 3)},
                "speed_mps": round(m.position.speed, 2),
                "position_std_m": round(m.position.pos_std, 2),
                "heading_std_deg": round(m.position.hdg_std, 1),
                "ekf_flags": m.position.ekf_flags,
                "node_class": m.node_class(),
                "capabilities": sp.capability_list(m.capabilities),
                "parent": m.route.primary,
                "secondary_parent": m.route.secondary,
                "subscriptions": m.route.subscriptions,
                "neighbors": [
                    {"id": n.eui, "rssi": n.rssi, "range_m": round(n.range_cm / 100.0, 2),
                     "quality": n.link_quality}
                    for n in m.neighbors.values()
                ],
            },
        }

    def _register_body(self, m: ModuleState) -> dict:
        # The capability model drives command authority: an ACTIVE node (autonomous
        # / overridable) is a partner that accepts mission commands; a PASSIVE node
        # is an observer the leader's gate refuses to command.
        node_class = m.node_class()
        active = node_class == "active"
        trust = "partner" if active else "observer"
        caps = [f"sensor:{s}" for s in m.sensor_names()]
        caps.append(f"contribution:{m.contribution()}")
        caps.append(f"class:{node_class}")
        caps += [f"cap:{c}" for c in sp.capability_list(m.capabilities)]
        caps.append(f"mount:{'vehicle' if m.mount == sp.Mount.VEHICLE else 'standalone'}")
        if m.attached_to:
            caps.append(f"attached_to:{m.attached_to}")
        manifest = {
            "provides_telemetry": True,
            "provides_detections": bool(m.sensors & sp.Sensor.CAMERA),
            "provides_features": m.is_provider,
            "accepted_commands": (["IDENTIFY", "SET_ROLE", "SET_RATE",
                                   "SET_WAYPOINT", "SET_MISSION", "OVERRIDE"]
                                  if active else ["IDENTIFY"]),
            "command_authority": "advisory" if active else "none",
            "participates_in_cbba": active and m.is_provider,
            "ttl_seconds": 0,
            "data_encryption_required": False,
        }
        return {
            "vehicle_id": m.eui,
            "role": "swarm_module",
            "capabilities": caps,
            "position": {
                "latitude": m.position.lat,
                "longitude": m.position.lon,
                "altitude": m.position.alt,
                "heading": m.position.heading,
            },
            "trust_tier": trust,
            "capability_manifest": manifest,
        }

    # --- transport (blocking; called via asyncio.to_thread) ---

    def _put_zenoh_rest(self, key: str, obj: dict) -> None:
        url = f"{self.cfg.zenoh_rest.rstrip('/')}/{key}"
        data = json.dumps(obj).encode()
        req = urllib.request.Request(url, data=data, method="PUT",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                resp.read()
        except urllib.error.URLError as e:
            log.debug("zenoh REST PUT %s failed: %s", key, e)
        except Exception as e:  # pragma: no cover
            log.debug("zenoh REST PUT %s error: %s", key, e)

    def _post_register(self, body: dict) -> bool:
        url = f"{self.cfg.vehicle_api.rstrip('/')}/api/v1/vehicles/register"
        data = json.dumps(body).encode()
        headers = {"Content-Type": "application/json"}
        if self.cfg.vehicle_api_key:
            headers["Authorization"] = f"Bearer {self.cfg.vehicle_api_key}"
        req = urllib.request.Request(url, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=4) as resp:
                resp.read()
                return True
        except urllib.error.HTTPError as e:
            log.warning("register %s -> HTTP %s", body["vehicle_id"], e.code)
        except urllib.error.URLError as e:
            log.debug("register %s failed: %s", body["vehicle_id"], e)
        except Exception as e:  # pragma: no cover
            log.debug("register %s error: %s", body["vehicle_id"], e)
        return False

    def close(self) -> None:
        if self._zenoh_session is not None:
            try:
                self._zenoh_session.close()
            except Exception:
                pass
