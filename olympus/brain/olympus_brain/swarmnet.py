from __future__ import annotations

import asyncio
import logging
import time
import struct
import math
import random
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple

import numpy as np

from .protocol import (
    Detection,
    DetectionType,
    GeoPosition,
    ModelMetadata,
    SwarmNetStatus,
)

# ---------------------------------------------------------------------------
# Concept Drift Detection — Page-Hinkley Test
# ---------------------------------------------------------------------------

class DriftDetector:
    """Bidirectional Page-Hinkley test for detecting concept drift.

    Monitors a running mean of accuracy and fires when accuracy *drops*
    significantly below the running mean. Uses two accumulators:

    - _ph_up: detects upward shift   (cumulative += value - mean - alpha)
    - _ph_down: detects downward shift (cumulative += mean - value - alpha)

    For accuracy monitoring, we care about downward drift (model degradation).
    The detector fires when _ph_down exceeds threshold.
    """

    def __init__(
        self,
        threshold: float = 0.10,   # fire when accuracy drops ≥ 10%
        min_samples: int = 50,
        alpha: float = 0.005,      # allowance (sensitivity vs false alarms)
    ):
        self.threshold = threshold
        self.min_samples = min_samples
        self.alpha = alpha
        self._n = 0
        self._sum = 0.0
        # Downward drift accumulator (detects accuracy drops)
        self._ph_down = 0.0
        self._ph_down_min = float("inf")
        # Upward drift accumulator (kept for completeness)
        self._ph_up = 0.0
        self._ph_up_min = float("inf")

    def update(self, value: float) -> bool:
        """Feed a new accuracy value (0.0 – 1.0). Returns True if drift detected."""
        self._n += 1
        self._sum += value
        mean = self._sum / self._n

        # Upward shift accumulator (original Page-Hinkley)
        self._ph_up += value - mean - self.alpha
        self._ph_up_min = min(self._ph_up_min, self._ph_up)

        # Downward shift accumulator — detects accuracy DROPS
        self._ph_down += mean - value - self.alpha
        self._ph_down_min = min(self._ph_down_min, self._ph_down)

        if self._n < self.min_samples:
            return False

        # Fire on downward drift (accuracy degradation)
        return (self._ph_down - self._ph_down_min) > self.threshold

    def reset(self) -> None:
        self._n = 0
        self._sum = 0.0
        self._ph_down = 0.0
        self._ph_down_min = float("inf")
        self._ph_up = 0.0
        self._ph_up_min = float("inf")

logger = logging.getLogger(__name__)


