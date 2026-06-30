from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional, Any, Dict, List
from pydantic import BaseModel, Field
import uuid
import math


class DroneRole(str, Enum):
    SCOUT = "scout"
    EXECUTOR = "executor"


class DroneStatus(str, Enum):
    IDLE = "idle"
    SCANNING = "scanning"
    TRANSITING = "transiting"
    EXECUTING = "executing"
    RETURNING = "returning"
    CHARGING = "charging"
    EMERGENCY = "emergency"
    OFFLINE = "offline"


class DetectionType(str, Enum):
    WEED = "weed"
    PEST = "pest"
    DISEASE = "disease"
    NUTRIENT_DEFICIENCY = "nutrient_deficiency"
    IRRIGATION_LEAK = "irrigation_leak"
    CROP_STRESS = "crop_stress"
    OBSTACLE = "obstacle"
    HOSTILE_ACTIVITY = "hostile_activity"
    VEHICLE_DETECTED = "vehicle_detected"
    PERSON_DETECTED = "person_detected"
    IED_SUSPECTED = "ied_suspected"
    STRUCTURAL_CHANGE = "structural_change"
    STRUCTURAL_CRACK = "structural_crack"
    CORROSION = "corrosion"
    THERMAL_ANOMALY = "thermal_anomaly"
    LEAK_DETECTED = "leak_detected"
    VEGETATION_ENCROACHMENT = "vegetation_encroachment"
    SURFACE_DEFORMATION = "surface_deformation"
    THERMAL_SIGNATURE = "thermal_signature"
    DEBRIS_FIELD = "debris_field"
    VEHICLE_WRECKAGE = "vehicle_wreckage"
    SIGNAL_DETECTED = "signal_detected"


class TaskType(str, Enum):
    SPRAY = "spray"
    SEED = "seed"
    FERTILIZE = "fertilize"
    INSPECT = "inspect"
    SAMPLE = "sample"
    INVESTIGATE = "investigate"
    MARK = "mark"
    PHOTOGRAPH = "photograph"
    RELAY = "relay"
    THERMAL_SCAN = "thermal_scan"
    MEASURE = "measure"
    DROP_SUPPLIES = "drop_supplies"
    MARK_LOCATION = "mark_location"
    RELAY_COMMS = "relay_comms"


class TaskState(str, Enum):
    PENDING = "pending"
    AUCTIONED = "auctioned"
    ASSIGNED = "assigned"
    AWAITING_APPROVAL = "awaiting_approval"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EscalationLevel(str, Enum):
    AUTO = "auto"                       # Execute without human input
    NOTIFY = "notify"                   # Execute and notify human
    APPROVE_REQUIRED = "approve_required"  # Wait for human approval (with timeout)
    EMERGENCY = "emergency"             # Immediate human attention required


class TrustTier(str, Enum):
    TRUSTED = "trusted"      # Own agents — full command authority, auto-approved
    PARTNER = "partner"      # Peer vehicles — negotiated capabilities, requires approval
    OBSERVER = "observer"    # Read-only telemetry consumers, no command authority


class CommandAuthority(str, Enum):
    BINDING = "binding"      # Commands executed directly (own fleet)
    ADVISORY = "advisory"    # Commands are suggestions (partner decides)
    NONE = "none"            # No command authority (observer)


class RegistrationStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"


class CapabilityManifest(BaseModel):
    """Declares what a vehicle provides and accepts on the network."""
    provides_telemetry: bool = True
    provides_detections: bool = False
    provides_features: bool = False       # P2P intermediate features via LoRa
    accepted_commands: List[str] = Field(default_factory=list)
    command_authority: CommandAuthority = CommandAuthority.NONE
    participates_in_cbba: bool = False    # Can receive task assignments?
    ttl_seconds: int = 3600              # Registration validity (0 = no expiry)
    data_encryption_required: bool = False


