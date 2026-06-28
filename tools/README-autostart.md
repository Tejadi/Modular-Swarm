# Boot autostart (mobile modules)

Each Jetson powers up, figures out whether it's the **leader** (command station)
or a **member**, and launches the right stack — no manual SSH.

```
power on
  -> swarm-node.service runs tools/swarm_boot.py
       -> finds the nRF (/dev/ttyACM*)
       -> reads its HELLO: GATEWAY/LEADER flag set?  -> leader : member
  -> leader  : python3 -m olympus_link   (mesh -> zenoh / dashboard bridge)
  -> member  : python3 -m jetson_agent   (brain; auto-detects camera -> scout/executor)
```

Role comes from the **nRF itself** (the firmware build is the source of truth) —
the leader/gateway nRF advertises the GATEWAY/LEADER flag, members don't. You can
pin it with `SWARM_ROLE=leader|member` if you prefer.

## Install (once per Jetson)

```
sudo tools/install-autostart.sh
sudo nano /etc/default/swarm-node     # set endpoints, anchor, etc.
sudo systemctl start swarm-node.service
journalctl -u swarm-node -f           # watch it pick a role + launch
```

`install-autostart.sh` writes `/etc/default/swarm-node`, generates the systemd
units with this machine's repo path / user / python, and enables `swarm-node`
so it starts on every boot (`Restart=always`).

## Scouts (camera) — also enable perception

```
sudo systemctl enable --now swarm-perception.service
```

Runs the YOLO people detector (`people_detect.py`) forever, publishing
depth-projected detections to the map. It **exits cleanly if no camera** is
present, so it's harmless to enable on executors too (it just re-checks on
restart, catching a hot-plugged camera). Set `SWARM_YOLO_ONNX` to the model and,
for a moving scout, `SWARM_NODE_EUI` so detection geo tracks the live pose.

## Config — `/etc/default/swarm-node`

| var | meaning |
|-----|---------|
| `SWARM_ROLE` | `auto` (detect from nRF) / `leader` / `member` |
| `SWARM_NRF_PORT` | `auto` (first ACM) or an explicit device |
| `SWARM_ZENOH_REST` / `SWARM_VEHICLE_API` | command-station endpoints |
| `SWARM_STATION_LAT` / `_LON` | leader map anchor until GPS lock |
| `SWARM_YOLO_ONNX` | scout: YOLO model path |
| `SWARM_NODE_EUI` | scout: node id for detection labels + live pose |

## Check role without launching

```
python3 tools/swarm_boot.py --probe      # prints ROLE=... PORT=... and exits
```
