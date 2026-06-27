#!/usr/bin/env bash
#
# Build the reconfigurable swarm module for the Seeed XIAO nRF52840 using the
# nRF Connect SDK toolchain installed by the VS Code extension.
#
# Usage:
#   ./build.sh                    # plain swarm node (provider+relay)
#   ./build.sh --gateway          # command-station gateway build
#   ./build.sh --pristine         # clean build
#   ./build.sh --flash            # copy the UF2 to a mounted XIAO drive
#   ./build.sh --board nrf52840dk_nrf52840
#
# Flags combine, e.g. ./build.sh --gateway --pristine --flash
set -euo pipefail

NCS_VERSION="v3.3.1"
TC="/opt/nordic/ncs/toolchains/0c0f19d91c"
ZEPHYR="/opt/nordic/ncs/${NCS_VERSION}/zephyr"

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

[ -d "$TC" ]     || { echo "Toolchain not found at $TC" >&2; exit 1; }
[ -d "$ZEPHYR" ] || { echo "Zephyr not found at $ZEPHYR" >&2; exit 1; }

export PATH="$TC/bin:$TC/usr/bin:$TC/usr/local/bin:$TC/opt/bin:$TC/opt/nanopb/generator-bin:$TC/nrfutil/bin:$TC/opt/zephyr-sdk/arm-zephyr-eabi/bin:$TC/opt/zephyr-sdk/riscv64-zephyr-elf/bin:$PATH"
export NRFUTIL_HOME="$TC/nrfutil/home"
export ZEPHYR_TOOLCHAIN_VARIANT="zephyr"
export ZEPHYR_SDK_INSTALL_DIR="$TC/opt/zephyr-sdk"
# shellcheck disable=SC1091
source "$ZEPHYR/zephyr-env.sh"

cd "$(dirname "$0")"
echo ">>> Building swarm_node for board: $BOARD ${EXTRA:+(gateway)}"
# shellcheck disable=SC2086
west build -b "$BOARD" $PRISTINE . -- $EXTRA

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
