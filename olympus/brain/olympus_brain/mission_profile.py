from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class PayloadConfig:
    label: str
    unit: str
    capacity: float
    rate: float


@dataclass
class TaskExecutionParams:
    duration: float
    consumes_payload: bool
    payload_fraction: float = 1.0
    log_template: str = "Task completed"
    requires_payload: bool = False


@dataclass
class AdvisorConfig:
    persona: str
    greeting: str
    system_prompt: str
    domain_context: str


@dataclass
class MissionProfileConfig:
    id: str
    name: str
    domain: str
    detection_types: List[str]
    task_types: List[str]
    detection_to_task: Dict[str, str]
    payload: PayloadConfig
    advisor: AdvisorConfig
    operating_center: Tuple[float, float]
    operating_size_meters: int
    task_execution: Dict[str, TaskExecutionParams] = field(default_factory=dict)

    @property
    def task_to_detection(self) -> Dict[str, str]:
        """Reverse mapping: task_type → detection_type. Used by CBBA ROE checks."""
        return {v: k for k, v in self.detection_to_task.items()}


CERES = MissionProfileConfig(
    id="ceres",
    name="Precision Agriculture",
    domain="agriculture",
    detection_types=["weed", "pest", "disease", "nutrient_deficiency", "irrigation_leak", "crop_stress"],
    task_types=["spray", "fertilize", "inspect", "seed", "sample"],
    detection_to_task={
        "weed": "spray",
        "pest": "spray",
        "disease": "spray",
        "nutrient_deficiency": "fertilize",
        "irrigation_leak": "inspect",
        "crop_stress": "inspect",
    },
    payload=PayloadConfig(label="Tank Level", unit="L", capacity=10.0, rate=2.0),
    task_execution={
        "spray": TaskExecutionParams(5.0, True, 1.0, "Sprayed {cost:.2f}{unit} of herbicide", True),
        "fertilize": TaskExecutionParams(5.0, True, 1.0, "Applied {cost:.2f}{unit} of fertilizer", True),
        "seed": TaskExecutionParams(8.0, True, 0.5, "Seeded {cost:.2f}{unit} in target area", True),
        "inspect": TaskExecutionParams(10.0, False, 0.0, "Hover inspection complete - imagery captured"),
        "sample": TaskExecutionParams(12.0, True, 0.1, "Soil sample collected ({cost:.2f}{unit})", True),
    },
    advisor=AdvisorConfig(
        persona="Farm Advisor",
        greeting="OLYMPUS Farm Advisor online. Ask me about fleet status, weather, detections, or anything about your operation.",
        system_prompt="""You are the OLYMPUS Farm Advisor AI, an expert agricultural consultant integrated with an autonomous drone fleet system.

Your role is to help farmers:
1. Monitor their drone fleet status and mission progress
2. Understand detections (weeds, pests, diseases) found by scout drones
3. Make informed decisions about crop treatment
4. Get weather and market information relevant to farming operations
5. Control the drone mission when needed

When answering:
- Be concise and actionable
- Use farming terminology the user understands
- Always consider weather conditions for spray recommendations
- Prioritize safety - recommend abort only in true emergencies
- When uncertain, ask clarifying questions

Current context: {context}""",
        domain_context="Precision agriculture - crop monitoring and treatment",
    ),
    operating_center=(36.6777, -121.6555),
    operating_size_meters=500,
)

ATHENA = MissionProfileConfig(
    id="athena",
    name="Tactical Reconnaissance",
    domain="defense",
    detection_types=["hostile_activity", "vehicle_detected", "person_detected", "ied_suspected", "structural_change", "thermal_anomaly"],
    task_types=["investigate", "mark", "photograph", "relay"],
    detection_to_task={
        "hostile_activity": "investigate",
        "vehicle_detected": "photograph",
        "person_detected": "photograph",
        "ied_suspected": "mark",
        "structural_change": "photograph",
        "thermal_anomaly": "investigate",
    },
    payload=PayloadConfig(label="Payload Bay", unit="%", capacity=100.0, rate=5.0),
    task_execution={
        "investigate": TaskExecutionParams(15.0, False, 0.0, "Close flyby complete - area under observation"),
        "mark": TaskExecutionParams(5.0, True, 0.2, "IR marker deployed ({cost:.1f}{unit} payload used)", True),
        "photograph": TaskExecutionParams(5.0, False, 0.0, "High-res imagery captured - {count} frames"),
        "relay": TaskExecutionParams(30.0, False, 0.0, "Comms relay orbit complete - link maintained"),
    },
    advisor=AdvisorConfig(
        persona="Tactical Advisor",
        greeting="OLYMPUS Tactical Advisor online. Report sector status, request ISR, or query threat assessment.",
        system_prompt="""You are the OLYMPUS Tactical Advisor AI, providing ISR (Intelligence, Surveillance, Reconnaissance) analysis and fleet coordination.

Your role:
1. Provide situation reports (SITREP) on drone fleet and sector status
2. Analyze contacts and threat detections
3. Recommend ISR tasking and coverage priorities
4. Coordinate executor drone responses
5. Manage mission flow and emergency procedures

Use concise military-style communication. Prioritize safety and rules of engagement.

Current context: {context}""",
        domain_context="Tactical reconnaissance - ISR and threat detection",
    ),
    operating_center=(34.0522, -118.2437),
    operating_size_meters=1000,
)

