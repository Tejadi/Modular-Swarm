"""Configuration for the olympus_link command-station service.

Everything is overridable from the environment so the same binary runs against
the simulator (a PTY) or a real gateway nRF on /dev/ttyACM0, and against a live
Olympus or a dry-run sink.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Config:
    # --- Serial link to the gateway nRF ---
    serial_port: str = "/dev/ttyACM0"
    serial_baud: int = 115200  # ignored for USB-CDC, honored for UART

    # --- Gateway / command-station anchor ---
    # The gateway is the root of the aggregation tree and sits at the station.
    gateway_eui: str = "0000000000000000"
    station_lat: float = 37.7749
    station_lon: float = -122.4194
    station_alt: float = 0.0

    # --- Olympus endpoints ---
    # Zenoh REST API (the router exposes PUT/GET here; the dashboard polls it).
    zenoh_rest: str = "http://localhost:8000"
    # Key prefix the running dashboard instance subscribes to. The bundled
    # dashboard polls "ceres/**"; the Rust bridge default is "olympus". Set this
    # to match the deployment or the command center sees nothing.
    key_prefix: str = "ceres"
    # vehicle-api base URL + key for module registration (existing endpoint).
    vehicle_api: str = "http://localhost:3001"
    vehicle_api_key: str = ""

    # --- Behavior ---
    # Mark a module offline if no message arrives within this many seconds.
    module_ttl_s: float = 12.0
    # Re-parent only if a new path beats the current one by this fraction.
    reparent_margin: float = 0.15
    # Max providers a consumer is subscribed to.
    max_subscriptions: int = 3
    # How often (s) to recompute routes / localization / liveness.
    tick_s: float = 1.0
    # Auto-approve swarm modules (impractical to hand-approve an ad-hoc swarm).
    swarm_auto_approve: bool = True
    # Olympus output mode: "rest" (zenoh REST PUT + vehicle-api), "zenoh"
    # (zenoh python lib if installed), or "dryrun" (log only, for testing).
    sink: str = "rest"

    @classmethod
    def from_env(cls) -> "Config":
        def f(name: str, default: float) -> float:
            return float(os.environ.get(name, default))

        return cls(
            serial_port=os.environ.get("SWARM_SERIAL_PORT", cls.serial_port),
            serial_baud=int(os.environ.get("SWARM_SERIAL_BAUD", cls.serial_baud)),
            gateway_eui=os.environ.get("SWARM_GATEWAY_EUI", cls.gateway_eui),
            station_lat=f("SWARM_STATION_LAT", cls.station_lat),
            station_lon=f("SWARM_STATION_LON", cls.station_lon),
            station_alt=f("SWARM_STATION_ALT", cls.station_alt),
            zenoh_rest=os.environ.get("SWARM_ZENOH_REST", cls.zenoh_rest),
            key_prefix=os.environ.get("SWARM_KEY_PREFIX", cls.key_prefix),
            vehicle_api=os.environ.get("SWARM_VEHICLE_API", cls.vehicle_api),
            vehicle_api_key=os.environ.get("SWARM_VEHICLE_API_KEY", cls.vehicle_api_key),
            module_ttl_s=f("SWARM_MODULE_TTL", cls.module_ttl_s),
            reparent_margin=f("SWARM_REPARENT_MARGIN", cls.reparent_margin),
            max_subscriptions=int(os.environ.get("SWARM_MAX_SUBS", cls.max_subscriptions)),
            tick_s=f("SWARM_TICK", cls.tick_s),
            swarm_auto_approve=os.environ.get("SWARM_AUTO_APPROVE", "1") not in ("0", "false", "False"),
            sink=os.environ.get("SWARM_SINK", cls.sink),
        )
