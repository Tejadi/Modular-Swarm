from __future__ import annotations

import asyncio
import json
import logging
import struct
from datetime import datetime
from typing import Callable, Optional, Any
from dataclasses import dataclass, field

import zenoh

from olympus_brain.protocol import (
    DroneRole,
    DroneStatus,
    DroneTelemetry,
    GeoPosition,
    BatteryState,
    Detection,
    Task,
    TaskBid,
    CommandMessage,
    DroneCommand,
    CommandType,
    ZenohKeys,
    ModelMetadata,
    TrustTier,
    CommandAuthority,
    RegistrationStatus,
    CapabilityManifest,
)

logger = logging.getLogger(__name__)


@dataclass
class NodeConfig:
    drone_id: str
    role: DroneRole
    zenoh_config: Optional[dict[str, Any]] = None
    telemetry_rate_hz: float = 5.0
    heartbeat_rate_hz: float = 0.5


@dataclass
class VehicleTrust:
    """Trust state for a registered vehicle."""
    tier: TrustTier = TrustTier.TRUSTED
    status: RegistrationStatus = RegistrationStatus.APPROVED
    manifest: Optional[CapabilityManifest] = None


@dataclass
class SwarmState:
    members: dict[str, DroneTelemetry] = field(default_factory=dict)
    detections: dict[str, Detection] = field(default_factory=dict)
    pending_tasks: dict[str, Task] = field(default_factory=dict)
    active_bids: dict[str, list[TaskBid]] = field(default_factory=dict)
    # Trust-tiered registration tracking
    trust_registry: dict[str, VehicleTrust] = field(default_factory=dict)

    def get_available_executors(self) -> list[DroneTelemetry]:
        return [
            t for t in self.members.values()
            if t.role == DroneRole.EXECUTOR and t.is_available()
        ]

    def get_scouts(self) -> list[DroneTelemetry]:
        return [
            t for t in self.members.values()
            if t.role == DroneRole.SCOUT
        ]

    def register_vehicle(
        self,
        vehicle_id: str,
        tier: TrustTier,
        status: RegistrationStatus,
        manifest: Optional[CapabilityManifest] = None,
    ) -> None:
        self.trust_registry[vehicle_id] = VehicleTrust(
            tier=tier, status=status, manifest=manifest,
        )

    def revoke_vehicle(self, vehicle_id: str) -> None:
        if vehicle_id in self.trust_registry:
            self.trust_registry[vehicle_id].status = RegistrationStatus.REVOKED

    def get_trust_tier(self, drone_id: str) -> TrustTier:
        entry = self.trust_registry.get(drone_id)
        if entry:
            return entry.tier
        return TrustTier.OBSERVER  # Fail-closed: unknown drones default to OBSERVER

    def is_command_allowed(self, sender_id: str, command_type: str) -> bool:
        """Check if sender is allowed to issue this command type."""
        # EMERGENCY_STOP always goes through — safety override
        if command_type == CommandType.EMERGENCY_STOP:
            return True

        entry = self.trust_registry.get(sender_id)
        if not entry:
            return False  # Fail-closed: unknown sender denied (except EMERGENCY_STOP above)

        if entry.status == RegistrationStatus.REVOKED:
            return False

        if entry.tier == TrustTier.OBSERVER:
            return False

        if entry.tier == TrustTier.PARTNER and entry.manifest:
            return command_type in entry.manifest.accepted_commands

        return True  # TRUSTED

    def get_cbba_participants(self) -> list[str]:
        """Return IDs of vehicles eligible for CBBA task allocation."""
        participants = []
        for vid, trust in self.trust_registry.items():
            if trust.status != RegistrationStatus.APPROVED:
                continue
            if trust.tier == TrustTier.TRUSTED:
                participants.append(vid)
            elif trust.tier == TrustTier.PARTNER and trust.manifest:
                if trust.manifest.participates_in_cbba:
                    participants.append(vid)
        # Fail-closed: unregistered members are NOT included in CBBA.
        # They must register first to participate in task allocation.
        return participants