VULCAN = MissionProfileConfig(
    id="vulcan",
    name="Industrial Inspection",
    domain="industrial",
    detection_types=["structural_crack", "corrosion", "thermal_anomaly", "leak_detected", "vegetation_encroachment", "surface_deformation"],
    task_types=["photograph", "thermal_scan", "measure", "sample"],
    detection_to_task={
        "structural_crack": "photograph",
        "corrosion": "photograph",
        "thermal_anomaly": "thermal_scan",
        "leak_detected": "sample",
        "vegetation_encroachment": "photograph",
        "surface_deformation": "measure",
    },
    payload=PayloadConfig(label="Sensor Bay", unit="%", capacity=100.0, rate=1.0),
    task_execution={
        "photograph": TaskExecutionParams(10.0, False, 0.0, "Close-range inspection photos captured"),
        "thermal_scan": TaskExecutionParams(12.0, True, 0.5, "FLIR thermal sweep complete ({cost:.1f}{unit} sensor capacity)"),
        "measure": TaskExecutionParams(8.0, False, 0.0, "LiDAR/photogrammetry measurement recorded"),
        "sample": TaskExecutionParams(15.0, True, 1.0, "Environmental sample collected ({cost:.2f}{unit})", True),
    },
    advisor=AdvisorConfig(
        persona="Inspection Advisor",
        greeting="OLYMPUS Inspection Advisor online. Query asset condition, schedule inspections, or review findings.",
        system_prompt="""You are the OLYMPUS Inspection Advisor AI, specializing in industrial asset integrity and infrastructure inspection.

Your role:
1. Report on inspection fleet status and coverage
2. Analyze defect detections (cracks, corrosion, leaks, thermal anomalies)
3. Prioritize findings by severity and risk
4. Recommend follow-up inspections and maintenance actions
5. Track inspection coverage across facility assets

Use technical inspection terminology. Reference industry standards when applicable.

Current context: {context}""",
        domain_context="Industrial inspection - structural integrity and asset monitoring",
    ),
    operating_center=(29.7604, -95.3698),
    operating_size_meters=300,
)

HERMES = MissionProfileConfig(
    id="hermes",
    name="Search and Rescue",
    domain="sar",
    detection_types=["person_detected", "thermal_signature", "debris_field", "vehicle_wreckage", "signal_detected"],
    task_types=["investigate", "drop_supplies", "mark_location", "relay_comms"],
    detection_to_task={
        "person_detected": "drop_supplies",
        "thermal_signature": "investigate",
        "debris_field": "mark_location",
        "vehicle_wreckage": "investigate",
        "signal_detected": "investigate",
    },
    payload=PayloadConfig(label="Supply Payload", unit="kg", capacity=5.0, rate=5.0),
    task_execution={
        "investigate": TaskExecutionParams(15.0, False, 0.0, "Close approach scan complete - thermal/visual sweep done"),
        "drop_supplies": TaskExecutionParams(8.0, True, 1.0, "Supply drop deployed ({cost:.2f}{unit})", True),
        "mark_location": TaskExecutionParams(5.0, True, 0.1, "Location marker deployed ({cost:.2f}{unit})", True),
        "relay_comms": TaskExecutionParams(30.0, False, 0.0, "Comms relay orbit complete - signal relay maintained"),
    },
    advisor=AdvisorConfig(
        persona="SAR Coordinator",
        greeting="OLYMPUS SAR Coordinator online. Report search status, mark findings, or request resource deployment.",
        system_prompt="""You are the OLYMPUS SAR Coordinator AI, managing search and rescue drone operations.

Your role:
1. Track search coverage and grid status
2. Analyze findings (survivors, thermal signatures, debris, signals)
3. Coordinate supply drops and rescue resource deployment
4. Manage search priorities based on probability of detection
5. Monitor weather conditions affecting search operations

Prioritize life safety above all else. Use clear, urgent communication for confirmed findings.

Current context: {context}""",
        domain_context="Search and rescue - survivor detection and resource deployment",
    ),
    operating_center=(37.7749, -122.4194),
    operating_size_meters=2000,
)

PROFILES = {
    "ceres": CERES,
    "athena": ATHENA,
    "vulcan": VULCAN,
    "hermes": HERMES,
}


def load_profile(profile_id: Optional[str] = None) -> MissionProfileConfig:
    if not profile_id:
        profile_id = os.environ.get("OLYMPUS_INSTANCE", "athena")

    profile = PROFILES.get(profile_id)
    if not profile:
        print(f"Warning: Unknown profile '{profile_id}', defaulting to athena")
        profile = ATHENA

    return profile
