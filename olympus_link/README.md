# olympus_link

Command-station bridge between the nRF swarm and Olympus. Runs on the machine
wired to the gateway nRF. Reads the mesh over serial, keeps the live module
registry, localizes non-GPS modules, runs the overlay autorouter, and pushes the
whole picture into Olympus over the network — no Olympus source is imported or
modified.

## What it does each tick

1. **Liveness** — modules silent past their TTL flip OFFLINE; a final status is
   pushed and routes recompute around them.
2. **Registration** — a module that just announced (or was reconfigured) is
   registered via `POST /api/v1/vehicles/register`. Contributors register as
   `partner`, pure consumers as `observer` (no command authority). Sensors and
   the module↔vehicle link ride in `capabilities`, so no Olympus model change is
   needed.
3. **Localization** — GPS modules are anchors; IMU-only / rangefinder modules
   are multilaterated from their RTT/RSSI ranges to the anchors (least squares),
   fused with any IMU dead-reckoning estimate.
4. **Autorouting** — a link-quality-weighted shortest-path tree to the gateway
   with a redundant secondary parent per module, plus provider→consumer
   subscriptions. Re-parent hysteresis prevents flapping; orphans fall back to
   multicast announce. Route assignments are pushed back down the mesh.
5. **Telemetry + topology** — each online module is published to
   `{prefix}/swarm/{id}/telemetry` (the key the dashboard already renders) and
   the full topology to `{prefix}/swarm/topology`.

## Run

```bash
# against the simulator (no hardware):
python sim/swarm_sim.py                 # prints a /dev/pts/N path
python -m olympus_link --port /dev/pts/N --prefix ceres --sink rest

# against a real gateway nRF:
python -m olympus_link --port /dev/ttyACM0 --prefix ceres \
    --vehicle-api http://localhost:3001
```

Set `SWARM_VEHICLE_API_KEY` if vehicle-api auth is enabled (`Authorization:
Bearer`). The `--prefix` must match the running dashboard instance (the bundled
dashboard polls `ceres/**`; the Rust bridge default is `olympus`).

## Dependencies

Stdlib only. `--sink rest` (default) publishes via the Zenoh REST endpoint and
registers via vehicle-api with `urllib`. `--sink zenoh` uses the `zenoh` Python
lib if installed (falls back to REST). `--sink dryrun` records pushes in memory
for testing.

## Test

```bash
python sim/test_e2e.py     # full pipeline over a virtual clock, dryrun sink
```
