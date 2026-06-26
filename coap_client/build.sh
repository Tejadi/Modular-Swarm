#!/usr/bin/env bash
#
# Build the OpenThread CoAP client sample for the Seeed XIAO nRF52840
# using the nRF Connect SDK toolchain installed by the VS Code extension.
#
# Console/shell/log are routed to USB CDC ACM via boards/xiao_ble.overlay
# and boards/xiao_ble.conf (auto-applied for board xiao_ble) -- open the
# board's USB serial port after flashing to see boot + Thread output.
#
# Usage:
#   ./build.sh                 # incremental build for xiao_ble
#   ./build.sh --pristine      # clean (pristine) build
#   ./build.sh --flash         # build, then copy the UF2 to a mounted XIAO drive
#
# Flags can be combined, e.g. ./build.sh --pristine --flash
set -euo pipefail

# --- locations (edit if you install a different SDK/toolchain version) ---
NCS_VERSION="v3.3.1"
TC="/opt/nordic/ncs/toolchains/0c0f19d91c"
ZEPHYR="/opt/nordic/ncs/${NCS_VERSION}/zephyr"

# --- parse flags ---
BOARD="xiao_ble"
PRISTINE=""
DO_FLASH=0
for arg in "$@"; do
  case "$arg" in
    --pristine) PRISTINE="-p always" ;;
    --flash)    DO_FLASH=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

# --- sanity checks ---
[ -d "$TC" ]     || { echo "Toolchain not found at $TC" >&2; exit 1; }
[ -d "$ZEPHYR" ] || { echo "Zephyr not found at $ZEPHYR" >&2; exit 1; }

# --- toolchain environment (from $TC/environment.json) ---
export PATH="$TC/bin:$TC/usr/bin:$TC/usr/local/bin:$TC/opt/bin:$TC/opt/nanopb/generator-bin:$TC/nrfutil/bin:$TC/opt/zephyr-sdk/arm-zephyr-eabi/bin:$TC/opt/zephyr-sdk/riscv64-zephyr-elf/bin:$PATH"
export NRFUTIL_HOME="$TC/nrfutil/home"
export ZEPHYR_TOOLCHAIN_VARIANT="zephyr"
export ZEPHYR_SDK_INSTALL_DIR="$TC/opt/zephyr-sdk"
# shellcheck disable=SC1091
source "$ZEPHYR/zephyr-env.sh"

# --- build (run from the directory of this script) ---
cd "$(dirname "$0")"
echo ">>> Building coap_client for board: $BOARD"
west build -b "$BOARD" $PRISTINE .

UF2="build/coap_client/zephyr/zephyr.uf2"
echo ">>> Build complete: $UF2"

# --- optional flash via UF2 bootloader ---
if [ "$DO_FLASH" -eq 1 ]; then
  DRIVE="$(ls -d /Volumes/XIAO* 2>/dev/null | head -1 || true)"
  if [ -z "$DRIVE" ]; then
    echo "!!! No XIAO drive mounted. Double-tap RESET on the board, then re-run with --flash." >&2
    exit 1
  fi
  echo ">>> Flashing to $DRIVE"
  cp "$UF2" "$DRIVE/"
  echo ">>> Done. The board will reboot."
fi