class OlympusNode:

    def __init__(self, config: NodeConfig):
        self.config = config
        self.drone_id = config.drone_id
        self.role = config.role

        self._telemetry = DroneTelemetry(
            drone_id=self.drone_id,
            role=self.role,
        )
        self._swarm_state = SwarmState()
        self._running = False

        self._session: Optional[zenoh.Session] = None
        self._publishers: dict[str, zenoh.Publisher] = {}
        self._subscribers: list[zenoh.Subscriber] = []

        self._command_callbacks: list[Callable[[CommandMessage], None]] = []
        self._detection_callbacks: list[Callable[[Detection], None]] = []
        self._task_callbacks: list[Callable[[Task], None]] = []
        self._model_metadata_callbacks: list[Callable[[ModelMetadata], None]] = []
        self._global_model_callbacks: list[Callable[[bytes, int], None]] = []
        self._feature_callbacks: list[Callable[[str, bytes], None]] = []

        self._tasks: list[asyncio.Task] = []

    @property
    def telemetry(self) -> DroneTelemetry:
        return self._telemetry

    @property
    def swarm_state(self) -> SwarmState:
        return self._swarm_state

    async def start(self) -> None:
        logger.info(f"Starting OLYMPUS node: {self.drone_id} (role={self.role.value})")

        if self.config.zenoh_config:
            zenoh_cfg = zenoh.Config.from_json5(json.dumps(self.config.zenoh_config))
        else:
            zenoh_cfg = zenoh.Config()

        self._session = zenoh.open(zenoh_cfg)
        logger.info("Zenoh session opened")

        self._setup_publishers()
        self._setup_subscribers()

        self._running = True
        self._tasks.append(asyncio.create_task(self._telemetry_loop()))
        self._tasks.append(asyncio.create_task(self._state_cleanup_loop()))

        logger.info(f"OLYMPUS node {self.drone_id} started")

    async def stop(self) -> None:
        logger.info(f"Stopping OLYMPUS node: {self.drone_id}")
        self._running = False

        for task in self._tasks:
            task.cancel()

        for sub in self._subscribers:
            sub.undeclare()

        for pub in self._publishers.values():
            pub.undeclare()

        if self._session:
            self._session.close()

        logger.info(f"OLYMPUS node {self.drone_id} stopped")

    def _setup_publishers(self) -> None:
        key = ZenohKeys.telemetry(self.drone_id)
        self._publishers["telemetry"] = self._session.declare_publisher(key)
        logger.debug(f"Publisher created: {key}")

        if self.role == DroneRole.SCOUT:
            key = ZenohKeys.detection(self.drone_id)
            self._publishers["detection"] = self._session.declare_publisher(key)
            logger.debug(f"Publisher created: {key}")

            key = ZenohKeys.features(self.drone_id)
            self._publishers["features"] = self._session.declare_publisher(key)
            logger.debug(f"Publisher created: {key}")

        key = ZenohKeys.task_auction()
        self._publishers["task_auction"] = self._session.declare_publisher(key)

        key = ZenohKeys.command("*")
        self._publishers["command"] = self._session.declare_publisher(key)

        key = ZenohKeys.model_metadata(self.drone_id)
        self._publishers["model_metadata"] = self._session.declare_publisher(key)

    def _setup_subscribers(self) -> None:
        sub = self._session.declare_subscriber(
            ZenohKeys.telemetry_wildcard(),
            self._on_swarm_telemetry,
        )
        self._subscribers.append(sub)

        sub = self._session.declare_subscriber(
            ZenohKeys.command(self.drone_id),
            self._on_command,
        )
        self._subscribers.append(sub)

        sub = self._session.declare_subscriber(
            ZenohKeys.command("*"),
            self._on_command,
        )
        self._subscribers.append(sub)

        sub = self._session.declare_subscriber(
            ZenohKeys.detection_all(),
            self._on_detection,
        )
        self._subscribers.append(sub)

        sub = self._session.declare_subscriber(
            ZenohKeys.task_auction(),
            self._on_task_auction,
        )
        self._subscribers.append(sub)

        sub = self._session.declare_subscriber(
            ZenohKeys.model_metadata_wildcard(),
            self._on_model_metadata,
        )
        self._subscribers.append(sub)

        sub = self._session.declare_subscriber(
            ZenohKeys.swarmnet_global_model(),
            self._on_global_model,
        )
        self._subscribers.append(sub)

        # Tactical layer: peer intermediate features (LoRa mesh → Zenoh)
        sub = self._session.declare_subscriber(
            ZenohKeys.features_wildcard(),
            self._on_peer_features,
        )
        self._subscribers.append(sub)

        # Trust-tiered registration events
        sub = self._session.declare_subscriber(
            ZenohKeys.registry_wildcard(),
            self._on_registry_event,
        )
        self._subscribers.append(sub)

        logger.debug("All subscribers set up")

    def _on_swarm_telemetry(self, sample: zenoh.Sample) -> None:
        try:
            data = json.loads(sample.payload.to_bytes().decode())
            telem = DroneTelemetry(**data)

            if telem.drone_id != self.drone_id:
                self._swarm_state.members[telem.drone_id] = telem
                logger.debug(f"Updated swarm member: {telem.drone_id}")
        except Exception as e:
            logger.warning(f"Failed to parse telemetry: {e}")

    def _on_command(self, sample: zenoh.Sample) -> None:
        try:
            data = json.loads(sample.payload.to_bytes().decode())
            cmd = CommandMessage(**data)

            if cmd.target_drone != "*" and cmd.target_drone != self.drone_id:
                return

            # Trust-tier command filtering
            sender = cmd.issued_by
            cmd_type = cmd.command.type
            if not self._swarm_state.is_command_allowed(sender, cmd_type):
                tier = self._swarm_state.get_trust_tier(sender)
                logger.warning(
                    f"Dropped command {cmd_type} from {sender} "
                    f"(tier={tier.value}, not allowed)"
                )
                return

            logger.info(f"Received command: {cmd.command.type}")

            self._process_command(cmd)

            for callback in self._command_callbacks:
                try:
                    callback(cmd)
                except Exception as e:
                    logger.error(f"Command callback error: {e}")

        except Exception as e:
            logger.warning(f"Failed to parse command: {e}")

    def _on_detection(self, sample: zenoh.Sample) -> None:
        try:
            data = json.loads(sample.payload.to_bytes().decode())
            detection = Detection(**data)

            self._swarm_state.detections[detection.id] = detection
            logger.info(
                f"Detection: {detection.detection_type.value} at "
                f"({detection.position.latitude:.5f}, {detection.position.longitude:.5f})"
            )

            for callback in self._detection_callbacks:
                try:
                    callback(detection)
                except Exception as e:
                    logger.error(f"Detection callback error: {e}")

        except Exception as e:
            logger.warning(f"Failed to parse detection: {e}")

    def _on_task_auction(self, sample: zenoh.Sample) -> None:
        try:
            data = json.loads(sample.payload.to_bytes().decode())
            task = Task(**data)

            self._swarm_state.pending_tasks[task.id] = task
            logger.info(f"Task auction: {task.id} type={task.task_type.value}")

            for callback in self._task_callbacks:
                try:
                    callback(task)
                except Exception as e:
                    logger.error(f"Task callback error: {e}")

        except Exception as e:
            logger.warning(f"Failed to parse task: {e}")

    def _on_model_metadata(self, sample: zenoh.Sample) -> None:
        try:
            data = json.loads(sample.payload.to_bytes().decode())
            metadata = ModelMetadata(**data)

            if metadata.drone_id == self.drone_id:
                return

            for callback in self._model_metadata_callbacks:
                try:
                    callback(metadata)
                except Exception as e:
                    logger.error(f"Model metadata callback error: {e}")

        except Exception as e:
            logger.warning(f"Failed to parse model metadata: {e}")

    def _on_global_model(self, sample: zenoh.Sample) -> None:
        try:
            raw = sample.payload.to_bytes()
            if len(raw) < 4:
                return
            version = struct.unpack("<I", raw[:4])[0]
            weights = raw[4:]

            logger.info(f"Received global model v{version}")

            for callback in self._global_model_callbacks:
                try:
                    callback(weights, version)
                except Exception as e:
                    logger.error(f"Global model callback error: {e}")

        except Exception as e:
            logger.warning(f"Failed to process global model: {e}")

    def _on_peer_features(self, sample: zenoh.Sample) -> None:
        """Handle intermediate features from peer drones (tactical layer)."""
        try:
            key_str = str(sample.key_expr)
            # Extract drone_id from key: olympus/swarm/{drone_id}/features
            parts = key_str.split("/")
            if len(parts) >= 4:
                sender_id = parts[2]
            else:
                return

            if sender_id == self.drone_id:
                return

            raw = sample.payload.to_bytes()
            if not raw:
                return

            for callback in self._feature_callbacks:
                try:
                    callback(sender_id, raw)
                except Exception as e:
                    logger.error(f"Feature callback error: {e}")

        except Exception as e:
            logger.warning(f"Failed to process peer features: {e}")

    def _on_registry_event(self, sample: zenoh.Sample) -> None:
        """Handle trust-tiered registration events from the Vehicle API."""
        try:
            data = json.loads(sample.payload.to_bytes().decode())
            action = data.get("action", "")
            vehicle_id = data.get("vehicle_id", "")
            if not vehicle_id:
                return

            if action == "registered" or action == "approved":
                tier_str = data.get("trust_tier", "trusted")
                try:
                    tier = TrustTier(tier_str)
                except ValueError:
                    tier = TrustTier.TRUSTED

                status_str = data.get("registration_status", "approved")
                if action == "approved":
                    status = RegistrationStatus.APPROVED
                else:
                    try:
                        status = RegistrationStatus(status_str)
                    except ValueError:
                        status = RegistrationStatus.APPROVED

                # Parse capability manifest if present
                manifest = None
                caps = data.get("capabilities")
                if caps and isinstance(caps, dict):
                    try:
                        manifest = CapabilityManifest(**caps)
                    except Exception:
                        pass

                self._swarm_state.register_vehicle(vehicle_id, tier, status, manifest)
                logger.info(
                    f"Registry: {action} vehicle {vehicle_id} "
                    f"(tier={tier.value}, status={status.value})"
                )

            elif action == "rejected":
                self._swarm_state.register_vehicle(
                    vehicle_id, TrustTier.PARTNER, RegistrationStatus.REJECTED,
                )
                logger.info(f"Registry: rejected vehicle {vehicle_id}")

            elif action == "revoked":
                self._swarm_state.revoke_vehicle(vehicle_id)
                logger.warning(f"Registry: REVOKED vehicle {vehicle_id}")

        except Exception as e:
            logger.warning(f"Failed to process registry event: {e}")

    def _process_command(self, cmd: CommandMessage) -> None:
        match cmd.command.type:
            case CommandType.EMERGENCY_STOP:
                logger.critical("EMERGENCY STOP RECEIVED")
                self._telemetry.status = DroneStatus.EMERGENCY

            case CommandType.RETURN_TO_LAUNCH:
                logger.info("RTL command received")
                self._telemetry.status = DroneStatus.RETURNING

            case CommandType.PAUSE:
                logger.info("Pause command received")
                self._telemetry.status = DroneStatus.IDLE

            case CommandType.RESUME:
                logger.info("Resume command received")
                if self.role == DroneRole.SCOUT:
                    self._telemetry.status = DroneStatus.SCANNING
                else:
                    self._telemetry.status = DroneStatus.IDLE

            case CommandType.RECALL_FOR_UPDATE:
                logger.warning("RECALL FOR UPDATE — pausing for model update")
                self._telemetry.status = DroneStatus.RETURNING

            case CommandType.REDEPLOY:
                logger.info("REDEPLOY — resuming operations")
                if self.role == DroneRole.SCOUT:
                    self._telemetry.status = DroneStatus.SCANNING
                else:
                    self._telemetry.status = DroneStatus.IDLE

            case _:
                logger.debug(f"Unhandled command type: {cmd.command.type}")

    async def _telemetry_loop(self) -> None:
        period = 1.0 / self.config.telemetry_rate_hz

        while self._running:
            try:
                self._telemetry.timestamp = datetime.utcnow()

                payload = self._telemetry.model_dump_json().encode()
                self._publishers["telemetry"].put(payload)

                await asyncio.sleep(period)
            except Exception as e:
                logger.error(f"Telemetry publish error: {e}")
                await asyncio.sleep(1.0)

    async def _state_cleanup_loop(self) -> None:
        while self._running:
            try:
                now = datetime.utcnow()
                timeout_seconds = 30

                for drone_id, telem in list(self._swarm_state.members.items()):
                    age = (now - telem.timestamp).total_seconds()
                    if age > timeout_seconds:
                        if telem.status != DroneStatus.OFFLINE:
                            logger.warning(f"Peer {drone_id} timed out ({age:.0f}s)")
                            telem.status = DroneStatus.OFFLINE

                await asyncio.sleep(5.0)
            except Exception as e:
                logger.error(f"State cleanup error: {e}")
                await asyncio.sleep(5.0)

    def update_position(self, position: GeoPosition) -> None:
        self._telemetry.position = position

    def update_battery(self, battery: BatteryState) -> None:
        self._telemetry.battery = battery

    def update_status(self, status: DroneStatus) -> None:
        self._telemetry.status = status

    def publish_detection(self, detection: Detection) -> None:
        if "detection" not in self._publishers:
            logger.warning("Detection publisher not available")
            return

        payload = detection.model_dump_json().encode()
        self._publishers["detection"].put(payload)
        logger.info(f"Published detection: {detection.id}")

    def publish_task(self, task: Task) -> None:
        payload = task.model_dump_json().encode()
        self._publishers["task_auction"].put(payload)
        logger.info(f"Published task auction: {task.id}")

    def send_command(
        self,
        target: str,
        command_type: CommandType,
        payload: Optional[dict] = None,
    ) -> None:
        # Enforce trust tier on outbound commands (skip for broadcast "*")
        if target != "*" and command_type != CommandType.EMERGENCY_STOP:
            entry = self._swarm_state.trust_registry.get(target)
            if entry and entry.status == RegistrationStatus.REVOKED:
                logger.warning(
                    f"Blocked command {command_type.value} to revoked vehicle {target}"
                )
                return

        cmd = CommandMessage(
            target_drone=target,
            command=DroneCommand(type=command_type, payload=payload),
            issued_by=self.drone_id,
        )

        data = cmd.model_dump_json().encode()
        self._publishers["command"].put(data)
        logger.info(f"Sent command {command_type.value} to {target}")

    def on_command(self, callback: Callable[[CommandMessage], None]) -> None:
        self._command_callbacks.append(callback)

    def on_detection(self, callback: Callable[[Detection], None]) -> None:
        self._detection_callbacks.append(callback)

    def on_task(self, callback: Callable[[Task], None]) -> None:
        self._task_callbacks.append(callback)

    def on_model_metadata(self, callback: Callable[[ModelMetadata], None]) -> None:
        self._model_metadata_callbacks.append(callback)

    def on_global_model(self, callback: Callable[[bytes, int], None]) -> None:
        self._global_model_callbacks.append(callback)

    def on_features(self, callback: Callable[[str, bytes], None]) -> None:
        """Register callback for peer intermediate features (tactical layer)."""
        self._feature_callbacks.append(callback)

    def publish_features(self, data: bytes) -> None:
        """Publish intermediate features for tactical P2P sharing via LoRa mesh."""
        if "features" not in self._publishers:
            return
        self._publishers["features"].put(data)

    def publish_model_metadata(self, metadata: ModelMetadata) -> None:
        if "model_metadata" not in self._publishers:
            return
        payload = metadata.model_dump_json().encode()
        self._publishers["model_metadata"].put(payload)

