"""nrf_link.py — Jetson-side reader for the nRF coap_server JSON data link.

This is the production counterpart to the bench script jetson_mimic.py, packaged
into the jetson_agent runtime. It speaks the newline-delimited JSON contract
defined in coap_server/src/protocol.h:

    nRF  -> Jetson : {"type":"manifest","schema":1,"node":"...","sensors":[...]}
    nRF  -> Jetson : {"type":"data","schema":1,"id":"gps0","ts_ms":..,"value":{..}}
    Jetson -> nRF  : {"type":"ack","schema":1}

The nRF is the USB device; the Jetson is the host. Opening the port asserts DTR,
which the firmware watches to (re)announce its capability manifest. We ACK each
manifest so the firmware's announce/retry/backoff loop terminates and it begins
streaming, and we re-ACK on every reconnect (the firmware re-announces on each
fresh DTR assert). Streamed samples are dispatched to callbacks; the latest
value per sensor is also cached.

Run standalone (prints every frame and ACKs, exactly like jetson_mimic.py):

    python -m jetson_agent.nrf_link                 # /dev/ttyACM0
    python -m jetson_agent.nrf_link /dev/ttyACM1

Requires pyserial (pip install pyserial).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Callable, Optional

import serial  # pyserial

log = logging.getLogger("jetson_agent.nrf_link")

# Mirror of coap_server/src/protocol.h — keep in lockstep with the firmware.
PROTO_SCHEMA_VERSION = 1
PROTO_TYPE_MANIFEST = "manifest"
PROTO_TYPE_DATA = "data"
PROTO_TYPE_ACK = "ack"

DEFAULT_PORT = os.environ.get("SWARM_NRF_PORT", "/dev/ttyACM0")
DEFAULT_BAUD = 115200  # nominal only; USB-CDC ignores the baud value

# Callback signatures
ManifestCb = Callable[[dict], None]
DataCb = Callable[[str, dict, dict], None]  # (sensor_id, value, full_frame)


class NrfLink:
    """Newline-delimited JSON link to one nRF module over USB-CDC.

    Usage (embedded):
        link = NrfLink("/dev/ttyACM0", on_data=my_handler)
        link.start()        # background thread, auto-reconnects
        ...
        link.stop()

    Or blocking:
        NrfLink("/dev/ttyACM0").run()
    """

    def __init__(
        self,
        port: str = DEFAULT_PORT,
        baud: int = DEFAULT_BAUD,
        on_manifest: Optional[ManifestCb] = None,
        on_data: Optional[DataCb] = None,
        schema: int = PROTO_SCHEMA_VERSION,
        reconnect_s: float = 2.0,
    ) -> None:
        self.port = port
        self.baud = baud
        self.schema = schema
        self.reconnect_s = reconnect_s
        self._on_manifest = on_manifest
        self._on_data = on_data

        # Live view of the module, populated from the manifest + stream.
        self.node: Optional[str] = None
        self.sensors: dict[str, dict] = {}     # id -> descriptor
        self.latest: dict[str, dict] = {}      # id -> last value object

        self._ser: Optional[serial.Serial] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Run the read loop on a daemon thread (auto-reconnects)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, name="nrf_link", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=2.0)

    def run(self) -> None:
        """Blocking read loop. Opens the port, reconnecting until stopped."""
        log.info("nrf_link: data port=%s", self.port)
        while not self._stop.is_set():
            try:
                self._session()
            except serial.SerialException as e:
                log.warning("serial error on %s: %s (retrying in %.1fs)",
                            self.port, e, self.reconnect_s)
            except Exception:
                log.exception("nrf_link session crashed (retrying)")
            finally:
                self._ser = None
            if self._stop.wait(self.reconnect_s):
                break

    # --- internals ---------------------------------------------------------

    def _session(self) -> None:
        # Opening the port asserts DTR -> the firmware (re)announces its manifest.
        self._ser = serial.Serial(self.port, self.baud, timeout=1)
        self._ser.dtr = True
        log.info("connected to %s; waiting for manifest", self.port)

        while not self._stop.is_set():
            raw = self._ser.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                log.debug("non-JSON line: %s", line)
                continue
            if isinstance(frame, dict):
                self._dispatch(frame)

    def _dispatch(self, frame: dict) -> None:
        ftype = frame.get("type")
        if ftype == PROTO_TYPE_MANIFEST:
            self._handle_manifest(frame)
        elif ftype == PROTO_TYPE_DATA:
            self._handle_data(frame)
        else:
            log.debug("unknown frame type %r: %s", ftype, frame)

    def _handle_manifest(self, frame: dict) -> None:
        self.node = frame.get("node")
        self.sensors = {s.get("id"): s for s in frame.get("sensors", []) if "id" in s}
        ids = ", ".join(self.sensors) or "(none)"
        log.info("manifest node=%s schema=%s sensors=[%s]",
                 self.node, frame.get("schema"), ids)
        self._send_ack(frame)
        if self._on_manifest:
            try:
                self._on_manifest(frame)
            except Exception:
                log.exception("on_manifest callback failed")

    def _handle_data(self, frame: dict) -> None:
        sid = frame.get("id")
        value = frame.get("value", {})
        if sid is None:
            return
        self.latest[sid] = value
        log.debug("data %s ts=%s %s", sid, frame.get("ts_ms"), value)
        if self._on_data:
            try:
                self._on_data(sid, value, frame)
            except Exception:
                log.exception("on_data callback failed")

    def _send_ack(self, manifest: dict) -> None:
        # protocol.h: the firmware accepts any object containing "ack"; we send
        # the canonical {"type":"ack","schema":N} and echo node for traceability.
        ack = {
            "type": PROTO_TYPE_ACK,
            "schema": manifest.get("schema", self.schema),
            "node": manifest.get("node"),
        }
        self._ser.write((json.dumps(ack) + "\n").encode("utf-8"))
        self._ser.flush()
        log.info("-> ACK schema=%s node=%s", ack["schema"], ack["node"])


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="nRF JSON data-link reader (coap_server firmware)")
    ap.add_argument("port", nargs="?", default=DEFAULT_PORT,
                    help="nRF USB-CDC data port (default %(default)s)")
    ap.add_argument("baud", nargs="?", type=int, default=DEFAULT_BAUD,
                    help="nominal baud (ignored by USB-CDC)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="log streamed data frames too")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    link = NrfLink(args.port, args.baud)
    try:
        link.run()
    except KeyboardInterrupt:
        print()
    finally:
        link.stop()


if __name__ == "__main__":
    main()