class LocalTrainer:
    """On-device lightweight model. Runs inference locally and trains on
    detections as they arrive. The base station periodically pushes a
    better global model; between pushes the local trainer keeps the model
    warm with its own observations."""

    def __init__(
        self,
        detection_types: List[str],
        model_version: int = 0,
        buffer_size: int = 256,
        learning_rate: float = 0.01,
        gps_denied: bool = False,
    ):
        self.detection_types = detection_types
        self.num_classes = len(detection_types)
        self.class_to_idx = {dt: i for i, dt in enumerate(detection_types)}
        self.model_version = model_version
        self.buffer_size = buffer_size
        self.learning_rate = learning_rate

        self._feature_dim = 32
        self._weights = np.random.randn(self._feature_dim, self.num_classes).astype(np.float32) * 0.1
        self._bias = np.zeros(self.num_classes, dtype=np.float32)

        self._feature_buffer: List[np.ndarray] = []
        self._label_buffer: List[int] = []
        self._label_counts: Dict[str, int] = defaultdict(int)
        self._correct = 0
        self._total = 0

        # GPS-denied mode: use relative displacement encoding from IMU/VIO
        # instead of absolute lat/lon which becomes stale/garbage without GPS
        self._gps_denied = gps_denied
        self._origin: Optional[GeoPosition] = None  # first known-good position
        self._last_position: Optional[GeoPosition] = None
        self._displacement = np.zeros(3, dtype=np.float32)  # cumulative dx, dy, dz (meters)

    def set_gps_denied(self, denied: bool) -> None:
        """Switch between GPS and IMU/VIO relative encoding at runtime."""
        if denied and not self._gps_denied:
            # Entering GPS-denied: lock origin to last known-good position
            if self._last_position:
                self._origin = self._last_position
            self._displacement = np.zeros(3, dtype=np.float32)
            logger.info("SwarmNet: GPS-DENIED mode — switching to relative feature encoding")
        elif not denied and self._gps_denied:
            logger.info("SwarmNet: GPS restored — switching to absolute feature encoding")
        self._gps_denied = denied

    def infer(self, position: GeoPosition) -> List[Detection]:
        features = self._extract_features(position)
        logits = features @ self._weights + self._bias
        probs = _softmax(logits)

        detections = []
        for i, p in enumerate(probs):
            if p > 0.5 and i < len(self.detection_types):
                det_type_str = self.detection_types[i]
                try:
                    det_type = DetectionType(det_type_str)
                except ValueError:
                    continue

                detections.append(Detection.create(
                    detection_type=det_type,
                    position=GeoPosition(
                        latitude=position.latitude + random.uniform(-0.00002, 0.00002),
                        longitude=position.longitude + random.uniform(-0.00002, 0.00002),
                        altitude=0.0,
                    ),
                    confidence=float(p),
                    detected_by="swarmnet",
                    severity=max(1, min(10, int((1.0 - p) * 10))),
                ))

        return detections

    def train_step(self, detection: Detection) -> None:
        det_str = detection.detection_type.value
        if det_str not in self.class_to_idx:
            return

        label = self.class_to_idx[det_str]
        features = self._extract_features(detection.position)

        self._feature_buffer.append(features)
        self._label_buffer.append(label)
        self._label_counts[det_str] += 1

        if len(self._feature_buffer) > self.buffer_size:
            self._feature_buffer.pop(0)
            self._label_buffer.pop(0)

        logits = features @ self._weights + self._bias
        probs = _softmax(logits)
        predicted = int(np.argmax(probs))
        if predicted == label:
            self._correct += 1
        self._total += 1

        grad = probs.copy()
        grad[label] -= 1.0
        self._weights -= self.learning_rate * np.outer(features, grad)
        self._bias -= self.learning_rate * grad

        self.model_version += 1

    def get_weights(self) -> bytes:
        w_bytes = self._weights.tobytes()
        b_bytes = self._bias.tobytes()
        header = struct.pack("<II", self._feature_dim, self.num_classes)
        return header + w_bytes + b_bytes

    def set_weights(self, data: bytes) -> None:
        if len(data) < 8:
            return
        feat_dim, num_cls = struct.unpack("<II", data[:8])
        w_size = feat_dim * num_cls * 4
        b_size = num_cls * 4
        if len(data) < 8 + w_size + b_size:
            return
        self._weights = np.frombuffer(data[8:8 + w_size], dtype=np.float32).reshape(feat_dim, num_cls).copy()
        self._bias = np.frombuffer(data[8 + w_size:8 + w_size + b_size], dtype=np.float32).copy()
        self._feature_dim = feat_dim
        self.num_classes = num_cls

    def get_accuracy(self) -> float:
        if self._total == 0:
            return 0.0
        return self._correct / self._total

    def get_label_distribution(self) -> Dict[str, int]:
        return dict(self._label_counts)

    def get_metadata(self, drone_id: str) -> ModelMetadata:
        return ModelMetadata(
            drone_id=drone_id,
            model_version=self.model_version,
            accuracy=self.get_accuracy(),
            label_distribution=self.get_label_distribution(),
        )

    def get_soft_labels(self, positions: List[GeoPosition]) -> bytes:
        """Produce soft labels (class probabilities) for a set of positions.

        Soft labels are ~90% smaller than full weights and sufficient for
        knowledge distillation on the receiving side.
        """
        all_probs = []
        for pos in positions:
            features = self._extract_features(pos)
            logits = features @ self._weights + self._bias
            probs = _softmax(logits)
            all_probs.append(probs)

        combined = np.array(all_probs, dtype=np.float32)
        header = struct.pack("<II", len(positions), self.num_classes)
        return header + combined.tobytes()

    def distill_from_soft_labels(
        self,
        soft_label_data: bytes,
        positions: List[GeoPosition],
        temperature: float = 2.0,
        lr: float = 0.005,
    ) -> None:
        """Knowledge distillation from soft labels (teacher → student).

        Instead of copying raw weights, train this model to match the
        teacher's output distribution using KL divergence at `temperature`.
        """
        if len(soft_label_data) < 8:
            return
        n_samples, n_classes = struct.unpack("<II", soft_label_data[:8])
        if n_samples != len(positions) or n_classes != self.num_classes:
            return

        teacher_probs = np.frombuffer(
            soft_label_data[8:], dtype=np.float32
        ).reshape(n_samples, n_classes).copy()

        for i, pos in enumerate(positions):
            features = self._extract_features(pos)
            logits = features @ self._weights + self._bias

            # Temperature-scaled softmax
            student_probs = _softmax(logits / temperature)
            teacher_scaled = _softmax(np.log(teacher_probs[i] + 1e-8) / temperature)

            # KL divergence gradient: student_probs - teacher_probs (simplified)
            grad = (student_probs - teacher_scaled) * (temperature ** 2)
            self._weights -= lr * np.outer(features, grad)
            self._bias -= lr * grad

    def get_intermediate_features(self, position: GeoPosition) -> bytes:
        """Produce compact intermediate representation for tactical P2P sharing.

        Returns [features(32) || probs(num_classes)] as float32 bytes.
        For ATHENA (6 classes): (32+6)*4 = 152 bytes — fits LoRa 200B limit.
        """
        features = self._extract_features(position)
        logits = features @ self._weights + self._bias
        probs = _softmax(logits)
        combined = np.concatenate([features, probs]).astype(np.float32)
        return combined.tobytes()

    def receive_peer_features(self, feature_data: bytes, lr: float = 0.002) -> None:
        """Incorporate intermediate features from a nearby drone (tactical layer).

        The peer sends [features(32) || probs(num_classes)] as float32.
        We treat the probs as soft labels and do a single distillation step.
        This is lightweight: one forward+backward pass, ~0.1ms.
        """
        expected_size = (self._feature_dim + self.num_classes) * 4
        if len(feature_data) != expected_size:
            return
        combined = np.frombuffer(feature_data, dtype=np.float32).copy()
        peer_features = combined[:self._feature_dim]
        peer_probs = combined[self._feature_dim:]

        # Single distillation step using peer's soft output
        logits = peer_features @ self._weights + self._bias
        student_probs = _softmax(logits)
        grad = (student_probs - peer_probs)
        self._weights -= lr * np.outer(peer_features, grad)
        self._bias -= lr * grad

    def _extract_features(self, position: GeoPosition) -> np.ndarray:
        self._last_position = position

        if self._gps_denied:
            return self._extract_features_relative(position)
        return self._extract_features_absolute(position)

    def _extract_features_absolute(self, position: GeoPosition) -> np.ndarray:
        """Standard feature encoding using absolute GPS coordinates."""
        rng = np.random.RandomState(
            int((position.latitude * 1e6 + position.longitude * 1e6) % (2**31))
        )
        features = np.zeros(self._feature_dim, dtype=np.float32)
        features[0] = position.latitude % 1.0
        features[1] = position.longitude % 1.0
        features[2] = math.sin(position.latitude * 1000)
        features[3] = math.cos(position.longitude * 1000)
        features[4:] = rng.randn(self._feature_dim - 4).astype(np.float32) * 0.1
        return features

    def _extract_features_relative(self, position: GeoPosition) -> np.ndarray:
        """GPS-denied feature encoding using relative displacement from origin.

        Uses IMU/VIO-style relative positioning: encodes displacement (dx, dy, dz)
        from the last known-good GPS fix rather than absolute coordinates.
        This remains meaningful even when GPS is stale or unavailable.
        """
        if self._origin is None:
            self._origin = position

        # Compute displacement in meters from origin
        dx = (position.longitude - self._origin.longitude) * 111320 * math.cos(
            math.radians(self._origin.latitude)
        )
        dy = (position.latitude - self._origin.latitude) * 110540
        dz = position.altitude - self._origin.altitude
        self._displacement = np.array([dx, dy, dz], dtype=np.float32)

        # Deterministic noise from displacement magnitude (not absolute coords)
        disp_mag = float(np.linalg.norm(self._displacement))
        rng = np.random.RandomState(int(disp_mag * 1e3) % (2**31))

        features = np.zeros(self._feature_dim, dtype=np.float32)
        # Relative displacement features (normalized to ~100m scale)
        features[0] = dx / 100.0
        features[1] = dy / 100.0
        features[2] = math.sin(disp_mag / 50.0)
        features[3] = math.cos(disp_mag / 50.0)
        # Bearing from origin
        bearing = math.atan2(dx, dy) if (abs(dx) + abs(dy)) > 0.1 else 0.0
        features[4] = math.sin(bearing)
        features[5] = math.cos(bearing)
        features[6] = dz / 50.0  # altitude offset
        features[7:] = rng.randn(self._feature_dim - 7).astype(np.float32) * 0.1
        return features


