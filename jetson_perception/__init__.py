"""Standalone perception process for a swarm vehicle's Jetson.

    python -m jetson_perception --fake                       # synthetic detections
    python -m jetson_perception --model yolov8n.pt --source 0  # real YOLO
"""
from .detector import DetectionPublisher, run_fake, run_yolo, DEFAULT_PORT  # noqa: F401