class GeoPosition(BaseModel):
    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0
    heading: float = 0.0

    def distance_to(self, other: GeoPosition) -> float:
        R = 6_371_000

        lat1 = math.radians(self.latitude)
        lat2 = math.radians(other.latitude)
        delta_lat = math.radians(other.latitude - self.latitude)
        delta_lon = math.radians(other.longitude - self.longitude)

        a = (math.sin(delta_lat / 2) ** 2 +
             math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2)
        c = 2 * math.asin(math.sqrt(a))

        return R * c

    def bearing_to(self, other: GeoPosition) -> float:
        lat1 = math.radians(self.latitude)
        lat2 = math.radians(other.latitude)
        delta_lon = math.radians(other.longitude - self.longitude)

        x = math.sin(delta_lon) * math.cos(lat2)
        y = (math.cos(lat1) * math.sin(lat2) -
             math.sin(lat1) * math.cos(lat2) * math.cos(delta_lon))

        bearing = math.degrees(math.atan2(x, y))
        return (bearing + 360) % 360

    def to_tuple(self) -> tuple[float, float]:
        return (self.latitude, self.longitude)

    @classmethod
    def from_tuple(cls, coords: tuple[float, float], altitude: float = 0.0) -> GeoPosition:
        return cls(latitude=coords[0], longitude=coords[1], altitude=altitude)


class BatteryState(BaseModel):
    voltage: float = 0.0
    current: float = 0.0
    percentage: int = Field(default=100, ge=0, le=100)
    remaining_time: int = 0
    cell_count: int = 6
    temperature: float = 25.0


class DroneTelemetry(BaseModel):
    drone_id: str
    role: DroneRole
    status: DroneStatus = DroneStatus.IDLE
    position: GeoPosition = Field(default_factory=GeoPosition)
    battery: BatteryState = Field(default_factory=BatteryState)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    mesh_rssi: int = 0
    wifi_connected: bool = False
    current_task_id: Optional[str] = None

    def is_available(self) -> bool:
        return (
            self.status in [DroneStatus.IDLE, DroneStatus.SCANNING] and
            self.battery.percentage > 20 and
            self.current_task_id is None
        )