class CentralAggregator:
    """Runs at the base station. Collects detections from ALL drones,
    trains a single global model, and publishes updated weights for
    the fleet to download.  Detections arrive over LoRa (~100 bytes
    each) — much cheaper than shipping model weights drone-to-drone."""

    def __init__(
        self,
        detection_types: List[str],
        retrain_interval: float = 30.0,
        buffer_size: int = 2048,
        drift_threshold: float = 0.10,
    ):
        self.trainer = LocalTrainer(
            detection_types,
            buffer_size=buffer_size,
        )
        self.retrain_interval = retrain_interval
        self.drone_contributions: Dict[str, int] = defaultdict(int)
        self._last_retrain = time.time()
        self._global_version = 0

        # Concept drift detection
        self._drift_detector = DriftDetector(threshold=drift_threshold)
        self._accuracy_window: deque[float] = deque(maxlen=100)
        self._drift_retrain_count = 0
        self._use_distillation = False  # Switch to soft labels when bandwidth limited

    def receive_detection(self, detection: Detection) -> None:
        """Called when a detection arrives from any drone via Zenoh."""
        self.trainer.train_step(detection)
        self.drone_contributions[detection.detected_by] += 1

        # Track rolling accuracy for drift detection
        accuracy = self.trainer.get_accuracy()
        self._accuracy_window.append(accuracy)
        drift = self._drift_detector.update(accuracy)
        if drift:
            logger.warning(
                f"CONCEPT DRIFT detected — accuracy={accuracy:.3f}, "
                f"triggering immediate retrain"
            )
            self._drift_detector.reset()
            self._drift_retrain_count += 1

    def should_publish(self) -> bool:
        """Returns True if a model update should be pushed to drones.

        Triggers on either:
        - Regular interval elapsed
        - Concept drift detected (accuracy dropped significantly)
        """
        if self._drift_retrain_count > 0:
            return True
        return time.time() - self._last_retrain >= self.retrain_interval

    def get_global_weights(self) -> Tuple[bytes, int]:
        """Returns (weights_bytes, version) for distribution to drones."""
        self._last_retrain = time.time()
        self._global_version += 1
        self._drift_retrain_count = max(0, self._drift_retrain_count - 1)
        return self.trainer.get_weights(), self._global_version

    def get_soft_labels(self, positions: List[GeoPosition]) -> Tuple[bytes, int]:
        """Returns (soft_labels_bytes, version) — ~90% smaller than full weights.

        Use when bandwidth is limited (LoRa-only / degraded comms).
        """
        self._last_retrain = time.time()
        self._global_version += 1
        self._drift_retrain_count = max(0, self._drift_retrain_count - 1)
        return self.trainer.get_soft_labels(positions), self._global_version

    def set_bandwidth_mode(self, limited: bool) -> None:
        """Switch between full weights and soft-label distillation."""
        self._use_distillation = limited

    def get_status(self) -> SwarmNetStatus:
        return SwarmNetStatus(
            active_drones=len(self.drone_contributions),
            model_versions={"global": self._global_version},
            accuracies={"global": self.trainer.get_accuracy()},
            contributions=dict(self.drone_contributions),
        )


