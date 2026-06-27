"""Perception process — object detection, decoupled from the EKF/serial loop.

Runs YOLO on a camera and publishes detections as JSON UDP datagrams to
127.0.0.1 (fire-and-forget; the agent reads them best-effort via
jetson_agent/detections.py). Keeping it a SEPARATE process means GPU inference
stalls never block the 1 Hz fusion/serial loop.

YOLO (ultralytics) is optional — `--fake` emits synthetic detections so the
pipeline is testable with no model or camera.
"""

from __future__ import annotations

import json
import logging
import math
import socket
import time

log = logging.getLogger("jetson_perception")

DEFAULT_PORT = 47800


class DetectionPublisher:
    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> None:
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def publish(self, items: list, ts: "float | None" = None) -> None:
        msg = {"ts": ts if ts is not None else time.time(), "items": items}
        try:
            self.sock.sendto(json.dumps(msg).encode(), self.addr)
        except OSError as e:
            log.debug("detection publish failed: %s", e)

    def close(self) -> None:
        self.sock.close()


def _detection(cls: str, conf: float, bbox: list, bearing_deg: float,
               range_m: float) -> dict:
    """Normalised detection. bbox = [x, y, w, h] in pixels; bearing relative to
    the vehicle heading; range from depth (or estimated)."""
    return {"cls": cls, "conf": round(conf, 3), "bbox": bbox,
            "bearing_deg": round(bearing_deg, 1), "range_m": round(range_m, 1)}


def run_fake(port: int = DEFAULT_PORT, hz: float = 2.0, stop=None) -> None:
    """Emit synthetic detections (for the test / a no-camera bench)."""
    import random
    pub = DetectionPublisher(port=port)
    classes = ["person", "vehicle", "debris", "marker"]
    i = 0
    try:
        while stop is None or not stop():
            i += 1
            n = i % 3
            items = [_detection(random.choice(classes), random.uniform(0.5, 0.99),
                                [random.randint(0, 600), random.randint(0, 400), 40, 60],
                                random.uniform(-60, 60), random.uniform(2, 40))
                     for _ in range(n)]
            pub.publish(items)
            time.sleep(1.0 / hz)
    finally:
        pub.close()


def run_yolo(model_path: str, source: str = "0", port: int = DEFAULT_PORT,
             conf: float = 0.4, stop=None) -> None:
    """Run an Ultralytics YOLO model on `source` and publish detections.

    Needs a Jetson with ultralytics + a camera; cannot run in CI. Range/bearing
    are crude pixel-geometry estimates here — fuse with RealSense depth for real
    ranges in a production build.
    """
    from ultralytics import YOLO   # raises if ultralytics absent
    pub = DetectionPublisher(port=port)
    model = YOLO(model_path)
    names = model.names
    try:
        for result in model.predict(source=source, stream=True, conf=conf, verbose=False):
            if stop is not None and stop():
                break
            w = result.orig_shape[1] if result.orig_shape else 640
            items = []
            for b in result.boxes:
                x1, y1, x2, y2 = (float(v) for v in b.xyxy[0])
                cx = (x1 + x2) / 2.0
                bearing = (cx / w - 0.5) * 70.0           # ~70 deg HFOV
                items.append(_detection(names[int(b.cls)], float(b.conf),
                                        [int(x1), int(y1), int(x2 - x1), int(y2 - y1)],
                                        bearing, 0.0))
            pub.publish(items)
    finally:
        pub.close()
