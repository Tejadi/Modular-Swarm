import asyncio
import json
import time
import heapq
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple, Callable, Any
from enum import Enum
from datetime import datetime, timedelta
import numpy as np

import struct

from .protocol import (
    GeoPosition, DroneTelemetry, Detection, Task, TaskBid,
    DroneRole, DroneStatus, DetectionType, TaskType, TaskStatus,
    ZenohKeys, ModelMetadata, SwarmNetStatus,
)
from .partitioning import FieldPartitioner, CoveragePathPlanner, compute_field_coverage
from .swarmnet import CentralAggregator


class MissionPhase(str, Enum):
    IDLE = "idle"
    PLANNING = "planning"
    DEPLOYING = "deploying"
    ACTIVE = "active"
    PAUSED = "paused"
    RETURNING = "returning"
    COMPLETE = "complete"
    EMERGENCY = "emergency"


class AlertLevel(str, Enum):
    NORMAL = "normal"
    CAUTION = "caution"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class PrioritizedDetection:
    priority: float
    detection: Detection
    timestamp: float = field(default_factory=time.time)

    def __lt__(self, other):
        return self.priority < other.priority


@dataclass
class FleetStatus:
    total_drones: int = 0
    scouts_active: int = 0
    scouts_idle: int = 0
    executors_active: int = 0
    executors_idle: int = 0
    executors_returning: int = 0
    low_battery_count: int = 0
    offline_count: int = 0
    total_detections: int = 0
    pending_tasks: int = 0
    completed_tasks: int = 0
    alert_level: AlertLevel = AlertLevel.NORMAL

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_drones": self.total_drones,
            "scouts": {"active": self.scouts_active, "idle": self.scouts_idle},
            "executors": {"active": self.executors_active, "idle": self.executors_idle, "returning": self.executors_returning},
            "low_battery_count": self.low_battery_count,
            "offline_count": self.offline_count,
            "detections": self.total_detections,
            "tasks": {"pending": self.pending_tasks, "completed": self.completed_tasks},
            "alert_level": self.alert_level.value
        }


@dataclass
class MissionConfig:
    field_boundary: List[Tuple[float, float]]
    home_position: GeoPosition

    auction_timeout_sec: float = 5.0
    detection_expiry_sec: float = 3600.0
    task_timeout_sec: float = 600.0

    min_battery_threshold: float = 20.0
    critical_battery_threshold: float = 10.0
    max_concurrent_tasks_per_executor: int = 1

    priority_weight_severity: float = 1.0
    priority_weight_age: float = 0.1
    priority_weight_distance: float = 0.05

    scout_altitude_m: float = 35.0
    executor_transit_altitude_m: float = 50.0
    executor_work_altitude_m: float = 5.0
    swath_width_deg: float = 0.0001


class AuctionManager:

    def __init__(self, timeout_sec: float = 5.0):
        self.timeout_sec = timeout_sec
        self.active_auctions: Dict[str, Dict] = {}
        self._lock = asyncio.Lock()

    async def start_auction(self, task: Task) -> str:
        async with self._lock:
            auction_state = {
                "task": task,
                "bids": {},
                "start_time": time.time(),
                "status": "open",
                "winner": None
            }
            self.active_auctions[task.id] = auction_state
            return task.id

    async def submit_bid(self, task_id: str, bid: TaskBid) -> bool:
        async with self._lock:
            if task_id not in self.active_auctions:
                return False

            auction = self.active_auctions[task_id]
            if auction["status"] != "open":
                return False

            if time.time() - auction["start_time"] > self.timeout_sec:
                auction["status"] = "closed"
                return False

            auction["bids"][bid.drone_id] = bid
            return True

    async def close_auction(self, task_id: str) -> Optional[TaskBid]:
        async with self._lock:
            if task_id not in self.active_auctions:
                return None

            auction = self.active_auctions[task_id]
            auction["status"] = "closed"

            if not auction["bids"]:
                return None

            winner = min(auction["bids"].values(), key=lambda b: b.cost)
            auction["winner"] = winner.drone_id

            return winner

    async def get_auction_status(self, task_id: str) -> Optional[Dict]:
        async with self._lock:
            return self.active_auctions.get(task_id)

    async def cleanup_expired(self):
        async with self._lock:
            expired = []
            current_time = time.time()

            for task_id, auction in self.active_auctions.items():
                if auction["status"] == "closed":
                    if current_time - auction["start_time"] > 300:
                        expired.append(task_id)

            for task_id in expired:
                del self.active_auctions[task_id]


