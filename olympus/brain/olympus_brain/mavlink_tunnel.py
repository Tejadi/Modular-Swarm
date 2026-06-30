"""MAVLink TUNNEL message encoding/decoding for Olympus detections.

Encodes Detection objects into compact binary (≤128 bytes) for transmission
through MAVLink TUNNEL messages (msg_id=385, payload_type=0x4F) from Jetson
to Pixhawk. PX4 auto-routes TUNNEL between TELEM ports, so detections sent
on TELEM2 reach the ELRS radio on TELEM1 → base station.

Compact binary format (19 + id_len bytes):
    [0x4F]              magic / payload_type
    [det_type:u8]       DetectionType enum index
    [confidence:u8]     0-255 maps to 0.0-1.0
    [severity:u8]       1-10
    [lat:i32 LE]        latitude * 1e7
    [lon:i32 LE]        longitude * 1e7
    [alt:u16 LE]        altitude in meters (unsigned)
    [id_len:u8]         length of detected_by string
    [detected_by:bytes] variable length (typically 8-16 bytes)
    [timestamp:u32 LE]  unix epoch seconds
"""

from __future__ import annotations

import struct
import logging
from datetime import datetime, timezone
from typing import Optional

from olympus_brain.protocol import (
    Detection,
    DetectionType,
    GeoPosition,
)

logger = logging.getLogger(__name__)

TUNNEL_PAYLOAD_TYPE = 0x4F
TUNNEL_MAX_PAYLOAD = 128

# Stable enum index mapping — order must match Rust bridge decoder
_DETECTION_TYPE_INDEX: dict[DetectionType, int] = {
    DetectionType.WEED: 0,
    DetectionType.PEST: 1,
    DetectionType.DISEASE: 2,
    DetectionType.NUTRIENT_DEFICIENCY: 3,
    DetectionType.IRRIGATION_LEAK: 4,
    DetectionType.CROP_STRESS: 5,
    DetectionType.OBSTACLE: 6,
    DetectionType.HOSTILE_ACTIVITY: 7,
    DetectionType.VEHICLE_DETECTED: 8,
    DetectionType.PERSON_DETECTED: 9,
    DetectionType.IED_SUSPECTED: 10,
    DetectionType.STRUCTURAL_CHANGE: 11,
    DetectionType.STRUCTURAL_CRACK: 12,
    DetectionType.CORROSION: 13,
    DetectionType.THERMAL_ANOMALY: 14,
    DetectionType.LEAK_DETECTED: 15,
    DetectionType.VEGETATION_ENCROACHMENT: 16,
    DetectionType.SURFACE_DEFORMATION: 17,
    DetectionType.THERMAL_SIGNATURE: 18,
    DetectionType.DEBRIS_FIELD: 19,
    DetectionType.VEHICLE_WRECKAGE: 20,
    DetectionType.SIGNAL_DETECTED: 21,
}

_INDEX_TO_DETECTION_TYPE: dict[int, DetectionType] = {
    v: k for k, v in _DETECTION_TYPE_INDEX.items()
}


