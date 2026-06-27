# swarm-link — command-station bridge

Host-side service that turns a gateway/leader nRF's mesh traffic into Olympus
state. It is a thin **deploy wrapper** around the `olympus_link/` Python package
(registry + multilateration localization + autorouter); the logic lives there so
`jetson_agent/` and `sim/` can import the same code.

## What it does

- reads the gateway/leader nRF over USB-CDC serial (`swarm_proto`, COBS+CRC16)
- tracks every module (position, sensors, **node class**, neighbors, routes)
- localizes non-GPS modules by ranging against GPS/fused anchors
- registers modules via vehicle-api and publishes telemetry/topology to Zenoh,
  carrying the EKF fused **velocity + uncertainty** and the passive/active class

## Run it

Containerized, as part of the umbrella stack (recommended):

```bash
# on the computer the leader nRF is plugged into:
SWARM_SERIAL_PORT=/dev/ttyACM1 docker compose -f ../docker-compose.swarm.yml up
# dashboard: http://<this-host>:3000
```

Or directly (no container), against a running Olympus or a dry run:

```bash
cd ..                       # repo root
python -m olympus_link --port /dev/ttyACM1 --prefix ceres --sink rest \
       --vehicle-api http://localhost:3001
python -m olympus_link --port /dev/pts/N  --prefix ceres --sink dryrun -v   # with sim/swarm_sim.py
```

Config is all `SWARM_*` env vars — see `olympus_link/config.py`.