class MissionPlanner:

    def __init__(self, config: MissionConfig, zenoh_session=None):
        self.config = config
        self.z_session = zenoh_session

        self.drones: Dict[str, DroneTelemetry] = {}
        self.detections: Dict[str, Detection] = {}
        self.tasks: Dict[str, Task] = {}
        self.completed_tasks: Dict[str, Task] = {}

        self.detection_queue: List[PrioritizedDetection] = []

        self.auction_manager = AuctionManager(config.auction_timeout_sec)

        self.field_partitioner = FieldPartitioner(config.field_boundary)
        self.path_planner = CoveragePathPlanner(config.swath_width_deg)
        self.current_partitions: Dict[str, Any] = {}

        self.phase = MissionPhase.IDLE
        self.mission_start_time: Optional[float] = None
        self.last_partition_update: float = 0
        self.partition_update_interval: float = 60.0

        self.feature_cache: Dict[str, bytes] = {}
        self.feature_timestamps: Dict[str, float] = {}
        self.replan_interval: float = 30.0
        self.last_replan_time: float = 0

        detection_types = [dt.value for dt in DetectionType]
        self.central_aggregator = CentralAggregator(detection_types)
        self.model_metadata: Dict[str, ModelMetadata] = {}

        self._on_task_assigned: Optional[Callable] = None
        self._on_alert: Optional[Callable] = None

        self._state_lock = asyncio.Lock()

    def set_zenoh_session(self, session):
        self.z_session = session

    def on_task_assigned(self, callback: Callable):
        self._on_task_assigned = callback

    def on_alert(self, callback: Callable):
        self._on_alert = callback

    async def update_drone_telemetry(self, telemetry: DroneTelemetry):
        async with self._state_lock:
            self.drones[telemetry.drone_id] = telemetry

            if telemetry.battery_percent < self.config.critical_battery_threshold:
                await self._raise_alert(
                    AlertLevel.CRITICAL,
                    f"Drone {telemetry.drone_id} critically low battery: {telemetry.battery_percent}%"
                )
            elif telemetry.battery_percent < self.config.min_battery_threshold:
                await self._raise_alert(
                    AlertLevel.WARNING,
                    f"Drone {telemetry.drone_id} low battery: {telemetry.battery_percent}%"
                )

    async def update_model_metadata(self, metadata: ModelMetadata):
        async with self._state_lock:
            self.model_metadata[metadata.drone_id] = metadata

    async def update_features(self, drone_id: str, features: bytes):
        async with self._state_lock:
            self.feature_cache[drone_id] = features
            self.feature_timestamps[drone_id] = time.time()

    async def _offline_replan(self):
        if time.time() - self.last_replan_time < self.replan_interval:
            return

        self.last_replan_time = time.time()

        async with self._state_lock:
            stale = 120.0
            now = time.time()
            active_features = {
                did: data for did, data in self.feature_cache.items()
                if now - self.feature_timestamps.get(did, 0) < stale
            }

            if not active_features:
                return

            coverage_scores: Dict[str, float] = {}
            for drone_id, feat_bytes in active_features.items():
                if len(feat_bytes) < 4:
                    continue
                arr = np.frombuffer(feat_bytes[:min(len(feat_bytes), 1024)], dtype=np.float32)
                coverage_scores[drone_id] = float(np.mean(np.abs(arr))) if len(arr) > 0 else 0.0

            if not coverage_scores:
                return

            low_coverage_drones = [
                did for did, score in coverage_scores.items()
                if score < 0.3 and did in self.drones
                and self.drones[did].role == DroneRole.SCOUT
            ]

            for det_id, detection in list(self.detections.items()):
                nearby_scout = None
                min_dist = float("inf")
                for did in low_coverage_drones:
                    if did not in self.drones:
                        continue
                    d = self.drones[did].position.distance_to(detection.position)
                    if d < min_dist:
                        min_dist = d
                        nearby_scout = did

                if nearby_scout and min_dist < 500:
                    task_id = f"task_{det_id}"
                    if task_id in self.tasks and self.tasks[task_id].priority < 8:
                        self.tasks[task_id].priority = min(10, self.tasks[task_id].priority + 2)

    async def add_detection(self, detection: Detection):
        async with self._state_lock:
            self.detections[detection.id] = detection
            self.central_aggregator.receive_detection(detection)

            priority = self._calculate_detection_priority(detection)

            heapq.heappush(
                self.detection_queue,
                PrioritizedDetection(priority=priority, detection=detection)
            )

            task = Task.from_detection(detection)
            self.tasks[task.id] = task

            await self._start_task_auction(task)

    def _calculate_detection_priority(self, detection: Detection) -> float:
        severity_score = (10 - detection.severity) * self.config.priority_weight_severity

        age_hours = (time.time() - detection.timestamp.timestamp()) / 3600
        age_score = -age_hours * self.config.priority_weight_age

        distance_score = 0.0
        executors = [d for d in self.drones.values()
                    if d.role == DroneRole.EXECUTOR and d.is_available()]
        if executors:
            min_distance = min(
                detection.position.distance_to(e.position)
                for e in executors
            )
            distance_score = min_distance * self.config.priority_weight_distance

        return severity_score + age_score + distance_score

    async def _start_task_auction(self, task: Task):
        await self.auction_manager.start_auction(task)

        if self.z_session:
            await self.z_session.put(
                ZenohKeys.TASK_AUCTION,
                json.dumps(task.to_dict())
            )

        asyncio.create_task(self._close_auction_after_timeout(task.id))

    async def _close_auction_after_timeout(self, task_id: str):
        await asyncio.sleep(self.config.auction_timeout_sec)

        winner = await self.auction_manager.close_auction(task_id)

        if winner:
            await self._assign_task(task_id, winner.drone_id)
        else:
            async with self._state_lock:
                if task_id in self.tasks:
                    self.tasks[task_id].status = TaskStatus.PENDING

    async def _assign_task(self, task_id: str, drone_id: str):
        async with self._state_lock:
            if task_id not in self.tasks:
                return

            task = self.tasks[task_id]
            task.assigned_to = drone_id
            task.status = TaskStatus.ASSIGNED

        if self.z_session:
            await self.z_session.put(
                ZenohKeys.task_assignment(task_id),
                json.dumps({
                    "task_id": task_id,
                    "assigned_to": drone_id,
                    "task": task.to_dict()
                })
            )

        if self._on_task_assigned:
            await self._on_task_assigned(task, drone_id)

    async def submit_bid(self, bid: TaskBid):
        success = await self.auction_manager.submit_bid(bid.task_id, bid)
        return success

    async def mark_task_complete(self, task_id: str, success: bool = True):
        async with self._state_lock:
            if task_id in self.tasks:
                task = self.tasks.pop(task_id)
                task.status = TaskStatus.COMPLETED if success else TaskStatus.FAILED
                self.completed_tasks[task_id] = task

    async def update_partitions(self):
        async with self._state_lock:
            scouts = {
                drone_id: drone
                for drone_id, drone in self.drones.items()
                if drone.role == DroneRole.SCOUT and drone.status != DroneStatus.OFFLINE
            }

            if len(scouts) < 1:
                return

            drone_positions = [
                (d.position.latitude, d.position.longitude)
                for d in scouts.values()
            ]

            partitions = self.field_partitioner.compute_partitions(
                drone_positions,
                lloyd_iterations=10
            )

            self.current_partitions = {}
            for (drone_id, _), partition in zip(scouts.items(), partitions):
                self.current_partitions[drone_id] = partition

            self.last_partition_update = time.time()

        if self.z_session:
            for drone_id, partition in self.current_partitions.items():
                scout = scouts[drone_id]
                path = self.path_planner.generate_path(
                    partition,
                    (scout.position.latitude, scout.position.longitude),
                    angle=0
                )

                await self.z_session.put(
                    ZenohKeys.scout_zone(drone_id),
                    json.dumps({
                        "drone_id": drone_id,
                        "partition": list(partition.exterior.coords),
                        "path": path,
                        "timestamp": time.time()
                    })
                )

    async def _raise_alert(self, level: AlertLevel, message: str):
        alert = {
            "level": level.value,
            "message": message,
            "timestamp": time.time()
        }

        if self._on_alert:
            await self._on_alert(alert)

        if self.z_session:
            await self.z_session.put(
                ZenohKeys.ALERT,
                json.dumps(alert)
            )

    def get_fleet_status(self) -> FleetStatus:
        status = FleetStatus()

        for drone in self.drones.values():
            status.total_drones += 1

            if drone.status == DroneStatus.OFFLINE:
                status.offline_count += 1
                continue

            if drone.battery_percent < self.config.min_battery_threshold:
                status.low_battery_count += 1

            if drone.role == DroneRole.SCOUT:
                if drone.status == DroneStatus.SCANNING:
                    status.scouts_active += 1
                else:
                    status.scouts_idle += 1
            else:
                if drone.status == DroneStatus.EXECUTING:
                    status.executors_active += 1
                elif drone.status == DroneStatus.RETURNING:
                    status.executors_returning += 1
                else:
                    status.executors_idle += 1

        status.total_detections = len(self.detections)
        status.pending_tasks = len([t for t in self.tasks.values()
                                   if t.status in (TaskStatus.PENDING, TaskStatus.ASSIGNED)])
        status.completed_tasks = len(self.completed_tasks)

        if status.offline_count > 0 or status.low_battery_count > status.total_drones * 0.3:
            status.alert_level = AlertLevel.WARNING
        elif status.low_battery_count > 0:
            status.alert_level = AlertLevel.CAUTION

        return status

    def get_mission_state(self) -> Dict[str, Any]:
        fleet_status = self.get_fleet_status()

        return {
            "phase": self.phase.value,
            "fleet": fleet_status.to_dict(),
            "drones": {
                drone_id: drone.to_dict()
                for drone_id, drone in self.drones.items()
            },
            "detections": [
                {**d.to_dict(), "priority": self._calculate_detection_priority(d)}
                for d in list(self.detections.values())[-100:]
            ],
            "active_tasks": [t.to_dict() for t in self.tasks.values()],
            "partitions": {
                drone_id: list(p.exterior.coords)
                for drone_id, p in self.current_partitions.items()
            },
            "field_boundary": self.config.field_boundary,
            "uptime_sec": time.time() - self.mission_start_time if self.mission_start_time else 0
        }

    async def start_mission(self):
        self.phase = MissionPhase.PLANNING
        self.mission_start_time = time.time()

        await self.update_partitions()

        self.phase = MissionPhase.ACTIVE

        if self.z_session:
            await self.z_session.put(
                ZenohKeys.MISSION_STATUS,
                json.dumps({"phase": self.phase.value, "timestamp": time.time()})
            )

    async def pause_mission(self):
        self.phase = MissionPhase.PAUSED

        if self.z_session:
            await self.z_session.put(
                ZenohKeys.COMMAND_BROADCAST,
                json.dumps({"command": "PAUSE", "timestamp": time.time()})
            )

    async def resume_mission(self):
        self.phase = MissionPhase.ACTIVE

        if self.z_session:
            await self.z_session.put(
                ZenohKeys.COMMAND_BROADCAST,
                json.dumps({"command": "RESUME", "timestamp": time.time()})
            )

    async def abort_mission(self):
        self.phase = MissionPhase.EMERGENCY

        if self.z_session:
            await self.z_session.put(
                ZenohKeys.COMMAND_BROADCAST,
                json.dumps({"command": "RTL", "priority": "EMERGENCY", "timestamp": time.time()})
            )

    async def run(self):
        self.mission_start_time = time.time()

        while self.phase not in (MissionPhase.COMPLETE, MissionPhase.EMERGENCY):
            try:
                if time.time() - self.last_partition_update > self.partition_update_interval:
                    await self.update_partitions()

                await self._process_detection_queue()

                await self._offline_replan()

                if self.central_aggregator.should_publish():
                    weights, version = self.central_aggregator.get_global_weights()
                    if self.z_session:
                        payload = struct.pack("<I", version) + weights
                        await self.z_session.put(
                            ZenohKeys.swarmnet_global_model(),
                            payload
                        )
                    status = self.central_aggregator.get_status()
                    if self.z_session:
                        await self.z_session.put(
                            ZenohKeys.swarmnet_status(),
                            json.dumps(status.model_dump(), default=str)
                        )

                await self.auction_manager.cleanup_expired()

                await self._cleanup_expired_detections()

                if self.z_session:
                    await self.z_session.put(
                        ZenohKeys.MISSION_STATUS,
                        json.dumps(self.get_mission_state())
                    )

                await asyncio.sleep(1.0)

            except Exception as e:
                print(f"Mission planner error: {e}")
                await asyncio.sleep(5.0)

    async def _process_detection_queue(self):
        async with self._state_lock:
            available_executors = [
                d for d in self.drones.values()
                if d.role == DroneRole.EXECUTOR and d.is_available()
            ]

            if not available_executors:
                return

            while self.detection_queue and available_executors:
                if not self.detection_queue:
                    break

                item = self.detection_queue[0]
                detection = item.detection

                task_id = f"task_{detection.id}"
                if task_id in self.tasks:
                    task = self.tasks[task_id]
                    if task.status not in (TaskStatus.PENDING,):
                        heapq.heappop(self.detection_queue)
                        continue

                break

    async def _cleanup_expired_detections(self):
        async with self._state_lock:
            current_time = time.time()
            expired = []

            for det_id, detection in self.detections.items():
                age = current_time - detection.timestamp.timestamp()
                if age > self.config.detection_expiry_sec:
                    expired.append(det_id)

            for det_id in expired:
                del self.detections[det_id]


