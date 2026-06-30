from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime
from typing import Optional, Callable

from shapely.geometry import Polygon

from olympus_brain.node import OlympusNode, NodeConfig
from olympus_brain.protocol import (
    CommandType,
    DroneRole,
    DroneStatus,
    GeoPosition,
    Detection,
    DetectionType,
    Task,
    TaskType,
    ZenohKeys,
)
from olympus_brain.partitioning import FieldPartitioner, CoveragePathPlanner
from olympus_brain.mission_profile import load_profile
from olympus_brain.swarmnet import (
    LocalTrainer,
    SwarmNetController,
)

logger = logging.getLogger(__name__)


class TDMAFeatureScheduler:
    """Time-Division Multiple Access scheduler for feature broadcasts.

    Each drone gets a dedicated time slot within a frame, preventing N²
    channel saturation on the LoRa mesh. At 0.1 Hz effective per-drone
    rate, 20 drones produce ~122 B/s total — within LoRa budget.
    """

    def __init__(
        self,
        drone_id: str,
        frame_period_s: float = 10.0,
        max_slots: int = 32,
    ):
        self._drone_id = drone_id
        self._frame_period = frame_period_s
        self._max_slots = max_slots
        self._slot_index = self._hash_slot(drone_id)
        self._last_broadcast: float = 0.0

    def _hash_slot(self, drone_id: str) -> int:
        """Deterministic slot assignment from drone ID."""
        h = 0
        for c in drone_id:
            h = (h * 31 + ord(c)) & 0xFFFFFFFF
        return h % self._max_slots

    def update_slot_count(self, n_drones: int) -> None:
        """Adjust frame based on observed swarm size."""
        self._max_slots = max(4, min(32, n_drones + 2))
        self._slot_index = self._hash_slot(self._drone_id) % self._max_slots

    def should_broadcast(self) -> bool:
        """Check if this drone's TDMA slot is active right now."""
        import time as _time
        now = _time.monotonic()

        # Enforce minimum inter-broadcast interval (frame_period / max_slots)
        slot_duration = self._frame_period / self._max_slots
        time_in_frame = now % self._frame_period
        my_slot_start = self._slot_index * slot_duration
        my_slot_end = my_slot_start + slot_duration

        in_slot = my_slot_start <= time_in_frame < my_slot_end
        cooldown_ok = (now - self._last_broadcast) >= slot_duration

        if in_slot and cooldown_ok:
            self._last_broadcast = now
            return True
        return False


