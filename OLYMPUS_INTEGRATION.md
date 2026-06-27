# Olympus Integration — Reconfigurable nRF Swarm

This adds an Olympus command-center connection to the nRF swarm. Each agent is a
Jetson + nRF module carrying any subset of a modular sensor stack (GPS, IMU, or
neither). A module is the unit of identity: it can ride a vehicle or operate
standalone (ad-hoc, reconfigurable). When a module comes online the command
station records its position, its available sensors, and whether it contributes
data to the swarm or only consumes swarm information — and shows a live view of
every module. An overlay autorouter connects the modules robustly on top of
OpenThread's own mesh routing.

Everything new lives in this repo; Olympus is reached over the network (its
existing Zenoh keys + registration endpoint), so a module appears in the command
center with no Olympus code change.

## Layout

```
proto/          swarm_protocol.h (C)  + swarm_proto.py  — one wire format, both sides
swarm_node/     Zephyr/C firmware: node + gateway builds (OpenThread + CoAP overlay)
jetson_agent/   per-agent Python companion (redundant IP path, Jetson sensor fusion)
olympus_link/   command-station Python service (registry, localization, autorouter, push)
sim/            PTY swarm simulator + automated end-to-end test (no hardware needed)
```

## Data flow

```
 Agent (xN)                          RF mesh            Command station (Olympus host)
 GPS/IMU -> jetson_agent  --USB-CDC--  nRF (swarm_node) --OT/CoAP--> gateway nRF --USB-CDC--> olympus_link
            |  (redundant IP: Zenoh + vehicle-api over WiFi/5G)  ----------------------------^   |
            v                                                                                    v
        Olympus Zenoh  <--------------------------------- telemetry / topology / registry  ------+
        Olympus dashboard renders swarm/{id}/telemetry (existing key)
```

OpenThread handles L2/L3 mesh routing and self-healing. Telemetry reaches the
station two ways: realm-local multicast flooded to the gateway nRF, and (when an
agent has IP) the Jetson agent publishing directly. The autorouter is an
application overlay that assigns roles, aggregation parents, and provider→
consumer subscriptions, and pushes them back down the mesh.

## The protocol: current vs upgraded

Current (stock sample): a single `PUT /light` (1 byte) plus a one-shot
`GET /provisioning`, non-confirmable, no sensors, no liveness.

Upgraded overlay (`proto/swarm_protocol.h`), kept alongside the legacy resources:

| Resource    | Dir | Purpose |
|-------------|-----|---------|
| `swm/hello` | up  | periodic descriptor: id, role (provider/consumer/relay), mount, sensor bitmap, battery — the "module online" event |
| `swm/tlm`   | up  | position (gps/ranged/imu/fused) + heading + sensor readings + status |
| `swm/nbr`   | up  | neighbor link table (RSSI + RTT range + quality) — feeds the autorouter |
| `swm/rng`   | p2p | two-way RTT ranging for GPS-denied localization |
| `swm/rte`   | down| route assignment: primary + secondary parent + subscriptions |
| `swm/cmd`   | down| command channel (set role/mount/rate, identify, reboot) |

Up messages are non-confirmable realm-local multicast (flooded mesh-wide to the
gateway); downlink is confirmable unicast (ACKed). Liveness is a `hello`
heartbeat + last-seen TTL.

Localization: the nRF52840 cannot do true time-of-flight, so range is an RTT +
RSSI path-loss estimate. The station multilaterates non-GPS modules against GPS
anchors (least squares) and fuses IMU dead-reckoning when only the IMU exists.
Accuracy is coarse (meters–tens of meters) and surfaced as a position quality.

## Run it end-to-end (no hardware)

```bash
# 1. automated check of the whole command-station pipeline
python sim/test_e2e.py

# 2. live, against a real Olympus + dashboard:
python sim/swarm_sim.py                     # prints a /dev/pts/N device
python -m olympus_link --port /dev/pts/N --prefix ceres --sink rest
#    open the Olympus dashboard — modules appear on the map as they "power on",
#    move, drop out (failover), and reconfigure.
```

`--prefix` must match the dashboard instance (bundled dashboard polls `ceres/**`;
the Rust bridge default is `olympus`).

## On hardware

```bash
cd swarm_node
./build.sh                 # plain node (provider+relay)   -> flash >= 2
./build.sh --gateway       # command-station gateway       -> flash 1, wire to host
# then on the host:
python -m olympus_link --port /dev/ttyACM0 --prefix ceres
# and on each agent's Jetson:
python -m jetson_agent --port /dev/ttyACM1 --prefix ceres --vehicle-api http://host:3001
```

Role / mount / sensors are Kconfig + devicetree (see `swarm_node/README.rst`).

## Olympus footprint

None required. `olympus_link` registers via the existing `register_vehicle`
endpoint and publishes to the existing `swarm/{id}/telemetry` key, which the
dashboard already renders. The telemetry payload also carries a richer `swarm`
object (sensor manifest, provider/consumer, mount + attached vehicle, neighbors,
position source/quality, parent/subscriptions) for an optional swarm panel —
add a card following `dashboard/src/components/FleetPanel.jsx` and draw mesh
links/routing tree with `PolylineGraphics` if you want the detailed view.

## Verification status

- `proto`: C/Python CRC16 + header packing verified byte-identical.
- `olympus_link` + `sim`: end-to-end test passes (registration, ranged
  localization, routing for all modules, failover with no orphans,
  reconfiguration re-registration, revival) over both a direct feed and a real
  PTY serial link.
- `jetson_agent`: identifies its module, fuses Jetson GPS, registers + publishes.
- `swarm_node` firmware: written against the nRF Connect SDK / OpenThread native
  CoAP API; build on hardware with `swarm_node/build.sh`.
