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

---

## JSON link (`coap_server` firmware): `nrf_link.py`

The `agent.py` path above speaks the **binary** `swarm_proto` codec used by the
`swarm_node` gateway firmware. The `coap_server` firmware instead exposes a
**single** USB-CDC port carrying **newline-delimited JSON** (its shell is on RTT,
not USB). `nrf_link.NrfLink` is the consumer for that firmware — the production
counterpart to the bench script `jetson_mimic.py`.

It implements the capability-discovery handshake from
`coap_server/src/protocol.h`: opening the port asserts DTR → the firmware
announces a `manifest` → we reply with an `ack` → the firmware streams `data`
frames. It re-ACKs on every reconnect and caches the latest value per sensor.

```bash
# nRF board is on /dev/ttyACM0 (single CDC port; shell lives on RTT)
python -m jetson_agent.nrf_link                  # defaults to /dev/ttyACM0
python -m jetson_agent.nrf_link /dev/ttyACM0 -v  # also log streamed data frames

# override the default port via the environment (shared with the rest of the pkg)
SWARM_NRF_PORT=/dev/ttyACM0 python -m jetson_agent.nrf_link
```

Embed it in a larger runtime via callbacks:

```python
from jetson_agent import NrfLink

def on_data(sensor_id, value, frame):
    if sensor_id == "gps0" and value.get("fix", 0) > 0:
        print(value["lat"], value["lon"])

link = NrfLink("/dev/ttyACM0", on_data=on_data)
link.start()        # background thread, auto-reconnects
# link.latest["gps0"] holds the most recent fix; link.stop() to shut down
```

Requires `pyserial` (`pip install pyserial`).