class SwarmNetController:
    """Per-drone controller. Runs inference locally, receives global
    model updates from the central base station."""

    def __init__(self, drone_id: str, trainer: LocalTrainer):
        self.drone_id = drone_id
        self.trainer = trainer
        self._global_version = 0
        self._recent_positions: deque[GeoPosition] = deque(maxlen=64)
        self._ack_callback: Optional[callable] = None

    def set_ack_callback(self, callback) -> None:
        """Register callback to send MODEL_ACK when weights are loaded.
        Signature: callback(drone_id: str, version: int)."""
        self._ack_callback = callback

    def receive_global_weights(self, weights: bytes, version: int) -> None:
        """Called when central pushes new global model weights."""
        if version > self._global_version:
            self.trainer.set_weights(weights)
            self._global_version = version
            self.trainer.model_version = version
            logger.info(
                f"SwarmNet: loaded global model v{version} on {self.drone_id}"
            )
            # Send ACK to base station confirming model loaded
            if self._ack_callback:
                try:
                    self._ack_callback(self.drone_id, version)
                except Exception as e:
                    logger.error(f"SwarmNet: failed to send model ACK: {e}")

    def receive_soft_labels(self, data: bytes, version: int) -> None:
        """Called when central pushes soft labels (bandwidth-limited mode).

        Uses knowledge distillation to update local model from soft labels
        rather than replacing weights entirely.
        """
        if version > self._global_version:
            positions = list(self._recent_positions)
            if positions:
                self.trainer.distill_from_soft_labels(data, positions)
                self._global_version = version
                self.trainer.model_version = version
                logger.info(
                    f"SwarmNet: distilled soft labels v{version} on {self.drone_id} "
                    f"({len(positions)} samples)"
                )

    def record_position(self, position: GeoPosition) -> None:
        """Track recent positions for soft-label distillation."""
        self._recent_positions.append(position)

    def get_metadata(self) -> ModelMetadata:
        return self.trainer.get_metadata(self.drone_id)


def _softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - np.max(x))
    return e / e.sum()
