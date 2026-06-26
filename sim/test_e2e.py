"""End-to-end check of the command-station pipeline, no hardware required.

Drives the SwarmSim through the real SwarmLinkService (decode -> registry ->
localization -> autorouter -> Olympus push) over a virtual clock and asserts the
behaviors the plan promised:

  * every online module registers with Olympus,
  * IMU-only / rangefinder-only modules get a multilaterated position from the
    GPS anchors,
  * the autorouter assigns every module a parent toward the gateway,
  * killing an anchor flips it OFFLINE and the swarm reroutes without orphans,
  * detaching a module from its vehicle re-registers it,
  * a revived module comes back online and re-registers.

Uses the dry-run sink, so it records pushes in-memory instead of hitting a live
Olympus. Run: python sim/test_e2e.py
"""

from __future__ import annotations

import asyncio
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "proto"))
sys.path.insert(0, os.path.join(HERE, "..", "olympus_link"))
sys.path.insert(0, HERE)

import swarm_proto as sp  # noqa: E402
import model  # noqa: E402
from config import Config  # noqa: E402
from service import SwarmLinkService  # noqa: E402
from swarm_sim import SwarmSim, GATEWAY_EUI, STATION_LAT, STATION_LON, _enu_to_ll, TimelineEvent  # noqa: E402


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t


GPS_ANCHORS = {"a1a1a1a1a1a1a1a1", "b2b2b2b2b2b2b2b2",
               "c3c3c3c3c3c3c3c3", "f6f6f6f6f6f6f6f6"}
NON_GPS = {"d4d4d4d4d4d4d4d4", "e5e5e5e5e5e5e5e5"}
A, B = "a1a1a1a1a1a1a1a1", "b2b2b2b2b2b2b2b2"


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    import math
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def main() -> int:
    clk = FakeClock()
    model.time = clk  # liveness/TTL now run on the virtual clock

    cfg = Config(sink="dryrun", gateway_eui=GATEWAY_EUI,
                 station_lat=STATION_LAT, station_lon=STATION_LON,
                 module_ttl_s=3.0)
    svc = SwarmLinkService(cfg)
    # The service normally opens serial; for the test we feed payloads straight
    # into the decode path and capture downlink route writes in a list.
    sent_routes: list = []

    class FakeSerial:
        def write_payload(self, payload: bytes) -> None:
            sent_routes.append(sp.decode(payload))

    svc.serial = FakeSerial()

    # Count registrations per module to prove re-registration happens.
    reg_counts: dict[str, int] = {}
    orig_register = svc.olympus.register_module

    async def counting_register(m):
        reg_counts[m.eui] = reg_counts.get(m.eui, 0) + 1
        return await orig_register(m)

    svc.olympus.register_module = counting_register

    sim = SwarmSim(emit=svc._on_payload, gateway_eui=GATEWAY_EUI)
    sim.timeline = [
        TimelineEvent(4.0, "kill", B),     # anchor drops out
        TimelineEvent(7.0, "detach", A),   # module leaves its vehicle
        TimelineEvent(11.0, "revive", B),  # anchor returns
    ]

    checkpoints: dict[str, dict] = {}

    async def drive() -> None:
        dt = 0.5
        t = 0.0
        while t <= 15.0:
            clk.t = t
            sim.step(dt, t)
            sim.emit_round(t, hello=(round(t * 2) % 6 == 0), nbr=True)
            await svc._tick()
            # Snapshot at the moments we assert on.
            for mark in (3.0, 8.0, 13.0):
                if abs(t - mark) < 1e-6:
                    checkpoints[f"t{int(mark)}"] = _snapshot(svc)
            t = round(t + dt, 3)

    asyncio.run(drive())

    return _assert(svc, checkpoints, reg_counts, sent_routes)


