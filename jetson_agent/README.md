# jetson_agent

Per-agent companion. Runs on each agent's Jetson next to its local nRF module's
data port. Gives the command center a **redundant IP path** to the RF gateway:
an agent on WiFi/5G stays visible even if the mesh backhaul degrades.

## What it does

- Reads its module's own announce / telemetry / neighbor messages off the nRF
  serial link (the firmware mirrors them there).
- Fuses any Jetson-side GPS/IMU. If GPS lives on the Jetson rather than the nRF,
  its fix is folded in and `gps` is added to the module's advertised sensors.
- Registers the module with Olympus and streams its telemetry over IP
  (`{prefix}/swarm/{id}/telemetry`), redundant to the gateway path.
- Forwards operator commands down to the nRF over the same serial link.

Routing stays central on the command station; the agent only handles its own
module.

## Run

```bash
# nRF data port is the second CDC-ACM the firmware exposes (the first is the ot shell)
python -m jetson_agent --port /dev/ttyACM1 --prefix ceres \
    --vehicle-api http://command-station:3001

# enable a Jetson-side GPS via gpsd:
SWARM_JETSON_GPS=gpsd python -m jetson_agent --port /dev/ttyACM1 --prefix ceres
```

## Dependencies

Stdlib only. Reuses `olympus_link`'s model + Olympus client and `proto/`'s
codec (both in this repo). GPS is read from `gpsd` over its TCP JSON protocol
when `SWARM_JETSON_GPS=gpsd`; otherwise no Jetson sensors are assumed.