class BeliefMap:
    """Bayesian occupancy grid for information-gain waypoint planning.

    Tracks per-cell belief about detection probability. Cells that have
    never been visited have high uncertainty (high information gain).
    Cells where detections were found have elevated prior for revisit.
    """

    def __init__(
        self,
        bounds: list[tuple[float, float]],
        cell_size_deg: float = 0.00005,  # ~5m cells
        prior: float = 0.5,
        decay_rate: float = 0.01,
    ):
        if not bounds or len(bounds) < 3:
            self._enabled = False
            self.cells = {}
            return

        self._enabled = True
        self.cell_size = cell_size_deg
        self.prior = prior
        self.decay_rate = decay_rate

        lats = [b[0] for b in bounds]
        lons = [b[1] for b in bounds]
        self.min_lat = min(lats)
        self.max_lat = max(lats)
        self.min_lon = min(lons)
        self.max_lon = max(lons)

        self.cells: dict[tuple[int, int], float] = {}

    def _cell_key(self, lat: float, lon: float) -> tuple[int, int]:
        r = int((lat - self.min_lat) / self.cell_size)
        c = int((lon - self.min_lon) / self.cell_size)
        return (r, c)

    def _cell_center(self, key: tuple[int, int]) -> tuple[float, float]:
        lat = self.min_lat + (key[0] + 0.5) * self.cell_size
        lon = self.min_lon + (key[1] + 0.5) * self.cell_size
        return (lat, lon)

    def update_visited(self, lat: float, lon: float, detected: bool) -> None:
        """Update belief at a location after visiting it."""
        if not self._enabled:
            return
        key = self._cell_key(lat, lon)
        current = self.cells.get(key, self.prior)
        if detected:
            # Increase belief (more likely to find detections here)
            self.cells[key] = min(0.95, current * 1.3 + 0.1)
        else:
            # Decrease belief (less likely)
            self.cells[key] = max(0.05, current * 0.6)

    def information_gain(self, lat: float, lon: float) -> float:
        """Compute information gain for visiting a location.

        High gain for: unvisited cells (prior), cells with high detection probability.
        """
        if not self._enabled:
            return 0.5
        key = self._cell_key(lat, lon)
        belief = self.cells.get(key, self.prior)
        # Shannon entropy: H = -p*log(p) - (1-p)*log(1-p)
        import math
        if belief <= 0 or belief >= 1:
            return 0.0
        entropy = -(belief * math.log2(belief) + (1 - belief) * math.log2(1 - belief))
        return entropy

    def rank_waypoints(
        self,
        waypoints: list[tuple[float, float]],
        current_pos: tuple[float, float],
        distance_weight: float = 0.3,
        info_weight: float = 0.7,
    ) -> list[tuple[float, float]]:
        """Rank waypoints by information gain, penalized by distance.

        Returns waypoints sorted by descending score (best first).
        """
        if not self._enabled or not waypoints:
            return waypoints

        import math

        scores = []
        for wp in waypoints:
            ig = self.information_gain(wp[0], wp[1])

            dlat = wp[0] - current_pos[0]
            dlon = wp[1] - current_pos[1]
            dist = math.sqrt(dlat**2 + dlon**2)
            max_dist = math.sqrt(
                (self.max_lat - self.min_lat)**2 +
                (self.max_lon - self.min_lon)**2
            )
            dist_score = 1.0 - (dist / max_dist) if max_dist > 0 else 1.0

            score = info_weight * ig + distance_weight * dist_score
            scores.append((score, wp))

        scores.sort(key=lambda x: -x[0])
        return [wp for _, wp in scores]


class ScoutConfig:

    def __init__(
        self,
        drone_id: str,
        field_boundary: list[tuple[float, float]],
        operational_altitude: float = 35.0,
        scan_speed_mps: float = 10.0,
        swath_width: float = 0.0001,
        detection_confidence_threshold: float = 0.7,
    ):
        self.drone_id = drone_id
        self.field_boundary = field_boundary
        self.operational_altitude = operational_altitude
        self.scan_speed_mps = scan_speed_mps
        self.swath_width = swath_width
        self.detection_confidence_threshold = detection_confidence_threshold