def _snapshot(svc: SwarmLinkService) -> dict:
    reg = svc.reg
    return {
        "online": {m.eui for m in reg.online_modules() if m.eui != reg.gateway_eui},
        "registered": set(svc.olympus.registered),
        "positions": {e: (m.position.source, m.position.quality, m.position.lat, m.position.lon)
                      for e, m in reg.modules.items()},
        "parents": {e: m.route.primary for e, m in reg.modules.items()},
        "topology": svc.olympus.last_topology,
    }


def _assert(svc, cp, reg_counts, sent_routes) -> int:
    fails = []

    def check(cond, msg):
        print(("  ok   " if cond else "  FAIL ") + msg)
        if not cond:
            fails.append(msg)

    print("\n=== assertions ===")

    t3 = cp.get("t3", {})
    # 1. Registration of all six modules.
    registered = t3.get("registered", set())
    check(GPS_ANCHORS | NON_GPS <= registered,
          f"all 6 modules registered with Olympus ({len(registered & (GPS_ANCHORS|NON_GPS))}/6)")

    # 2. Ranged localization of the non-GPS modules, near their true position.
    for eui in NON_GPS:
        m = svc.reg.modules.get(eui)
        src = m.position.source if m else None
        ranged = src in (sp.PosSource.RANGED, sp.PosSource.FUSED)
        check(ranged, f"{eui} localized by ranging (source={src})")
    # Spatial sanity for delta: the ranged fix should fall inside the anchors.
    d = svc.reg.modules.get("d4d4d4d4d4d4d4d4")
    if d and d.position.valid:
        # ground truth re-derived from the sim object captured in svc? we don't
        # have it here; instead assert the fix sits within the anchor hull bbox.
        lats = [svc.reg.modules[a].position.lat for a in GPS_ANCHORS if a in svc.reg.modules and svc.reg.modules[a].position.valid]
        lons = [svc.reg.modules[a].position.lon for a in GPS_ANCHORS if a in svc.reg.modules and svc.reg.modules[a].position.valid]
        if lats:
            inside = (min(lats) - 0.002 <= d.position.lat <= max(lats) + 0.002 and
                      min(lons) - 0.002 <= d.position.lon <= max(lons) + 0.002)
            check(inside, "delta's ranged fix sits within the anchor field")

    # 3. Every online module has a parent toward the gateway.
    parents = t3.get("parents", {})
    online = t3.get("online", set())
    all_parented = all(parents.get(e) for e in online)
    check(all_parented, f"all online modules have a routing parent ({sum(1 for e in online if parents.get(e))}/{len(online)})")
    check(len(sent_routes) > 0, f"route assignments pushed downlink to the mesh ({len(sent_routes)} sent)")

    # 4. Killing anchor B flips it OFFLINE and leaves no orphans.
    t8 = cp.get("t8", {})
    check(B not in t8.get("online", set()), "anchor B is OFFLINE after kill+TTL")
    topo8 = t8.get("topology") or {}
    check(topo8.get("orphans") == [], f"no orphaned modules after failover (orphans={topo8.get('orphans')})")
    # Non-GPS modules still localized with one anchor gone (3 remain).
    for eui in NON_GPS:
        m = svc.reg.modules.get(eui)
        still = m and m.position.source in (sp.PosSource.RANGED, sp.PosSource.FUSED)
        check(bool(still), f"{eui} still localized after losing an anchor")

    # 5. Detaching A re-registers it (count went up) and the link cleared.
    check(reg_counts.get(A, 0) >= 2, f"module A re-registered after detaching from its vehicle (registrations={reg_counts.get(A,0)})")
    check(svc.reg.modules[A].attached_to == "", "module A no longer attached to a vehicle")

    # 6. Revived anchor B is back online and re-registered.
    t13 = cp.get("t13", {})
    check(B in t13.get("online", set()), "anchor B back ONLINE after revive")
    check(reg_counts.get(B, 0) >= 2, f"anchor B re-registered on revival (registrations={reg_counts.get(B,0)})")

    print("\n=== result ===")
    if fails:
        print(f"  {len(fails)} FAILED")
        return 1
    print("  ALL PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
