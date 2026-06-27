#!/usr/bin/env bash
#
# Run THIS machine as the swarm COMMAND STATION (the "command module" host).
#
# It:
#   - reads the attached LEADER nRF over USB-CDC (default /dev/ttyACM0, the data
#     port; /dev/ttyACM0 is the console),
#   - brings up Olympus (zenoh-router, vehicle-api, dashboard) + the swarm-link
#     bridge that feeds the mesh into Olympus,
#   - serves the GUI at http://localhost:3000.
#
# Prereqs:
#   * the attached nRF is flashed with the LEADER build:
#       (cd swarm_node && ./build.sh --leader --flash)
#   * Docker + Docker Compose v2.20+
#   * your user is in the 'dialout' group (serial access): sudo usermod -aG dialout "$USER"
#
# Override the serial port / instance / full-stack via env:
#   SWARM_SERIAL_PORT=/dev/ttyACM0 OLYMPUS_INSTANCE=ceres ./run-command-station.sh
#   FULL=1 ./run-command-station.sh        # also start ollama + brain + advisor
set -euo pipefail

cd "$(dirname "$0")"

PORT="${SWARM_SERIAL_PORT:-/dev/ttyACM0}"
INSTANCE="${OLYMPUS_INSTANCE:-ceres}"
COMPOSE="docker-compose.swarm.yml"

# Light subset (map + telemetry) by default; FULL=1 brings up the whole brain.
SERVICES="zenoh-router vehicle-api dashboard swarm-link"
[ "${FULL:-0}" = "1" ] && SERVICES=""

[ -f "$COMPOSE" ]      || { echo "!!! $COMPOSE not found (run from the repo root)." >&2; exit 1; }
[ -d "../olympus" ]    || { echo "!!! ../olympus not found — the compose includes it." >&2; exit 1; }
if [ ! -e "$PORT" ]; then
  echo "!!! Leader serial port $PORT not found. Plug in the leader nRF (flashed with" >&2
  echo "    ./build.sh --leader) or set SWARM_SERIAL_PORT. Available ports:" >&2
  ls /dev/ttyACM* 2>/dev/null || echo "    (no /dev/ttyACM* present)" >&2
  exit 1
fi

echo ">>> Command station: leader nRF on $PORT, instance=$INSTANCE"
echo ">>> GUI: http://localhost:3000   (from another machine: http://<this-ip>:3000)"
echo ">>> Services: ${SERVICES:-ALL}"

exec env SWARM_SERIAL_PORT="$PORT" OLYMPUS_INSTANCE="$INSTANCE" \
     docker compose -f "$COMPOSE" up $SERVICES
