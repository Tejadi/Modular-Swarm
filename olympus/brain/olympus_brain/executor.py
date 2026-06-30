from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional
from enum import Enum

from olympus_brain.node import OlympusNode, NodeConfig
from olympus_brain.protocol import (
    CommandType,
    CommandMessage,
    DroneRole,
    DroneStatus,
    GeoPosition,
    Task,
    TaskType,
    TaskState,
    TaskBid,
    ZenohKeys,
    EscalationLevel,
    EscalationRequest,
    EscalationResponse,
)
from olympus_brain.mission_profile import load_profile
from olympus_brain.escalation import EscalationEngine, SwarmContext
from olympus_brain.cbba import CBBAAllocator, ExecutorSnapshot

logger = logging.getLogger(__name__)


class ExecutorState(Enum):
    IDLE = "idle"
    BIDDING = "bidding"
    AWAITING_APPROVAL = "awaiting_approval"
    TRANSITING = "transiting"
    EXECUTING = "executing"
    RETURNING = "returning"
    RECALLED = "recalled"  # Recalled for model update — task cancelled, holding safe


class ExecutorConfig:

    def __init__(
        self,
        drone_id: str,
        home_position: tuple[float, float],
        transit_altitude: float = 50.0,
        working_altitude: float = 5.0,
        transit_speed_mps: float = 15.0,
        working_speed_mps: float = 5.0,
        payload_rate: float = None,
        payload_capacity: float = None,
        min_battery_percent: int = 20,
        capabilities: Optional[list[str]] = None,
        vertical: str = "CERES",
    ):
        profile = load_profile()
        self.drone_id = drone_id
        self.home_position = GeoPosition(
            latitude=home_position[0],
            longitude=home_position[1],
            altitude=0.0,
        )
        self.transit_altitude = transit_altitude
        self.working_altitude = working_altitude
        self.transit_speed_mps = transit_speed_mps
        self.working_speed_mps = working_speed_mps
        self.payload_rate = payload_rate or profile.payload.rate
        self.payload_capacity = payload_capacity or profile.payload.capacity
        self.payload_unit = profile.payload.unit
        self.min_battery_percent = min_battery_percent
        self.spray_rate_lpm = self.payload_rate
        self.tank_capacity_liters = self.payload_capacity
        self.capabilities = capabilities or [t.value for t in TaskType]
        self.vertical = vertical


