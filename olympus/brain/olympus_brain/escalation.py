"""Escalation scoring engine for bilevel autonomy.

Determines whether a task should be executed autonomously (AUTO),
executed with notification (NOTIFY), require human approval (APPROVE_REQUIRED),
or trigger an emergency alert (EMERGENCY).

The swarm operates autonomously at the tactical layer. Escalation occurs when
tasks exceed configurable risk thresholds — high cost, novel detections,
low confidence, geo-fence proximity, or high resource commitment.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from olympus_brain.protocol import (
    EscalationLevel,
    EscalationRequest,
    Task,
    TaskType,
    Detection,
    DetectionType,
    GeoPosition,
)

logger = logging.getLogger(__name__)


@dataclass
class EscalationThresholds:
    """Per-vertical configurable thresholds."""
    # Confidence below this → APPROVE_REQUIRED
    min_confidence_auto: float = 0.7
    min_confidence_notify: float = 0.5

    # Priority above this → APPROVE_REQUIRED
    high_priority_threshold: int = 8

    # Max swarm fraction committed before escalation
    max_swarm_commitment: float = 0.5

    # Geo-fence proximity (meters) — escalate if task is within this distance
    geofence_proximity_m: float = 100.0

    # Task types that always require approval
    always_approve: list[str] = field(default_factory=lambda: [
        "drop_supplies",
    ])

    # Task types that never need approval (fully autonomous)
    always_auto: list[str] = field(default_factory=lambda: [
        "photograph",
        "inspect",
        "relay",
    ])

    # Timeout for human response (seconds)
    approval_timeout_s: int = 30

    # What to do on timeout: "proceed" or "abort"
    timeout_action: str = "proceed"


# Preset thresholds per vertical
VERTICAL_THRESHOLDS = {
    "CERES": EscalationThresholds(
        min_confidence_auto=0.6,
        max_swarm_commitment=0.7,
        timeout_action="proceed",
    ),
    "ATHENA": EscalationThresholds(
        min_confidence_auto=0.85,
        high_priority_threshold=6,
        max_swarm_commitment=0.3,
        always_approve=["drop_supplies", "mark_location", "investigate"],
        timeout_action="abort",
        approval_timeout_s=60,
    ),
    "VULCAN": EscalationThresholds(
        min_confidence_auto=0.7,
        timeout_action="proceed",
    ),
    "HERMES": EscalationThresholds(
        min_confidence_auto=0.5,
        max_swarm_commitment=0.8,
        timeout_action="proceed",
        approval_timeout_s=15,
    ),
}


@dataclass
class SwarmContext:
    """Current swarm state for escalation decisions."""
    total_drones: int = 1
    committed_drones: int = 0
    geofence_boundaries: list[GeoPosition] = field(default_factory=list)
    recent_detection_types: list[str] = field(default_factory=list)


class EscalationEngine:
    """Scores tasks and determines escalation level."""

    def __init__(self, vertical: str = "CERES", thresholds: Optional[EscalationThresholds] = None):
        self.vertical = vertical
        self.thresholds = thresholds or VERTICAL_THRESHOLDS.get(vertical, EscalationThresholds())
        self._seen_detection_types: set[str] = set()

    def score(
        self,
        task: Task,
        detection: Optional[Detection] = None,
        context: Optional[SwarmContext] = None,
    ) -> EscalationLevel:
        """Compute escalation level for a task based on risk factors."""
        ctx = context or SwarmContext()
        th = self.thresholds

        # Always-approve task types
        if task.task_type.value in th.always_approve:
            return EscalationLevel.APPROVE_REQUIRED

        # Always-auto task types
        if task.task_type.value in th.always_auto:
            return EscalationLevel.AUTO

        factors: list[tuple[str, EscalationLevel]] = []

        # Factor 1: Detection confidence
        if detection:
            if detection.confidence < th.min_confidence_notify:
                factors.append(("low_confidence", EscalationLevel.APPROVE_REQUIRED))
            elif detection.confidence < th.min_confidence_auto:
                factors.append(("moderate_confidence", EscalationLevel.NOTIFY))

            # Factor 2: Novel detection type (never seen before)
            det_type_str = detection.detection_type.value
            if det_type_str not in self._seen_detection_types:
                factors.append(("novel_detection", EscalationLevel.NOTIFY))
            self._seen_detection_types.add(det_type_str)

        # Factor 3: High priority / severity
        if task.priority >= th.high_priority_threshold:
            factors.append(("high_priority", EscalationLevel.NOTIFY))
        if task.priority >= 10:
            factors.append(("critical_priority", EscalationLevel.EMERGENCY))

        # Factor 4: Swarm resource commitment
        if ctx.total_drones > 0:
            commitment = ctx.committed_drones / ctx.total_drones
            if commitment >= th.max_swarm_commitment:
                factors.append(("high_commitment", EscalationLevel.APPROVE_REQUIRED))

        # Factor 5: Geo-fence proximity
        if ctx.geofence_boundaries:
            min_dist = min(
                task.target_position.distance_to(boundary)
                for boundary in ctx.geofence_boundaries
            )
            if min_dist < th.geofence_proximity_m:
                factors.append(("near_geofence", EscalationLevel.APPROVE_REQUIRED))

        if not factors:
            return EscalationLevel.AUTO

        # Take the highest escalation level from all factors
        level_order = {
            EscalationLevel.AUTO: 0,
            EscalationLevel.NOTIFY: 1,
            EscalationLevel.APPROVE_REQUIRED: 2,
            EscalationLevel.EMERGENCY: 3,
        }
        highest = max(factors, key=lambda f: level_order[f[1]])
        level = highest[1]

        reasons = [f[0] for f in factors if f[1] == level]
        logger.info(
            f"Escalation for task {task.id}: {level.value} "
            f"(reasons: {', '.join(reasons)})"
        )

        return level

    def create_request(
        self,
        task: Task,
        drone_id: str,
        level: EscalationLevel,
        reason: str,
    ) -> EscalationRequest:
        """Create an escalation request for human review."""
        return EscalationRequest(
            task_id=task.id,
            drone_id=drone_id,
            escalation_level=level,
            reason=reason,
            recommended_action="proceed",
            timeout_action=self.thresholds.timeout_action,
            timeout_seconds=self.thresholds.approval_timeout_s,
        )
