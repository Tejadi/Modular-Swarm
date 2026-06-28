#!/usr/bin/env bash
# Install the swarm autostart systemd services on a Jetson.
# Run as root:   sudo tools/install-autostart.sh
#
# Installs:
#   /etc/default/swarm-node              (config, from the example; kept if present)
#   /etc/systemd/system/swarm-node.service       (role launcher -> leader|member)
#   /etc/systemd/system/swarm-perception.service (scout YOLO detector; opt-in)
# and enables swarm-node so it starts on every boot.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root: sudo $0" >&2
  exit 1
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$(command -v python3)"
RUN_USER="${SUDO_USER:-root}"
echo "repo=$REPO  python=$PY  user=$RUN_USER"

# 1. config
if [ -f /etc/default/swarm-node ]; then
  echo "kept existing /etc/default/swarm-node"
else
  install -m 0644 "$REPO/tools/swarm-node.env.example" /etc/default/swarm-node
  echo "installed /etc/default/swarm-node  (edit it per module)"
fi

# 2. units (substitute repo/user/python into the templates)
gen() {
  sed -e "s|@REPO@|$REPO|g" -e "s|@USER@|$RUN_USER|g" -e "s|@PY@|$PY|g" \
      "$REPO/tools/$1" > "/etc/systemd/system/$1"
  echo "installed /etc/systemd/system/$1"
}
gen swarm-node.service
gen swarm-perception.service

# 3. enable
systemctl daemon-reload
systemctl enable swarm-node.service
echo
echo "swarm-node.service enabled -- starts on boot, auto-detects leader/member."
echo "  start now : sudo systemctl start swarm-node.service"
echo "  watch     : journalctl -u swarm-node -f"
echo
echo "On a scout (camera) also run:"
echo "  sudo systemctl enable --now swarm-perception.service"
