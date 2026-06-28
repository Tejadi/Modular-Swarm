#!/usr/bin/env bash
#
# Build the reconfigurable swarm module for the Seeed XIAO nRF52840 with the
# nRF Connect SDK (NCS v3.3.1).
#
# Usage:
#   ./build.sh                    # plain swarm node (provider+relay)
#   ./build.sh --gateway          # command-station gateway build
#   ./build.sh --leader           # base-station leader build
#   ./build.sh --pristine         # clean build
#   ./build.sh --flash            # copy the UF2 to a mounted XIAO drive
#   ./build.sh --board nrf52840dk/nrf52840
#
# The NCS install is located automatically: a nrfutil toolchain-manager bundle
# (default ~/ncs) is used via `nrfutil toolchain-manager launch` — self-contained,
# no system packages needed; otherwise a system install under ~/ncs or
# /opt/nordic/ncs is used directly. Override the SDK root with NCS_ROOT=/path.
#
# Flags combine, e.g. ./build.sh --leader --pristine --flash
set -euo pipefail

NCS_VERSION="v3.3.1"

BOARD="xiao_ble"
PRISTINE=""
DO_FLASH=0
EXTRA=""
while [ $# -gt 0 ]; do
  case "$1" in
    --pristine) PRISTINE="-p always" ;;
    --flash)    DO_FLASH=1 ;;
    --gateway)  EXTRA="-DEXTRA_CONF_FILE=overlay-gateway.conf" ;;
    --leader)   EXTRA="-DEXTRA_CONF_FILE=overlay-leader.conf" ;;
    --board)    shift; BOARD="$1" ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
  shift
done

cd "$(dirname "$0")"
APP="$PWD"

NRFUTIL="$(command -v nrfutil || true)"
[ -z "$NRFUTIL" ] && [ -x "$HOME/.local/bin/nrfutil" ] && NRFUTIL="$HOME/.local/bin/nrfutil"

# Self-contained toolchain bundle (no system deps): run west inside `launch`.
build_via_nrfutil() {
  local root
  root="$("$NRFUTIL" toolchain-manager config --show 2>/dev/null | sed -n 's/^Install directory: *//p')"
  [ -n "$root" ] && [ -d "$root/$NCS_VERSION/zephyr" ] || return 1
  echo ">>> Building via nrfutil toolchain-manager ($NCS_VERSION, $root)"
  # shellcheck disable=SC2086
  "$NRFUTIL" toolchain-manager launch --ncs-version "$NCS_VERSION" -- bash -c \
    "set -e; export ZEPHYR_BASE='$root/$NCS_VERSION/zephyr'; cd '$APP'; \
     west build -b '$BOARD' $PRISTINE . -- $EXTRA"
}

# System NCS install: set up PATH + ZEPHYR env explicitly, then build.
build_via_paths() {
  local root="" tc=""
  for r in "${NCS_ROOT:-}" "$HOME/ncs" /opt/nordic/ncs; do
    [ -n "$r" ] && [ -d "$r/$NCS_VERSION/zephyr" ] && { root="$r"; break; }
  done
  [ -n "$root" ] || { echo "NCS $NCS_VERSION not found (set NCS_ROOT, or install via nrfutil)." >&2; exit 1; }
  tc="$(ls -d "$root"/toolchains/*/ 2>/dev/null | head -1)"; tc="${tc%/}"
  [ -d "$tc" ] || { echo "No toolchain under $root/toolchains" >&2; exit 1; }
  echo ">>> Building via $root (toolchain $tc)"
  export PATH="$tc/bin:$tc/usr/bin:$tc/usr/local/bin:$tc/opt/bin:$tc/opt/zephyr-sdk/arm-zephyr-eabi/bin:$PATH"
  export ZEPHYR_TOOLCHAIN_VARIANT="zephyr" ZEPHYR_SDK_INSTALL_DIR="$tc/opt/zephyr-sdk"
  # shellcheck disable=SC1091
  source "$root/$NCS_VERSION/zephyr/zephyr-env.sh"
  # shellcheck disable=SC2086
  west build -b "$BOARD" $PRISTINE "$APP" -- $EXTRA
}

echo ">>> swarm_node: board=$BOARD ${EXTRA:+overlay=${EXTRA#*=}}"
if [ -n "$NRFUTIL" ] && "$NRFUTIL" toolchain-manager list 2>/dev/null | grep -q "$NCS_VERSION"; then
  build_via_nrfutil || build_via_paths
else
  build_via_paths
fi

UF2="build/swarm_node/zephyr/zephyr.uf2"
echo ">>> Build complete: $UF2"

if [ "$DO_FLASH" -eq 1 ]; then
  # Bootloader mass-storage mount: macOS /Volumes, Linux /media|/run/media|/mnt.
  DRIVE="$(ls -d /Volumes/XIAO* /media/"$USER"/XIAO* /run/media/"$USER"/XIAO* \
              /media/XIAO* /mnt/XIAO* 2>/dev/null | head -1 || true)"
  if [ -z "$DRIVE" ]; then
    echo "!!! No XIAO drive mounted. Double-tap RESET, then re-run with --flash." >&2
    exit 1
  fi
  echo ">>> Flashing to $DRIVE"
  cp "$UF2" "$DRIVE/"
  sync
  echo ">>> Done. The board will reboot."
fi