async def main():
    import zenoh

    field_boundary = [
        (42.3601, -71.0589),
        (42.3601, -71.0550),
        (42.3570, -71.0550),
        (42.3570, -71.0589),
    ]

    home = GeoPosition(latitude=42.3585, longitude=-71.0570, altitude=0)

    config = MissionConfig(
        field_boundary=field_boundary,
        home_position=home
    )

    z_config = zenoh.Config()
    session = await zenoh.open(z_config)

    planner = MissionPlanner(config, session)

    async def on_telemetry(sample):
        data = json.loads(sample.payload.decode())
        telemetry = DroneTelemetry.from_dict(data)
        await planner.update_drone_telemetry(telemetry)

    async def on_detection(sample):
        data = json.loads(sample.payload.decode())
        detection = Detection.from_dict(data)
        await planner.add_detection(detection)

    async def on_bid(sample):
        data = json.loads(sample.payload.decode())
        bid = TaskBid.from_dict(data)
        await planner.submit_bid(bid)

    async def on_features(sample):
        key = sample.key_expr.as_str()
        parts = key.split("/")
        if len(parts) >= 4:
            drone_id = parts[2]
            await planner.update_features(drone_id, sample.payload)

    async def on_model_metadata(sample):
        data = json.loads(sample.payload.decode())
        metadata = ModelMetadata(**data)
        await planner.update_model_metadata(metadata)

    await session.declare_subscriber("olympus/swarm/*/telemetry", on_telemetry)
    await session.declare_subscriber("olympus/detection/**", on_detection)
    await session.declare_subscriber("olympus/task/*/bid", on_bid)
    await session.declare_subscriber(ZenohKeys.features_wildcard(), on_features)
    await session.declare_subscriber(ZenohKeys.model_metadata_wildcard(), on_model_metadata)

    print("OLYMPUS Mission Planner starting...")
    await planner.start_mission()
    await planner.run()


if __name__ == "__main__":
    asyncio.run(main())