class Detection(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    detection_type: DetectionType
    position: GeoPosition
    confidence: float = Field(ge=0.0, le=1.0)
    severity: int = Field(default=5, ge=1, le=10)
    detected_by: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    image_ref: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None

    @classmethod
    def create(
        cls,
        detection_type: DetectionType,
        position: GeoPosition,
        confidence: float,
        detected_by: str,
        severity: int = 5,
    ) -> Detection:
        return cls(
            detection_type=detection_type,
            position=position,
            confidence=confidence,
            severity=severity,
            detected_by=detected_by,
        )


class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_type: TaskType
    target_position: GeoPosition
    priority: int = Field(default=5, ge=1, le=10)
    state: TaskState = TaskState.PENDING
    created_by: str
    assigned_to: Optional[str] = None
    detection_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    deadline: Optional[datetime] = None
    payload_required: Optional[str] = None
    escalation_level: EscalationLevel = EscalationLevel.AUTO

    @classmethod
    def from_detection(cls, detection: Detection, task_type: TaskType) -> Task:
        return cls(
            task_type=task_type,
            target_position=detection.position,
            priority=detection.severity,
            created_by=detection.detected_by,
            detection_id=detection.id,
        )


class EscalationRequest(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str
    drone_id: str
    escalation_level: EscalationLevel
    reason: str
    recommended_action: str = "proceed"
    timeout_action: str = "proceed"  # What to do if human doesn't respond
    timeout_seconds: int = 30
    created_at: datetime = Field(default_factory=datetime.utcnow)
    resolved: bool = False
    resolution: Optional[str] = None  # "approved", "denied", "modified", "timeout"


class EscalationResponse(BaseModel):
    escalation_id: str
    approved: bool
    modified_action: Optional[str] = None
    responded_by: str = "operator"
    responded_at: datetime = Field(default_factory=datetime.utcnow)


class TaskBid(BaseModel):
    task_id: str
    bidder_id: str
    cost: float
    eta_seconds: int
    battery_after: int
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class CommandType(str, Enum):
    EMERGENCY_STOP = "emergency_stop"
    RETURN_TO_LAUNCH = "return_to_launch"
    GO_TO = "go_to"
    START_SCAN = "start_scan"
    PAUSE = "pause"
    RESUME = "resume"
    EXECUTE_TASK = "execute_task"
    SET_ALTITUDE = "set_altitude"
    ACK = "ack"
    RECALL_FOR_UPDATE = "recall_for_update"
    REDEPLOY = "redeploy"
    UPDATE_MODEL = "update_model"
    MODEL_ACK = "model_ack"  # Drone confirms model weights received + loaded


class DroneCommand(BaseModel):
    type: CommandType
    payload: Optional[dict[str, Any]] = None


class CommandMessage(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    target_drone: str
    command: DroneCommand
    issued_by: str
    issued_at: datetime = Field(default_factory=datetime.utcnow)
    priority: int = Field(default=5, ge=1, le=10)

    @classmethod
    def emergency_stop_all(cls, issued_by: str) -> CommandMessage:
        return cls(
            target_drone="*",
            command=DroneCommand(type=CommandType.EMERGENCY_STOP),
            issued_by=issued_by,
            priority=10,
        )


class ZenohKeys:
    PREFIX = "olympus"

    @staticmethod
    def telemetry(drone_id: str) -> str:
        return f"{ZenohKeys.PREFIX}/swarm/{drone_id}/telemetry"

    @staticmethod
    def telemetry_wildcard() -> str:
        return f"{ZenohKeys.PREFIX}/swarm/*/telemetry"

    @staticmethod
    def heartbeat(drone_id: str) -> str:
        return f"{ZenohKeys.PREFIX}/swarm/{drone_id}/heartbeat"

    @staticmethod
    def detection(drone_id: str) -> str:
        return f"{ZenohKeys.PREFIX}/detection/{drone_id}"

    @staticmethod
    def detection_all() -> str:
        return f"{ZenohKeys.PREFIX}/detection/**"

    @staticmethod
    def task_auction() -> str:
        return f"{ZenohKeys.PREFIX}/task/auction"

    @staticmethod
    def task_bid(task_id: str) -> str:
        return f"{ZenohKeys.PREFIX}/task/{task_id}/bid"

    @staticmethod
    def task_award(task_id: str) -> str:
        return f"{ZenohKeys.PREFIX}/task/{task_id}/award"

    @staticmethod
    def command(drone_id: str) -> str:
        return f"{ZenohKeys.PREFIX}/command/{drone_id}"

    @staticmethod
    def command_broadcast() -> str:
        return f"{ZenohKeys.PREFIX}/command/*"

    @staticmethod
    def zone_assignment(drone_id: str) -> str:
        return f"{ZenohKeys.PREFIX}/zone/{drone_id}"

    @staticmethod
    def features(drone_id: str) -> str:
        return f"{ZenohKeys.PREFIX}/swarm/{drone_id}/features"

    @staticmethod
    def features_wildcard() -> str:
        return f"{ZenohKeys.PREFIX}/swarm/*/features"

    @staticmethod
    def cbba_bundle(executor_id: str) -> str:
        return f"{ZenohKeys.PREFIX}/cbba/{executor_id}/bundle"

    @staticmethod
    def model_metadata(drone_id: str) -> str:
        return f"{ZenohKeys.PREFIX}/swarm/{drone_id}/model/metadata"

    @staticmethod
    def model_metadata_wildcard() -> str:
        return f"{ZenohKeys.PREFIX}/swarm/*/model/metadata"

    @staticmethod
    def swarmnet_global_model() -> str:
        return f"{ZenohKeys.PREFIX}/swarmnet/model/global"

    @staticmethod
    def swarmnet_status() -> str:
        return f"{ZenohKeys.PREFIX}/swarmnet/status"

    @staticmethod
    def elrs_rx(drone_id: str) -> str:
        return f"{ZenohKeys.PREFIX}/elrs/{drone_id}/rx"

    @staticmethod
    def elrs_link(drone_id: str) -> str:
        return f"{ZenohKeys.PREFIX}/swarm/{drone_id}/elrs/link"

    @staticmethod
    def elrs_link_wildcard() -> str:
        return f"{ZenohKeys.PREFIX}/swarm/*/elrs/link"

    @staticmethod
    def escalation(drone_id: str) -> str:
        return f"{ZenohKeys.PREFIX}/escalation/{drone_id}"

    @staticmethod
    def escalation_wildcard() -> str:
        return f"{ZenohKeys.PREFIX}/escalation/*"

    @staticmethod
    def escalation_response() -> str:
        return f"{ZenohKeys.PREFIX}/escalation/response"

    @staticmethod
    def comms_status(drone_id: str) -> str:
        return f"{ZenohKeys.PREFIX}/swarm/{drone_id}/comms"

    @staticmethod
    def registry(vehicle_id: str) -> str:
        return f"{ZenohKeys.PREFIX}/registry/{vehicle_id}"

    @staticmethod
    def registry_wildcard() -> str:
        return f"{ZenohKeys.PREFIX}/registry/*"


class ModelMetadata(BaseModel):
    drone_id: str
    model_version: int = 0
    accuracy: float = 0.0
    label_distribution: Dict[str, int] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class SwarmNetStatus(BaseModel):
    active_drones: int = 0
    model_versions: Dict[str, int] = Field(default_factory=dict)
    accuracies: Dict[str, float] = Field(default_factory=dict)
    contributions: Dict[str, int] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
