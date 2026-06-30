from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

from .protocol import (
    GeoPosition,
    Task,
    DroneTelemetry,
)
from .mission_profile import load_profile

# Lazy import to avoid circular dependency — only used at runtime in ATHENA vertical
_roe_enforcer = None
_threat_classifier = None


def _get_athena_validators():
    """Lazy-load ATHENA ROE/threat validators (only needed for defense vertical)."""
    global _roe_enforcer, _threat_classifier
    if _roe_enforcer is None:
        try:
            from .athena import ROEEnforcer, ThreatClassifier
            _roe_enforcer = ROEEnforcer()
            _threat_classifier = ThreatClassifier()
        except ImportError:
            pass
    return _roe_enforcer, _threat_classifier


@dataclass
class ExecutorSnapshot:
    drone_id: str
    position: GeoPosition
    battery_percent: int
    payload_level: float
    payload_capacity: float
    capable_task_types: list[str]
    timestamp: float = field(default_factory=time.time)

    @classmethod
    def from_telemetry(
        cls,
        telemetry: DroneTelemetry,
        payload_level: float,
        payload_capacity: float,
        capable_task_types: list[str],
    ) -> ExecutorSnapshot:
        return cls(
            drone_id=telemetry.drone_id,
            position=telemetry.position,
            battery_percent=telemetry.battery.percentage,
            payload_level=payload_level,
            payload_capacity=payload_capacity,
            capable_task_types=capable_task_types,
        )


class CommsAwareness:
    """Communication state for CA-CBBA bandwidth-aware scoring."""

    def __init__(self, mode: str = "full_comms", channel_load: float = 0.0):
        self.mode = mode           # "full_comms", "degraded", "minimal", "denied"
        self.channel_load = channel_load  # 0.0 to 1.0 — current channel utilization

    @property
    def bandwidth_factor(self) -> float:
        """Penalty factor for tasks requiring high-bandwidth coordination."""
        return {
            "full_comms": 1.0,
            "degraded": 0.7,
            "minimal": 0.3,
            "denied": 0.0,
        }.get(self.mode, 1.0)


