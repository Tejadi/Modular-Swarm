"""ATHENA — Defense vertical: threat classification, ROE enforcement, tactical symbols.

Provides:
- ThreatClassifier: maps detections → tactical threat levels (GREEN/AMBER/RED)
- ROEEnforcer: validates executor actions against Rules of Engagement
- TacticalSymbolGenerator: NATO APP-6(D) compatible symbol IDs for CesiumJS overlay
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from olympus_brain.protocol import Detection, Task, TaskType, GeoPosition

logger = logging.getLogger(__name__)


class ThreatLevel(str, Enum):
    GREEN = "green"    # Informational — no immediate threat
    AMBER = "amber"    # Potential threat — requires monitoring
    RED = "red"        # Confirmed/critical — immediate action required


@dataclass
class ThreatAssessment:
    level: ThreatLevel
    detection_type: str
    confidence: float
    description: str
    recommended_action: str
    min_approach_distance_m: float = 0.0
    requires_buddy: bool = False


# ---- Threat Classification ------------------------------------------------

THREAT_CLASSIFICATION: dict[str, ThreatLevel] = {
    # GREEN — informational
    "structural_change": ThreatLevel.GREEN,
    "thermal_anomaly": ThreatLevel.GREEN,
    # AMBER — potential threat, needs monitoring
    "vehicle_detected": ThreatLevel.AMBER,
    "person_detected": ThreatLevel.AMBER,
    # RED — confirmed/critical, immediate response
    "hostile_activity": ThreatLevel.RED,
    "ied_suspected": ThreatLevel.RED,
}

THREAT_RECOMMENDATIONS: dict[str, str] = {
    "structural_change": "Log and photograph. Schedule follow-up if in sensitive area.",
    "thermal_anomaly": "Investigate with IR sensor. May indicate concealed personnel or equipment.",
    "vehicle_detected": "Photograph and classify. Track movement vector if mobile.",
    "person_detected": "Photograph and track. Determine intent from behavior patterns.",
    "hostile_activity": "Mark position. Do NOT approach. Alert command immediately.",
    "ied_suspected": "Mark position. Maintain 50m standoff. Alert EOD team.",
}


class ThreatClassifier:
    """Maps raw detections to tactical threat levels with recommended actions."""

    def classify(self, detection: Detection) -> ThreatAssessment:
        det_type = detection.detection_type.value
        level = THREAT_CLASSIFICATION.get(det_type, ThreatLevel.AMBER)
        recommendation = THREAT_RECOMMENDATIONS.get(det_type, "Investigate and report.")

        min_distance = 0.0
        requires_buddy = False

        if det_type == "ied_suspected":
            min_distance = 50.0
            requires_buddy = False  # Stay away, don't send anyone close
        elif level == ThreatLevel.RED:
            min_distance = 30.0
        elif level == ThreatLevel.AMBER:
            requires_buddy = True  # Buddy system for AMBER contacts

        description = f"{level.value.upper()} contact: {det_type} (conf={detection.confidence:.2f})"

        return ThreatAssessment(
            level=level,
            detection_type=det_type,
            confidence=detection.confidence,
            description=description,
            recommended_action=recommendation,
            min_approach_distance_m=min_distance,
            requires_buddy=requires_buddy,
        )


# ---- Rules of Engagement --------------------------------------------------

@dataclass
class ROEConfig:
    """Rules of Engagement configuration loaded from mission profile."""
    # IED: mark only, never approach
    ied_standoff_m: float = 50.0
    # Minimum drones for INVESTIGATE tasks
    investigate_min_drones: int = 2
    # Geographic exclusion zones: [(center_lat, center_lon, radius_m)]
    exclusion_zones: list[tuple[float, float, float]] = field(default_factory=list)
    # Maximum threat level an executor can autonomously respond to
    max_autonomous_level: ThreatLevel = ThreatLevel.AMBER
    # RED-level detections require human approval before executor action
    red_requires_approval: bool = True


class ROEEnforcer:
    """Validates executor actions against Rules of Engagement before task assignment."""

    def __init__(self, config: Optional[ROEConfig] = None):
        self.config = config or ROEConfig()

    def validate_task(
        self,
        task: Task,
        threat: ThreatAssessment,
        available_drones: int = 1,
        executor_position: Optional[GeoPosition] = None,
    ) -> tuple[bool, str]:
        """Check if a task assignment is ROE-compliant.

        Returns (allowed: bool, reason: str).
        """
        # Rule 1: IED — mark only, no approach
        if threat.detection_type == "ied_suspected":
            if task.task_type not in (TaskType.MARK, TaskType.PHOTOGRAPH):
                return False, (
                    f"ROE VIOLATION: IED suspected — only MARK/PHOTOGRAPH allowed, "
                    f"got {task.task_type.value}. Maintain {self.config.ied_standoff_m}m standoff."
                )

        # Rule 2: INVESTIGATE requires buddy system (min 2 drones)
        if task.task_type == TaskType.INVESTIGATE:
            if available_drones < self.config.investigate_min_drones:
                return False, (
                    f"ROE VIOLATION: INVESTIGATE requires min {self.config.investigate_min_drones} "
                    f"drones (buddy system), only {available_drones} available."
                )

        # Rule 3: RED-level threats require human approval
        if threat.level == ThreatLevel.RED and self.config.red_requires_approval:
            return False, (
                f"ROE HOLD: RED-level threat ({threat.detection_type}) requires human approval. "
                f"Task {task.id} queued for authorization."
            )

        # Rule 4: Geographic exclusion zones
        if executor_position and self._in_exclusion_zone(
            task.target_position.latitude,
            task.target_position.longitude,
        ):
            return False, (
                "ROE VIOLATION: Target position is within a geographic exclusion zone."
            )

        return True, "ROE check passed"

    def _in_exclusion_zone(self, lat: float, lon: float) -> bool:
        """Check if a position falls within any exclusion zone."""
        import math
        for zone_lat, zone_lon, radius_m in self.config.exclusion_zones:
            # Haversine approximation (good enough for <10km)
            dlat = math.radians(lat - zone_lat)
            dlon = math.radians(lon - zone_lon)
            a = (
                math.sin(dlat / 2) ** 2
                + math.cos(math.radians(zone_lat))
                * math.cos(math.radians(lat))
                * math.sin(dlon / 2) ** 2
            )
            dist_m = 6371000.0 * 2 * math.asin(math.sqrt(a))
            if dist_m <= radius_m:
                return True
        return False


# ---- NATO APP-6(D) Tactical Symbols --------------------------------------

# Simplified SIDC (Symbol Identification Code) generation for CesiumJS
# Full APP-6D is 20-char, we produce a subset for display purposes.

SYMBOL_MAP: dict[str, dict] = {
    "hostile_activity": {
        "sidc": "SHG-UCI----",  # Hostile, Ground, Unit, Combat, Infantry
        "label": "HOSTILE",
        "color": "#FF0000",
    },
    "vehicle_detected": {
        "sidc": "SHG-EVM----",  # Hostile, Ground, Equipment, Vehicle, Wheeled
        "label": "VEH",
        "color": "#FF8800",
    },
    "person_detected": {
        "sidc": "SHG-UCI----",  # Suspect ground unit
        "label": "PERSON",
        "color": "#FF8800",
    },
    "ied_suspected": {
        "sidc": "SHG-EXI----",  # Hostile, Ground, Explosive/Ordnance
        "label": "IED",
        "color": "#FF0000",
    },
    "structural_change": {
        "sidc": "SNG-IB-----",  # Neutral, Ground, Installation, Building
        "label": "STRUCT",
        "color": "#00CC00",
    },
    "thermal_anomaly": {
        "sidc": "SNG-ES-----",  # Neutral, Ground, Event, Sensor
        "label": "THERMAL",
        "color": "#00CC00",
    },
}


class TacticalSymbolGenerator:
    """Generates NATO APP-6(D) compatible symbol data for CesiumJS overlay."""

    def get_symbol(self, detection: Detection) -> dict:
        """Return symbol info dict for rendering on the tactical map."""
        det_type = detection.detection_type.value
        sym = SYMBOL_MAP.get(det_type, {
            "sidc": "SUG-------",
            "label": det_type.upper()[:6],
            "color": "#AAAAAA",
        })

        return {
            "id": detection.id,
            "sidc": sym["sidc"],
            "label": sym["label"],
            "color": sym["color"],
            "position": {
                "latitude": detection.position.latitude,
                "longitude": detection.position.longitude,
                "altitude": detection.position.altitude,
            },
            "confidence": detection.confidence,
            "severity": detection.severity,
            "timestamp": detection.timestamp.isoformat(),
        }

    def get_threat_color(self, level: ThreatLevel) -> str:
        """Return hex color for a threat level (for map overlays)."""
        return {
            ThreatLevel.GREEN: "#00CC00",
            ThreatLevel.AMBER: "#FF8800",
            ThreatLevel.RED: "#FF0000",
        }.get(level, "#AAAAAA")
