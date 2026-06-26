"""Optional Jetson-side sensors.

In a modular build the GPS / IMU may hang off the Jetson rather than the nRF.
This reads them (best effort, no hard dependencies) so the agent can fuse them
into the telemetry it publishes. Everything degrades to "not present" if the
hardware or daemon is missing, so the agent runs fine on a bare board too.

GPS is read from gpsd over its TCP JSON protocol (localhost:2947) when enabled;
the IMU hook is left as a clearly marked extension point since the part varies.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time

log = logging.getLogger("jetson_agent.sensors")


class GpsdSource:
    """Best-effort gpsd reader. Holds the latest fix; never raises upward."""

    def __init__(self, host: str = "127.0.0.1", port: int = 2947) -> None:
        self.host, self.port = host, port
        self._fix: tuple[float, float, float, int] | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with socket.create_connection((self.host, self.port), timeout=3) as s:
                    s.sendall(b'?WATCH={"enable":true,"json":true}\n')
                    buf = b""
                    s.settimeout(3)
                    while not self._stop.is_set():
                        chunk = s.recv(4096)
                        if not chunk:
                            break
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            self._ingest(line)
            except (OSError, socket.timeout):
                time.sleep(2)  # gpsd not up yet — retry
            except Exception as e:  # pragma: no cover
                log.debug("gpsd error: %s", e)
                time.sleep(2)

    def _ingest(self, line: bytes) -> None:
        try:
            obj = json.loads(line)
        except ValueError:
            return
        if obj.get("class") != "TPV":
            return
        lat, lon = obj.get("lat"), obj.get("lon")
        if lat is None or lon is None:
            return
        alt = obj.get("alt", 0.0)
        mode = obj.get("mode", 0)  # 2=2D, 3=3D
        quality = 200 if mode >= 3 else 150
        self._fix = (float(lat), float(lon), float(alt), quality)

    def read(self) -> tuple[float, float, float, int] | None:
        return self._fix

    def stop(self) -> None:
        self._stop.set()


class NoSensors:
    def read(self) -> None:
        return None

    def stop(self) -> None:
        pass


def make_gps_source():
    """Pick a GPS source from the environment. SWARM_JETSON_GPS=gpsd|none."""
    kind = os.environ.get("SWARM_JETSON_GPS", "none").lower()
    if kind == "gpsd":
        log.info("Jetson GPS source: gpsd")
        return GpsdSource()
    return NoSensors()