class ScoutDrone:

    def __init__(self, config: ScoutConfig):
        self.config = config

        node_config = NodeConfig(
            drone_id=config.drone_id,
            role=DroneRole.SCOUT,
        )
        self.node = OlympusNode(node_config)

        self.partitioner = FieldPartitioner(config.field_boundary)
        self.planner = CoveragePathPlanner(config.swath_width)

        self.assigned_zone: Optional[Polygon] = None
        self.current_path: list[tuple[float, float]] = []
        self.path_index: int = 0
        self.belief_map = BeliefMap(config.field_boundary)
        self._use_info_gain: bool = True
        self._running = False
        self._scan_task: Optional[asyncio.Task] = None
        self._swarmnet_task: Optional[asyncio.Task] = None

        self._detection_processor: Optional[Callable] = None

        profile = load_profile()
        detection_types = list(profile.detection_to_task.keys())
        if not detection_types:
            detection_types = [dt.value for dt in DetectionType]
        self.trainer = LocalTrainer(detection_types)
        self.swarmnet = SwarmNetController(
            drone_id=config.drone_id,
            trainer=self.trainer,
        )
        self._tdma = TDMAFeatureScheduler(config.drone_id)

    async def start(self) -> None:
        logger.info(f"Starting Scout drone: {self.config.drone_id}")

        await self.node.start()

        self.node.on_global_model(self.swarmnet.receive_global_weights)
        self.node.on_features(self._on_peer_features)

        # Wire model ACK: when weights are loaded, send MODEL_ACK to base
        self.swarmnet.set_ack_callback(self._send_model_ack)

        self._running = True
        self._scan_task = asyncio.create_task(self._scan_loop())
        self._swarmnet_task = asyncio.create_task(self._swarmnet_loop())

        logger.info(f"Scout {self.config.drone_id} operational")

    async def stop(self) -> None:
        logger.info(f"Stopping Scout drone: {self.config.drone_id}")

        self._running = False

        if self._scan_task:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass

        if self._swarmnet_task:
            self._swarmnet_task.cancel()
            try:
                await self._swarmnet_task
            except asyncio.CancelledError:
                pass

        await self.node.stop()

    def set_detection_processor(self, processor: Callable) -> None:
        self._detection_processor = processor

    def _recompute_zone(self) -> None:
        swarm = self.node.swarm_state
        scout_positions = []
        my_index = 0

        my_pos = (
            self.node.telemetry.position.latitude,
            self.node.telemetry.position.longitude,
        )
        scout_positions.append(my_pos)

        for drone_id, telem in swarm.members.items():
            if telem.role == DroneRole.SCOUT and telem.status != DroneStatus.OFFLINE:
                pos = (telem.position.latitude, telem.position.longitude)
                if pos != my_pos:
                    scout_positions.append(pos)

        if len(scout_positions) == 0:
            self.assigned_zone = self.partitioner.field_polygon
        else:
            partitions = self.partitioner.compute_partitions(scout_positions)
            if partitions and len(partitions) > my_index:
                self.assigned_zone = partitions[my_index]
            else:
                self.assigned_zone = self.partitioner.field_polygon

        if self.assigned_zone and not self.assigned_zone.is_empty:
            raw_path = self.planner.generate_path(
                self.assigned_zone,
                start_position=my_pos,
            )
            # Re-rank waypoints by information gain if enabled
            if self._use_info_gain and raw_path:
                self.current_path = self.belief_map.rank_waypoints(
                    raw_path, my_pos,
                )
            else:
                self.current_path = raw_path
            self.path_index = 0
            logger.info(f"Zone updated: {len(self.current_path)} waypoints")
        else:
            self.current_path = []
            logger.warning("No valid zone assigned")

    async def _scan_loop(self) -> None:
        await asyncio.sleep(2.0)
        self._recompute_zone()

        recompute_interval = 60.0
        last_recompute = datetime.utcnow()

        while self._running:
            try:
                now = datetime.utcnow()
                if (now - last_recompute).total_seconds() > recompute_interval:
                    self._recompute_zone()
                    last_recompute = now

                self.node.update_status(DroneStatus.SCANNING)

                if self.current_path and self.path_index < len(self.current_path):
                    target = self.current_path[self.path_index]

                    await self._navigate_to(target)

                    # Record position for soft-label distillation
                    self.swarmnet.record_position(self.node.telemetry.position)

                    await self._process_detections()

                    # Tactical layer: TDMA-gated feature broadcast to peer scouts via LoRa
                    # Each drone gets a dedicated time slot to prevent N² channel saturation
                    n_scouts = len([
                        m for m in self.node.swarm_state.members.values()
                        if m.role == DroneRole.SCOUT and m.status != DroneStatus.OFFLINE
                    ]) + 1
                    self._tdma.update_slot_count(n_scouts)
                    if self._tdma.should_broadcast():
                        pos = self.node.telemetry.position
                        features = self.trainer.get_intermediate_features(pos)
                        if features:
                            self.node.publish_features(features)

                    self.path_index += 1

                    if self.path_index >= len(self.current_path):
                        logger.info("Coverage path complete, restarting")
                        self.path_index = 0
                else:
                    await asyncio.sleep(1.0)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scan loop error: {e}")
                await asyncio.sleep(1.0)

    async def _navigate_to(self, target: tuple[float, float]) -> None:
        current = self.node.telemetry.position
        target_pos = GeoPosition(
            latitude=target[0],
            longitude=target[1],
            altitude=self.config.operational_altitude,
        )

        distance = current.distance_to(target_pos)
        travel_time = distance / self.config.scan_speed_mps

        steps = max(1, int(travel_time * 2))
        for i in range(steps):
            if not self._running:
                break

            t = (i + 1) / steps
            new_lat = current.latitude + t * (target_pos.latitude - current.latitude)
            new_lon = current.longitude + t * (target_pos.longitude - current.longitude)

            self.node.update_position(GeoPosition(
                latitude=new_lat,
                longitude=new_lon,
                altitude=self.config.operational_altitude,
            ))

            # Mark cell as visited with no detection (updated in _handle_detection if found)
            self.belief_map.update_visited(new_lat, new_lon, detected=False)

            await asyncio.sleep(0.5)

    async def _process_detections(self) -> None:
        if self._detection_processor:
            detections = await self._detection_processor(self.node.telemetry.position)
            for detection in detections:
                self.trainer.train_step(detection)
                await self._handle_detection(detection)
        else:
            await self._swarmnet_inference()

    async def _swarmnet_inference(self) -> None:
        pos = self.node.telemetry.position

        detections = self.trainer.infer(pos)

        if not detections and random.random() > 0.95:
            detection_type = random.choice([
                DetectionType.WEED,
                DetectionType.PEST,
                DetectionType.CROP_STRESS,
            ])

            detection = Detection.create(
                detection_type=detection_type,
                position=GeoPosition(
                    latitude=pos.latitude + random.uniform(-0.00001, 0.00001),
                    longitude=pos.longitude + random.uniform(-0.00001, 0.00001),
                    altitude=0.0,
                ),
                confidence=random.uniform(0.7, 0.99),
                detected_by=self.config.drone_id,
                severity=random.randint(3, 9),
            )
            self.trainer.train_step(detection)
            await self._handle_detection(detection)
            return

        for detection in detections:
            detection.detected_by = self.config.drone_id
            self.trainer.train_step(detection)
            await self._handle_detection(detection)

    async def _swarmnet_loop(self) -> None:
        await asyncio.sleep(5.0)

        while self._running:
            try:
                metadata = self.swarmnet.get_metadata()
                self.node.publish_model_metadata(metadata)

                await asyncio.sleep(10.0)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"SwarmNet loop error: {e}")
                await asyncio.sleep(5.0)

    async def _handle_detection(self, detection: Detection) -> None:
        # Update belief map — detected=True raises revisit priority for this cell
        self.belief_map.update_visited(
            detection.position.latitude,
            detection.position.longitude,
            detected=True,
        )

        if detection.confidence < self.config.detection_confidence_threshold:
            logger.debug(f"Filtered low-confidence detection: {detection.confidence:.2f}")
            return

        logger.info(
            f"DETECTION: {detection.detection_type.value} "
            f"at ({detection.position.latitude:.6f}, {detection.position.longitude:.6f}) "
            f"conf={detection.confidence:.2f} sev={detection.severity}"
        )

        await self.node.publish_detection(detection)

        task_type = self._detection_to_task_type(detection.detection_type)
        task = Task.from_detection(detection, task_type)

        logger.info(f"Creating task {task.id} for {task_type.value}")
        await self.node.publish_task(task)

    def _on_peer_features(self, drone_id: str, data: bytes) -> None:
        """Receive intermediate features from a nearby scout (tactical P2P layer)."""
        self.trainer.receive_peer_features(data)
        logger.debug(f"Received peer features from {drone_id} ({len(data)}B)")

    def _send_model_ack(self, drone_id: str, version: int) -> None:
        """Send MODEL_ACK to base station confirming weights loaded."""
        self.node.send_command(
            "*", CommandType.MODEL_ACK,
            payload={"drone_id": drone_id, "model_version": version},
        )
        logger.info(f"Sent MODEL_ACK for v{version}")

    def _detection_to_task_type(self, detection_type: DetectionType) -> TaskType:
        profile = load_profile()
        profile_mapping = profile.detection_to_task
        task_str = profile_mapping.get(detection_type.value)
        if task_str:
            try:
                return TaskType(task_str)
            except ValueError:
                pass
        return TaskType.INSPECT


async def main():
    import os

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    field = [
        (45.5230, -122.6760),
        (45.5230, -122.6750),
        (45.5240, -122.6750),
        (45.5240, -122.6760),
    ]

    config = ScoutConfig(
        drone_id=os.environ.get("OLYMPUS_DRONE_ID", "scout_01"),
        field_boundary=field,
    )

    scout = ScoutDrone(config)

    try:
        await scout.start()

        while True:
            await asyncio.sleep(1.0)

    except KeyboardInterrupt:
        pass
    finally:
        await scout.stop()


if __name__ == "__main__":
    asyncio.run(main())
