#!/usr/bin/env python3
"""
OLYMPUS Jetson SAR Launcher — runs YOLO person detection on e-CAM25_CUONX
and publishes detections to the Olympus planning dashboard over WiFi/Zenoh.

Usage on Jetson:
    export OLYMPUS_INSTANCE=athena
    export OLYMPUS_DRONE_ID=scout-01
    export ZENOH_CONNECT=tcp/<laptop-ip>:7447
    python -m olympus_brain.jetson_sar

Environment variables:
    OLYMPUS_DRONE_ID    Drone identifier (default: jetson-scout-01)
    OLYMPUS_INSTANCE    Mission profile (default: athena)
    ZENOH_CONNECT       Zenoh router endpoint (default: tcp/localhost:7447)
    CAMERA_DEVICE       Camera /dev/video index (default: 0)
    YOLO_MODEL          Path to YOLO weights (default: yolov8n.pt)
    CONFIDENCE          Detection confidence threshold (default: 0.45)
    USE_GSTREAMER       Use GStreamer CSI pipeline (default: 1)
    SAR_CLASSES         Comma-separated COCO class IDs (default: 0 = person)
    FIXED_LAT           Fixed GPS latitude for bench testing (default: 37.7749)
    FIXED_LON           Fixed GPS longitude for bench testing (default: -122.4194)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("olympus.jetson_sar")


async def main() -> None:
    drone_id = os.environ.get("OLYMPUS_DRONE_ID", "jetson-scout-01")
    instance = os.environ.get("OLYMPUS_INSTANCE", "athena")
    camera_device = int(os.environ.get("CAMERA_DEVICE", "0"))
    model_path = os.environ.get("YOLO_MODEL", "yolov8n.pt")
    confidence = float(os.environ.get("CONFIDENCE", "0.45"))
    use_gstreamer = os.environ.get("USE_GSTREAMER", "1") == "1"
    fixed_lat = float(os.environ.get("FIXED_LAT", "37.7749"))
    fixed_lon = float(os.environ.get("FIXED_LON", "-122.4194"))

    zenoh_connect = os.environ.get("ZENOH_CONNECT", "")

    sar_classes_str = os.environ.get("SAR_CLASSES", "0")
    sar_classes = {int(c.strip()) for c in sar_classes_str.split(",")}

    # Force selected mission profile
    os.environ["OLYMPUS_INSTANCE"] = instance

    logger.info("=" * 60)
    logger.info("OLYMPUS SAR — Jetson Camera Detector")
    logger.info("=" * 60)
    logger.info(f"  Drone ID:    {drone_id}")
    logger.info(f"  Instance:    {instance}")
    logger.info(f"  Camera:      /dev/video{camera_device}")
    logger.info(f"  Model:       {model_path}")
    logger.info(f"  Confidence:  {confidence}")
    logger.info(f"  GStreamer:   {use_gstreamer}")
    logger.info(f"  SAR classes: {sar_classes}")
    logger.info(f"  Fixed pos:   ({fixed_lat}, {fixed_lon})")
    logger.info(f"  Zenoh:       {zenoh_connect or 'local'}")
    logger.info("=" * 60)

    # Import after env setup so mission profile loads correctly
    from olympus_brain.camera_detector import CameraDetector
    from olympus_brain.protocol import GeoPosition
    from olympus_brain.scout import ScoutConfig, ScoutDrone

    # Initialize camera + YOLO
    detector = CameraDetector(
        model_path=model_path,
        confidence_threshold=confidence,
        camera_device=camera_device,
        use_gstreamer=use_gstreamer,
        sar_classes=sar_classes,
        drone_id=drone_id,
    )
    detector.initialize()

    # Define a small search area around the fixed position for bench testing
    offset = 0.001  # ~110m
    field = [
        (fixed_lat - offset, fixed_lon - offset),
        (fixed_lat - offset, fixed_lon + offset),
        (fixed_lat + offset, fixed_lon + offset),
        (fixed_lat + offset, fixed_lon - offset),
    ]

    config = ScoutConfig(
        drone_id=drone_id,
        field_boundary=field,
        operational_altitude=60.0,
        scan_speed_mps=5.0,
        detection_confidence_threshold=confidence,
    )

    scout = ScoutDrone(config)

    # Connect Zenoh to the laptop's router (critical for detections to reach dashboard)
    if zenoh_connect:
        scout.node.config.zenoh_config = {
            "mode": "client",
            "connect": {"endpoints": [zenoh_connect]},
        }
        logger.info(f"Zenoh will connect to: {zenoh_connect}")

    # Replace SwarmNet inference with real camera detector
    scout.set_detection_processor(detector)

    # Set initial GPS position (bench test — no real GPS)
    scout.node.update_position(GeoPosition(
        latitude=fixed_lat,
        longitude=fixed_lon,
        altitude=60.0,
    ))

    shutdown_event = asyncio.Event()

    def _signal_handler(sig, frame):
        logger.info(f"Received signal {sig}, shutting down...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        await scout.start()
        logger.info("Scout drone started — camera pipeline active")
        logger.info("Detections will publish to: olympus/detection/%s", drone_id)
        logger.info("Press Ctrl+C to stop")

        await shutdown_event.wait()

    except KeyboardInterrupt:
        pass
    finally:
        detector.release()
        await scout.stop()
        logger.info("Jetson SAR shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
