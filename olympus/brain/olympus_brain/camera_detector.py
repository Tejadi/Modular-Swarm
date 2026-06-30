"""
OLYMPUS Camera Detector — YOLO inference on e-CAM25_CUONX (AR0234 CSI-2) for Jetson Orin.

Plugs into ScoutDrone.set_detection_processor() to replace SwarmNet inference
with real camera-based person detection (SAR / HERMES mode).

Pipeline:
  CSI Camera → GStreamer → OpenCV → YOLOv8n (TensorRT) → Olympus Detection → Zenoh
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from olympus_brain.protocol import (
    Detection,
    DetectionType,
    GeoPosition,
)

logger = logging.getLogger(__name__)

# COCO class 0 = person
PERSON_CLASS_ID = 0

# Map YOLO COCO classes to Olympus SAR detection types
YOLO_TO_OLYMPUS = {
    0: DetectionType.PERSON_DETECTED,       # person
    2: DetectionType.VEHICLE_WRECKAGE,      # car (wreckage proxy)
    5: DetectionType.VEHICLE_WRECKAGE,      # bus
    7: DetectionType.VEHICLE_WRECKAGE,      # truck
}


def build_gstreamer_pipeline(
    sensor_id: int = 0,
    width: int = 1280,
    height: int = 720,
    fps: int = 120,
    display_width: int = 640,
    display_height: int = 480,
    flip_method: int = 0,
) -> str:
    """GStreamer pipeline for e-CAM25_CUONX (AR0234) via V4L2 on Jetson Orin.

    AR0234 outputs UYVY 4:2:2 at:
      1280x720@120fps, 1920x1080@70fps, 1920x1200@60fps
    Default 720p@120 — lowest latency, and we downscale to 640 for YOLO anyway.
    """
    return (
        f"v4l2src device=/dev/video{sensor_id} ! "
        f"video/x-raw,format=UYVY,width={width},height={height},framerate={fps}/1 ! "
        f"nvvidconv flip-method={flip_method} ! "
        f"video/x-raw(memory:NVMM),width={display_width},height={display_height},format=BGRx ! "
        f"nvvidconv ! video/x-raw,format=BGRx ! "
        f"videoconvert ! video/x-raw,format=BGR ! appsink drop=1"
    )


def build_usb_pipeline(device: int = 0, width: int = 640, height: int = 480) -> str:
    """Fallback USB camera pipeline for testing."""
    return (
        f"v4l2src device=/dev/video{device} ! "
        f"video/x-raw,width={width},height={height} ! "
        f"videoconvert ! video/x-raw,format=BGR ! appsink drop=1"
    )


class CameraDetector:
    """Real-time YOLO person detector for SAR operations."""

    def __init__(
        self,
        model_path: str = "yolov8n.pt",
        confidence_threshold: float = 0.5,
        camera_device: int = 0,
        use_gstreamer: bool = True,
        inference_size: int = 640,
        max_fps: float = 10.0,
        sar_classes: Optional[set[int]] = None,
        drone_id: str = "jetson-scout-01",
    ):
        self.drone_id = drone_id
        self.model_path = model_path
        self.confidence_threshold = confidence_threshold
        self.camera_device = camera_device
        self.use_gstreamer = use_gstreamer
        self.inference_size = inference_size
        self.min_interval = 1.0 / max_fps
        self.sar_classes = sar_classes or {0}  # default: person only

        self._model = None
        self._cap: Optional[cv2.VideoCapture] = None
        self._last_inference = 0.0
        self._frame_count = 0
        self._detection_count = 0

    def initialize(self) -> None:
        """Load YOLO model and open camera. Call once at startup."""
        logger.info(f"Loading YOLO model: {self.model_path}")
        from ultralytics import YOLO
        self._model = YOLO(self.model_path)

        # Try TensorRT export on first run (cached after that)
        model_path = Path(self.model_path)
        trt_path = model_path.with_suffix(".engine")
        if not trt_path.exists() and model_path.suffix == ".pt":
            logger.info("Exporting to TensorRT (first run only, takes a few minutes)...")
            try:
                self._model.export(format="engine", imgsz=self.inference_size, half=True)
                self._model = YOLO(str(trt_path))
                logger.info(f"TensorRT engine ready: {trt_path}")
            except Exception as e:
                logger.warning(f"TensorRT export failed, using PyTorch: {e}")

        self._open_camera()
        logger.info("Camera detector initialized")

    def _open_camera(self) -> None:
        """Open camera with GStreamer or V4L2 fallback."""
        if self.use_gstreamer:
            pipeline = build_gstreamer_pipeline(sensor_id=self.camera_device)
            logger.info(f"Opening CSI camera with GStreamer: {pipeline}")
            self._cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)

        if self._cap is None or not self._cap.isOpened():
            logger.warning("GStreamer pipeline failed, falling back to V4L2 direct")
            self._cap = cv2.VideoCapture(self.camera_device)

        if not self._cap.isOpened():
            raise RuntimeError(
                f"Cannot open camera device {self.camera_device}. "
                "Check: ls /dev/video* and verify driver is loaded."
            )

        logger.info(f"Camera opened: {self._cap.get(cv2.CAP_PROP_FRAME_WIDTH):.0f}x"
                     f"{self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT):.0f}")

    def release(self) -> None:
        """Release camera resources."""
        if self._cap:
            self._cap.release()
            logger.info(f"Camera released. Frames: {self._frame_count}, Detections: {self._detection_count}")

    def _capture_frame(self) -> Optional[np.ndarray]:
        """Capture a single frame from the camera."""
        if self._cap is None or not self._cap.isOpened():
            return None
        ret, frame = self._cap.read()
        if not ret:
            logger.warning("Frame capture failed")
            return None
        self._frame_count += 1
        return frame

    def _run_inference(self, frame: np.ndarray) -> list[dict]:
        """Run YOLO inference, return filtered detections."""
        results = self._model(
            frame,
            imgsz=self.inference_size,
            conf=self.confidence_threshold,
            classes=list(self.sar_classes),
            verbose=False,
        )

        detections = []
        for result in results:
            boxes = result.boxes
            if boxes is None:
                continue
            for i in range(len(boxes)):
                cls_id = int(boxes.cls[i].item())
                conf = float(boxes.conf[i].item())
                bbox = boxes.xyxy[i].cpu().numpy().tolist()

                detections.append({
                    "class_id": cls_id,
                    "confidence": conf,
                    "bbox": bbox,
                    "class_name": result.names.get(cls_id, "unknown"),
                })

        return detections

    async def __call__(self, position: GeoPosition) -> list[Detection]:
        """
        Detection processor compatible with ScoutDrone.set_detection_processor().

        Args:
            position: Current drone GPS position from the scout navigation.

        Returns:
            List of Olympus Detection objects for any people/vehicles found.
        """
        now = time.monotonic()
        if now - self._last_inference < self.min_interval:
            return []
        self._last_inference = now

        frame = self._capture_frame()
        if frame is None:
            return []

        # Run inference in thread pool to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        raw_detections = await loop.run_in_executor(None, self._run_inference, frame)

        olympus_detections = []
        for det in raw_detections:
            cls_id = det["class_id"]
            det_type = YOLO_TO_OLYMPUS.get(cls_id)
            if det_type is None:
                continue

            confidence = det["confidence"]
            severity = max(1, min(10, int(confidence * 10)))

            # Slight position jitter to differentiate multiple detections at same location
            import random
            lat_offset = random.uniform(-0.00002, 0.00002)
            lon_offset = random.uniform(-0.00002, 0.00002)

            detection = Detection(
                id=str(uuid.uuid4()),
                detection_type=det_type,
                position=GeoPosition(
                    latitude=position.latitude + lat_offset,
                    longitude=position.longitude + lon_offset,
                    altitude=position.altitude,
                ),
                confidence=confidence,
                severity=severity,
                detected_by=self.drone_id,
                timestamp=datetime.utcnow(),
                metadata={
                    "source": "yolov8_camera",
                    "class_name": det["class_name"],
                    "bbox": det["bbox"],
                    "frame_id": self._frame_count,
                },
            )
            olympus_detections.append(detection)
            self._detection_count += 1

            logger.info(
                f"CAMERA DETECTION: {det_type.value} "
                f"conf={confidence:.2f} bbox={det['bbox']}"
            )

        return olympus_detections
