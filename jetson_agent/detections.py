"""Best-effort reader for the perception process's detections (UDP localhost).

Non-blocking: poll() drains any datagrams and returns the latest detection list,
or an empty list if the perception process isn't running. The detections feed
mission decisions (search prioritisation) and are published to Olympus as
olympus/detection/** — but a dead perception process never stalls the agent.
"""

from __future__ import annotations

import json
import logging
import socket

log = logging.getLogger("jetson_agent.detections")

DEFAULT_PORT = 47800


class DetectionReader:
    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> None:
        self.latest: list = []
        self.ts: float = 0.0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind((host, port))
            self.sock.setblocking(False)
            self._ok = True
        except OSError as e:
            log.warning("detection reader bind failed (%s); perception disabled", e)
            self._ok = False

    def poll(self) -> list:
        """Drain pending datagrams; return the most recent detection list."""
        if not self._ok:
            return []
        while True:
            try:
                data, _ = self.sock.recvfrom(65535)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                break
            try:
                msg = json.loads(data)
                self.latest = msg.get("items", [])
                self.ts = msg.get("ts", 0.0)
            except (ValueError, AttributeError):
                continue
        return self.latest

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass
