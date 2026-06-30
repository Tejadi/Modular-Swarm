__version__ = "0.1.0"
__author__ = "OLYMPUS OS Team"

from olympus_brain.protocol import (
    DroneRole,
    DroneStatus,
    GeoPosition,
    DroneTelemetry,
    Detection,
    DetectionType,
    Task,
    TaskType,
    TaskState,
)

try:
    from olympus_brain.node import OlympusNode
except ImportError:
    OlympusNode = None  # zenoh not installed

__all__ = [
    "OlympusNode",
    "DroneRole",
    "DroneStatus",
    "GeoPosition",
    "DroneTelemetry",
    "Detection",
    "DetectionType",
    "Task",
    "TaskType",
    "TaskState",
]