class CBBAAllocator:

    WEIGHT_DISTANCE = 0.35
    WEIGHT_BATTERY = 0.15
    WEIGHT_PAYLOAD = 0.15
    WEIGHT_URGENCY = 0.20
    WEIGHT_CAPABILITY = 0.15

    DIMINISHING_FACTOR = 0.7
    MAX_DISTANCE_M = 5000.0
    CONVERGENCE_ROUNDS = 2
    MAX_ROUNDS = 50

    # Tasks requiring high-bandwidth coordination
    HIGH_BANDWIDTH_TASKS = {"spray", "seed", "fertilize", "drop_supplies"}

    def __init__(self, drone_id: str, max_bundle_size: int = 3):
        self._drone_id = drone_id
        self._max_bundle_size = max_bundle_size
        self._profile = load_profile()
        self._comms = CommsAwareness()
        self._roe_enabled = self._profile.id == "athena"
        # Cache available drone count for ROE buddy-system check
        self._available_drone_count: int = 1

    def set_comms_state(self, mode: str, channel_load: float = 0.0) -> None:
        """Update communication state for bandwidth-aware scoring."""
        self._comms = CommsAwareness(mode=mode, channel_load=channel_load)

    def set_available_drone_count(self, count: int) -> None:
        """Update available executor count for ROE buddy-system validation."""
        self._available_drone_count = max(1, count)

    async def run_allocation(
        self,
        available_tasks: list[Task],
        executor_states: dict[str, ExecutorSnapshot],
        my_state: ExecutorSnapshot,
    ) -> list[Task]:
        if not available_tasks or self._drone_id not in executor_states:
            return []

        task_map = {t.id: t for t in available_tasks}
        all_task_ids = list(task_map.keys())
        drone_ids = sorted(executor_states.keys())

        bundles: dict[str, list[str]] = {d: [] for d in drone_ids}
        winning_bids: dict[str, float] = {tid: -1.0 for tid in all_task_ids}
        winning_agents: dict[str, Optional[str]] = {tid: None for tid in all_task_ids}
        bid_timestamps: dict[str, float] = {tid: 0.0 for tid in all_task_ids}

        stable_rounds = 0
        for _ in range(self.MAX_ROUNDS):
            prev_bundle = list(bundles[self._drone_id])

            self._bundle_phase(
                bundles[self._drone_id],
                all_task_ids,
                task_map,
                my_state,
                winning_bids,
                winning_agents,
                bid_timestamps,
            )

            changed = self._consensus_phase(
                drone_ids,
                bundles,
                winning_bids,
                winning_agents,
                bid_timestamps,
                executor_states,
                task_map,
            )

            if not changed and bundles[self._drone_id] == prev_bundle:
                stable_rounds += 1
            else:
                stable_rounds = 0

            if stable_rounds >= self.CONVERGENCE_ROUNDS:
                break

            await asyncio.sleep(0)

        return [task_map[tid] for tid in bundles[self._drone_id] if tid in task_map]

    def score_task(
        self,
        task: Task,
        executor_state: ExecutorSnapshot,
        bundle_position: int,
    ) -> float:
        task_type_str = task.task_type.value

        if task_type_str not in executor_state.capable_task_types:
            return 0.0

        # ROE enforcement: block bids that violate rules of engagement (ATHENA)
        if self._roe_enabled:
            roe, classifier = _get_athena_validators()
            if roe and classifier and hasattr(task, 'detection_id') and task.detection_id:
                # Build a lightweight threat assessment from the task's detection context
                from .protocol import Detection, DetectionType
                # Infer detection type from task type mapping
                det_type_str = self._profile.task_to_detection.get(task_type_str)
                if det_type_str:
                    try:
                        det = Detection.create(
                            detection_type=DetectionType(det_type_str),
                            position=task.target_position,
                            confidence=0.8,
                            detected_by="cbba",
                            severity=task.priority,
                        )
                        threat = classifier.classify(det)
                        allowed, reason = roe.validate_task(
                            task, threat,
                            available_drones=self._available_drone_count,
                            executor_position=executor_state.position,
                        )
                        if not allowed:
                            import logging as _log
                            _log.getLogger(__name__).warning(
                                f"CBBA ROE block: task {task.id} — {reason}"
                            )
                            return 0.0
                    except (ValueError, KeyError):
                        pass  # Unknown detection type, skip ROE check

        exec_params = self._profile.task_execution.get(task_type_str)
        payload_ratio = (
            executor_state.payload_level / executor_state.payload_capacity
            if executor_state.payload_capacity > 0
            else 1.0
        )
        if exec_params and exec_params.requires_payload and payload_ratio < 0.1:
            return 0.0

        distance = executor_state.position.distance_to(task.target_position)
        distance_score = max(0.0, 1.0 - distance / self.MAX_DISTANCE_M)

        battery_score = executor_state.battery_percent / 100.0

        urgency_score = task.priority / 10.0

        raw_score = (
            self.WEIGHT_DISTANCE * distance_score
            + self.WEIGHT_BATTERY * battery_score
            + self.WEIGHT_PAYLOAD * payload_ratio
            + self.WEIGHT_URGENCY * urgency_score
            + self.WEIGHT_CAPABILITY * 1.0
        )

        # CA-CBBA: penalize tasks requiring high-bandwidth coordination
        # when operating in degraded comms mode
        if task_type_str in self.HIGH_BANDWIDTH_TASKS:
            raw_score *= self._comms.bandwidth_factor

        # Agent censoring: when channel is congested (>70% load),
        # reduce score to limit message count during bidding
        if self._comms.channel_load > 0.7:
            congestion_penalty = 1.0 - (self._comms.channel_load - 0.7) / 0.3
            raw_score *= max(0.3, congestion_penalty)

        diminished = raw_score * (self.DIMINISHING_FACTOR ** bundle_position)

        if task.deadline:
            remaining = task.deadline.timestamp() - time.time()
            if remaining < 0:
                return 0.0
            if remaining < 120:
                diminished *= 1.5

        return round(diminished, 6)

    def _bundle_phase(
        self,
        my_bundle: list[str],
        all_task_ids: list[str],
        task_map: dict[str, Task],
        my_state: ExecutorSnapshot,
        winning_bids: dict[str, float],
        winning_agents: dict[str, Optional[str]],
        bid_timestamps: dict[str, float],
    ) -> None:
        while len(my_bundle) < self._max_bundle_size:
            best_task_id: Optional[str] = None
            best_score = -1.0
            position = len(my_bundle)

            for tid in all_task_ids:
                if tid in my_bundle:
                    continue

                score = self.score_task(task_map[tid], my_state, position)

                if score > winning_bids[tid] and score > best_score:
                    best_score = score
                    best_task_id = tid

            if best_task_id is None or best_score <= 0.0:
                break

            my_bundle.append(best_task_id)
            winning_bids[best_task_id] = best_score
            winning_agents[best_task_id] = self._drone_id
            bid_timestamps[best_task_id] = time.time()

    def _consensus_phase(
        self,
        drone_ids: list[str],
        bundles: dict[str, list[str]],
        winning_bids: dict[str, float],
        winning_agents: dict[str, Optional[str]],
        bid_timestamps: dict[str, float],
        executor_states: dict[str, ExecutorSnapshot],
        task_map: dict[str, Task],
    ) -> bool:
        changed = False

        for tid in list(winning_bids.keys()):
            claimants: list[tuple[str, float, float]] = []

            for did in drone_ids:
                if tid not in bundles[did]:
                    continue

                position = bundles[did].index(tid)
                score = self.score_task(task_map[tid], executor_states[did], position)
                ts = executor_states[did].timestamp
                claimants.append((did, score, ts))

            if len(claimants) <= 1:
                continue

            winner_id, winner_score, winner_ts = max(
                claimants,
                key=lambda c: (c[1], c[2]),
            )

            for did, score, ts in claimants:
                if did == winner_id:
                    continue

                if score < winner_score or (score == winner_score and ts < winner_ts):
                    bundles[did].remove(tid)
                    changed = True

            if winning_agents[tid] != winner_id:
                winning_agents[tid] = winner_id
                winning_bids[tid] = winner_score
                bid_timestamps[tid] = time.time()
                changed = True

        return changed