def encode_detection(detection: Detection) -> bytes:
    """Encode a Detection into compact binary for MAVLink TUNNEL payload.

    Returns bytes of length ≤ TUNNEL_MAX_PAYLOAD.
    """
    det_type_idx = _DETECTION_TYPE_INDEX.get(detection.detection_type, 0)
    confidence_u8 = min(255, max(0, int(detection.confidence * 255)))
    severity_u8 = min(10, max(1, detection.severity))
    lat_i32 = int(detection.position.latitude * 1e7)
    lon_i32 = int(detection.position.longitude * 1e7)
    alt_u16 = min(65535, max(0, int(detection.position.altitude)))

    detected_by = detection.detected_by.encode("utf-8")[:32]
    ts_u32 = int(detection.timestamp.replace(tzinfo=timezone.utc).timestamp()) & 0xFFFFFFFF

    buf = struct.pack(
        "<BBBBiiHB",
        TUNNEL_PAYLOAD_TYPE,
        det_type_idx,
        confidence_u8,
        severity_u8,
        lat_i32,
        lon_i32,
        alt_u16,
        len(detected_by),
    )
    buf += detected_by
    buf += struct.pack("<I", ts_u32)

    if len(buf) > TUNNEL_MAX_PAYLOAD:
        logger.warning(
            f"TUNNEL payload too large ({len(buf)}B), truncating detected_by"
        )
        max_id = TUNNEL_MAX_PAYLOAD - 19
        detected_by = detected_by[:max_id]
        buf = struct.pack(
            "<BBBBiiHB",
            TUNNEL_PAYLOAD_TYPE, det_type_idx, confidence_u8, severity_u8,
            lat_i32, lon_i32, alt_u16, len(detected_by),
        )
        buf += detected_by
        buf += struct.pack("<I", ts_u32)

    return buf


def decode_detection(data: bytes, fallback_detected_by: str = "unknown") -> Optional[Detection]:
    """Decode compact binary from MAVLink TUNNEL payload into a Detection.

    Returns None if data is malformed.
    """
    if len(data) < 19:
        logger.warning(f"TUNNEL payload too short: {len(data)} bytes")
        return None

    if data[0] != TUNNEL_PAYLOAD_TYPE:
        logger.debug(f"Not an Olympus TUNNEL payload (type=0x{data[0]:02X})")
        return None

    try:
        magic, det_type_idx, confidence_u8, severity_u8, lat_i32, lon_i32, alt_u16, id_len = (
            struct.unpack("<BBBBiiHB", data[:15])
        )

        pos = 15
        if pos + id_len + 4 > len(data):
            logger.warning("TUNNEL payload truncated")
            return None

        detected_by = data[pos:pos + id_len].decode("utf-8", errors="replace")
        pos += id_len

        ts_u32 = struct.unpack("<I", data[pos:pos + 4])[0]

        detection_type = _INDEX_TO_DETECTION_TYPE.get(det_type_idx, DetectionType.OBSTACLE)
        confidence = confidence_u8 / 255.0
        severity = max(1, min(10, severity_u8))
        latitude = lat_i32 / 1e7
        longitude = lon_i32 / 1e7
        altitude = float(alt_u16)
        timestamp = datetime.fromtimestamp(ts_u32, tz=timezone.utc)

        return Detection.create(
            detection_type=detection_type,
            position=GeoPosition(
                latitude=latitude,
                longitude=longitude,
                altitude=altitude,
            ),
            confidence=confidence,
            detected_by=detected_by or fallback_detected_by,
            severity=severity,
        )

    except (struct.error, ValueError) as e:
        logger.warning(f"Failed to decode TUNNEL detection: {e}")
        return None


class TunnelTransmitter:
    """Sends Detection objects as MAVLink TUNNEL messages via pymavlink."""

    def __init__(self, mavlink_connection, source_system: int = 1, source_component: int = 191):
        self._mav = mavlink_connection
        self._src_sys = source_system
        self._src_comp = source_component

    def send_detection(self, detection: Detection, target_system: int = 0, target_component: int = 0) -> bool:
        """Encode and send a detection as a MAVLink TUNNEL message.

        target_system=0 broadcasts to all systems on the link.
        """
        payload = encode_detection(detection)

        # Pad to 128 bytes (TUNNEL payload is fixed-size in MAVLink)
        padded = payload + b"\x00" * (TUNNEL_MAX_PAYLOAD - len(payload))

        try:
            self._mav.mav.tunnel_send(
                target_system,
                target_component,
                TUNNEL_PAYLOAD_TYPE,
                len(payload),
                padded,
            )
            logger.debug(
                f"Sent TUNNEL detection: {detection.detection_type.value} "
                f"conf={detection.confidence:.2f} ({len(payload)}B)"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send TUNNEL detection: {e}")
            return False