class ExecutorDrone:

    def __init__(self, config: ExecutorConfig):
        self.config = config
        self.profile = load_profile()

        node_config = NodeConfig(
            drone_id=config.drone_id,
            role=DroneRole.EXECUTOR,
        )
        self.node = OlympusNode(node_config)

        self.state = ExecutorState.IDLE
        self.current_task: Optional[Task] = None
        self.pending_bids: dict[str, TaskBid] = {}
        self.payload_level: float = config.payload_capacity
        self.tank_level_liters: float = config.payload_capacity

        self.node.on_task(self._on_task_received)

        self._escalation_engine = EscalationEngine(vertical=config.vertical)
        self._pending_escalation: Optional[EscalationRequest] = None
        self._escalation_response: Optional[EscalationResponse] = None

        self._cbba = CBBAAllocator(
            drone_id=config.drone_id,
            max_bundle_size=3,
        )

        self._running = False
        self._main_task: Optional[asyncio.Task] = None
        self._recalled = False  # True when RECALL_FOR_UPDATE received

    def _sync_payload_aliases(self) -> None:
        self.tank_level_liters = self.payload_level

    @property
    def is_available(self) -> bool:
        min_payload = self.config.payload_capacity * 0.05
        return (
            self.state == ExecutorState.IDLE and
            self.node.telemetry.battery.percentage > self.config.min_battery_percent and
            self.payload_level > min_payload
        )

    def to_cbba_snapshot(self) -> ExecutorSnapshot:
        """Create a CBBA executor snapshot with this drone's capabilities."""
        return ExecutorSnapshot.from_telemetry(
            telemetry=self.node.telemetry,
            payload_level=self.payload_level,
            payload_capacity=self.config.payload_capacity,
            capable_task_types=list(self.config.capabilities),
        )

    async def start(self) -> None:
        logger.info(f"Starting Executor drone: {self.config.drone_id}")

        await self.node.start()

        self.node.update_position(self.config.home_position)
        self.node.update_status(DroneStatus.IDLE)

        # Register command handler for RECALL/REDEPLOY
        self.node.on_command(self._on_command_received)

        self._running = True
        self._main_task = asyncio.create_task(self._main_loop())

        logger.info(f"Executor {self.config.drone_id} ready ({self.profile.name})")

    async def stop(self) -> None:
        logger.info(f"Stopping Executor drone: {self.config.drone_id}")

        self._running = False

        if self._main_task:
            self._main_task.cancel()
            try:
                await self._main_task
            except asyncio.CancelledError:
                pass

        await self.node.stop()

    def _on_task_received(self, task: Task) -> None:
        if self._recalled:
            logger.debug(f"Ignoring task {task.id} — recalled for model update")
            return

        if not self.is_available:
            logger.debug(f"Not available for task {task.id}")
            return

        # Update CBBA with current available executor count for ROE buddy-system
        available = len(self.node.swarm_state.get_available_executors()) + (
            1 if self.is_available else 0
        )
        self._cbba.set_available_drone_count(available)

        bid = self._calculate_bid(task)

        if bid:
            self.pending_bids[task.id] = bid
            logger.info(f"Calculated bid for task {task.id}: cost={bid.cost:.1f}")

            asyncio.create_task(self._submit_bid(bid))

    def _on_command_received(self, cmd: CommandMessage) -> None:
        """Handle RECALL_FOR_UPDATE and REDEPLOY commands."""
        if cmd.command.type == CommandType.RECALL_FOR_UPDATE:
            logger.warning(
                f"RECALL_FOR_UPDATE received — cancelling current task, entering safe state"
            )
            self._handle_recall()
        elif cmd.command.type == CommandType.REDEPLOY:
            if self._recalled:
                logger.info("REDEPLOY received — resuming operations")
                self._recalled = False
                self.state = ExecutorState.IDLE

    def _handle_recall(self) -> None:
        """Cancel in-flight task and transition to safe-state for model update.

        - If EXECUTING or TRANSITING: cancel current task, hold position
        - If AWAITING_APPROVAL: cancel pending escalation
        - If IDLE/BIDDING: just set recalled flag
        """
        self._recalled = True

        # Cancel current task if one exists
        if self.current_task:
            logger.warning(
                f"Cancelling in-flight task {self.current_task.id} "
                f"(was {self.state.value}) for model recall"
            )
            self.current_task.state = TaskState.CANCELLED
            self.current_task = None

        # Clear pending escalation
        if self._pending_escalation:
            self._pending_escalation = None
            self._escalation_response = None

        # Clear pending bids
        self.pending_bids.clear()

        # Enter RECALLED state — drone holds position and waits for REDEPLOY
        self.state = ExecutorState.RECALLED
        self.node.update_status(DroneStatus.RETURNING)

    def _calculate_bid(self, task: Task) -> Optional[TaskBid]:
        # Capability check — skip tasks this executor can't handle
        if task.task_type.value not in self.config.capabilities:
            return None

        current_pos = self.node.telemetry.position
        target_pos = task.target_position

        distance = current_pos.distance_to(target_pos)

        battery_pct = self.node.telemetry.battery.percentage
        battery_cost = max(0, (30 - battery_pct)) * 10

        payload_pct = self.payload_level / self.config.payload_capacity if self.config.payload_capacity > 0 else 1.0
        payload_cost = max(0, (0.2 - payload_pct)) * 500

        task_type_str = task.task_type.value
        exec_params = self.profile.task_execution.get(task_type_str)
        if exec_params and exec_params.requires_payload:
            min_payload = self.config.payload_capacity * 0.1
            if self.payload_level < min_payload:
                return None

        total_cost = distance + battery_cost + payload_cost

        transit_time = distance / self.config.transit_speed_mps
        execution_time = exec_params.duration if exec_params else 10.0
        eta = int(transit_time + execution_time)

        battery_consumption = (transit_time + execution_time) * 0.05
        battery_after = max(0, battery_pct - int(battery_consumption))

        return TaskBid(
            task_id=task.id,
            bidder_id=self.config.drone_id,
            cost=total_cost,
            eta_seconds=eta,
            battery_after=battery_after,
        )

    async def _submit_bid(self, bid: TaskBid) -> None:
        try:
            key = ZenohKeys.task_bid(bid.task_id)
            logger.info(f"Bid submitted: task={bid.task_id} cost={bid.cost:.1f}")
        except Exception as e:
            logger.error(f"Failed to submit bid: {e}")

    async def _main_loop(self) -> None:
        while self._running:
            try:
                match self.state:
                    case ExecutorState.IDLE:
                        await self._handle_idle()

                    case ExecutorState.BIDDING:
                        await self._handle_bidding()

                    case ExecutorState.AWAITING_APPROVAL:
                        await self._handle_awaiting_approval()

                    case ExecutorState.TRANSITING:
                        await self._handle_transiting()

                    case ExecutorState.EXECUTING:
                        await self._handle_executing()

                    case ExecutorState.RETURNING:
                        await self._handle_returning()

                    case ExecutorState.RECALLED:
                        await self._handle_recalled()

                await asyncio.sleep(0.1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Main loop error: {e}")
                await asyncio.sleep(1.0)

    async def _handle_idle(self) -> None:
        self.node.update_status(DroneStatus.IDLE)

        refill_threshold = self.config.payload_capacity * 0.10
        if self.payload_level < refill_threshold:
            current_pos = self.node.telemetry.position
            distance_to_home = current_pos.distance_to(self.config.home_position)
            if distance_to_home > 10:
                logger.info(f"Low payload ({self.payload_level:.1f}{self.config.payload_unit}), returning home")
                self.state = ExecutorState.RETURNING

        await asyncio.sleep(1.0)

    async def _handle_bidding(self) -> None:
        await asyncio.sleep(0.5)

    async def _handle_awaiting_approval(self) -> None:
        """Wait for human approval or timeout."""
        if not self._pending_escalation or not self.current_task:
            self.state = ExecutorState.IDLE
            return

        esc = self._pending_escalation
        elapsed = (datetime.utcnow() - esc.created_at).total_seconds()

        # Check for response
        if self._escalation_response and self._escalation_response.escalation_id == esc.id:
            resp = self._escalation_response
            self._escalation_response = None
            self._pending_escalation = None

            if resp.approved:
                logger.info(f"Escalation {esc.id} APPROVED by {resp.responded_by}")
                self.state = ExecutorState.TRANSITING
            else:
                logger.info(f"Escalation {esc.id} DENIED by {resp.responded_by}")
                self.current_task.state = TaskState.CANCELLED
                self.current_task = None
                self.state = ExecutorState.IDLE
            return

        # Check for timeout
        if elapsed >= esc.timeout_seconds:
            logger.warning(
                f"Escalation {esc.id} TIMEOUT after {elapsed:.0f}s — "
                f"action: {esc.timeout_action}"
            )
            self._pending_escalation = None

            if esc.timeout_action == "proceed":
                self.state = ExecutorState.TRANSITING
            else:
                self.current_task.state = TaskState.CANCELLED
                self.current_task = None
                self.state = ExecutorState.IDLE
            return

        await asyncio.sleep(1.0)

    async def _handle_transiting(self) -> None:
        if not self.current_task:
            self.state = ExecutorState.IDLE
            return

        self.node.update_status(DroneStatus.TRANSITING)

        target = self.current_task.target_position
        target.altitude = self.config.transit_altitude

        logger.info(f"Transiting to task at ({target.latitude:.6f}, {target.longitude:.6f})")

        await self._navigate_to(target, self.config.transit_speed_mps)

        working_pos = GeoPosition(
            latitude=target.latitude,
            longitude=target.longitude,
            altitude=self.config.working_altitude,
        )
        await self._descend_to(working_pos)

        self.state = ExecutorState.EXECUTING

    async def _handle_executing(self) -> None:
        if not self.current_task:
            self.state = ExecutorState.IDLE
            return

        self.node.update_status(DroneStatus.EXECUTING)

        task = self.current_task
        task_type_str = task.task_type.value
        logger.info(f"Executing task {task.id}: {task_type_str}")

        exec_params = self.profile.task_execution.get(task_type_str)

        if exec_params:
            await asyncio.sleep(exec_params.duration)

            if exec_params.consumes_payload:
                cost = (exec_params.duration / 60) * self.config.payload_rate * exec_params.payload_fraction
                self.payload_level = max(0, self.payload_level - cost)
                self._sync_payload_aliases()
                log_msg = exec_params.log_template.format(
                    cost=cost,
                    unit=self.config.payload_unit,
                    count=int(exec_params.duration),
                    remaining=self.payload_level,
                )
            else:
                log_msg = exec_params.log_template.format(
                    count=int(exec_params.duration),
                    unit=self.config.payload_unit,
                    remaining=self.payload_level,
                )

            logger.info(f"{log_msg} | payload: {self.payload_level:.1f}{self.config.payload_unit}")
        else:
            await asyncio.sleep(5.0)
            logger.info(f"Generic execution complete for {task_type_str}")

        logger.info(f"Task {task.id} completed")

        task.state = TaskState.COMPLETED
        self.current_task = None

        refill_threshold = self.config.payload_capacity * 0.20
        if self.payload_level < refill_threshold:
            logger.info(f"Payload low ({self.payload_level:.1f}{self.config.payload_unit}), returning home")
            self.state = ExecutorState.RETURNING
        else:
            self.state = ExecutorState.IDLE

    async def _handle_returning(self) -> None:
        self.node.update_status(DroneStatus.RETURNING)

        current = self.node.telemetry.position
        transit_pos = GeoPosition(
            latitude=current.latitude,
            longitude=current.longitude,
            altitude=self.config.transit_altitude,
        )
        await self._ascend_to(transit_pos)

        home_transit = GeoPosition(
            latitude=self.config.home_position.latitude,
            longitude=self.config.home_position.longitude,
            altitude=self.config.transit_altitude,
        )
        await self._navigate_to(home_transit, self.config.transit_speed_mps)

        await self._descend_to(self.config.home_position)

        logger.info(f"At home, refilling payload to {self.config.payload_capacity}{self.config.payload_unit}")
        await asyncio.sleep(5.0)
        self.payload_level = self.config.payload_capacity
        self._sync_payload_aliases()

        self.state = ExecutorState.IDLE

    async def _navigate_to(self, target: GeoPosition, speed: float) -> None:
        current = self.node.telemetry.position
        distance = current.distance_to(target)
        travel_time = distance / speed

        steps = max(1, int(travel_time * 2))
        for i in range(steps):
            if not self._running or self._recalled:
                break

            t = (i + 1) / steps
            new_pos = GeoPosition(
                latitude=current.latitude + t * (target.latitude - current.latitude),
                longitude=current.longitude + t * (target.longitude - current.longitude),
                altitude=target.altitude,
            )
            self.node.update_position(new_pos)
            await asyncio.sleep(0.5)

    async def _ascend_to(self, target: GeoPosition) -> None:
        current = self.node.telemetry.position
        alt_diff = abs(target.altitude - current.altitude)
        steps = max(1, int(alt_diff / 2))

        for i in range(steps):
            if not self._running:
                break

            t = (i + 1) / steps
            new_alt = current.altitude + t * (target.altitude - current.altitude)
            new_pos = GeoPosition(
                latitude=current.latitude,
                longitude=current.longitude,
                altitude=new_alt,
            )
            self.node.update_position(new_pos)
            await asyncio.sleep(0.3)

    async def _descend_to(self, target: GeoPosition) -> None:
        await self._ascend_to(target)

    async def _handle_recalled(self) -> None:
        """Hold position and wait for REDEPLOY. Drone is safe-stopped."""
        self.node.update_status(DroneStatus.RETURNING)
        await asyncio.sleep(1.0)

    def assign_task(self, task: Task, detection: Optional[object] = None) -> None:
        if not self.is_available:
            logger.warning(f"Cannot assign task, executor not available")
            return

        # Check capability match
        if task.task_type.value not in self.config.capabilities:
            logger.warning(
                f"Task {task.id} ({task.task_type.value}) not in capabilities: "
                f"{self.config.capabilities}"
            )
            return

        self.current_task = task
        task.assigned_to = self.config.drone_id
        task.state = TaskState.ASSIGNED

        # Evaluate escalation
        swarm_ctx = SwarmContext(
            total_drones=max(1, len(self.node.swarm_state.members) + 1),
            committed_drones=sum(
                1 for m in self.node.swarm_state.members.values()
                if m.current_task_id is not None
            ),
        )
        level = self._escalation_engine.score(task, detection, swarm_ctx)
        task.escalation_level = level

        if level in (EscalationLevel.APPROVE_REQUIRED, EscalationLevel.EMERGENCY):
            task.state = TaskState.AWAITING_APPROVAL
            self.state = ExecutorState.AWAITING_APPROVAL

            esc_req = self._escalation_engine.create_request(
                task=task,
                drone_id=self.config.drone_id,
                level=level,
                reason=f"Task {task.task_type.value} scored {level.value}",
            )
            self._pending_escalation = esc_req

            # Publish escalation request to Zenoh
            key = ZenohKeys.escalation(self.config.drone_id)
            payload = esc_req.model_dump_json().encode()
            self.node._publishers.get("command", self.node._publishers.get("telemetry")).put(payload)

            logger.info(
                f"Task {task.id} requires {level.value} — "
                f"escalation {esc_req.id} published, waiting {esc_req.timeout_seconds}s"
            )
        else:
            self.state = ExecutorState.TRANSITING
            if level == EscalationLevel.NOTIFY:
                logger.info(f"Task {task.id} auto-proceeding with notification")

        logger.info(f"Task {task.id} assigned to {self.config.drone_id} (escalation={level.value})")


async def main():
    import os

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    home = (45.5225, -122.6765)

    config = ExecutorConfig(
        drone_id=os.environ.get("OLYMPUS_DRONE_ID", "executor_01"),
        home_position=home,
    )

    executor = ExecutorDrone(config)

    try:
        await executor.start()

        while True:
            await asyncio.sleep(1.0)

    except KeyboardInterrupt:
        pass
    finally:
        await executor.stop()


if __name__ == "__main__":
    asyncio.run(main())
