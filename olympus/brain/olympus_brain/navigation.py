"""GPS health monitor and VIO fallback navigation.

Monitors GPS quality (HDOP, satellite count) and switches to Visual-Inertial
Odometry (VIO) when GPS is degraded. Publishes position with a source flag
so downstream consumers know the position origin.

VIO integration uses the Jetson's onboard camera + IMU through OpenCV.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable

from olympus_brain.protocol import GeoPosition

logger = logging.getLogger(__name__)


class PositionSource(Enum):
    GPS = "gps"
    VIO = "vio"
    GPS_VIO_FUSED = "gps_vio_fused"
    DEAD_RECKONING = "dead_reckoning"


@dataclass
class GpsHealth:
    satellites: int = 0
    hdop: float = 99.0
    fix_type: int = 0  # 0=none, 2=2D, 3=3D
    last_fix: float = 0.0

    @property
    def is_good(self) -> bool:
        return self.satellites >= 6 and self.hdop < 4.0 and self.fix_type >= 3

    @property
    def is_usable(self) -> bool:
        return self.satellites >= 4 and self.hdop < 8.0 and self.fix_type >= 2


@dataclass
class VioState:
    enabled: bool = False
    tracking_quality: float = 0.0  # 0.0 to 1.0
    position_drift_m: float = 0.0
    last_update: float = 0.0

    @property
    def is_tracking(self) -> bool:
        return self.enabled and self.tracking_quality > 0.3


@dataclass
class NavigationState:
    position: GeoPosition = field(default_factory=GeoPosition)
    source: PositionSource = PositionSource.GPS
    gps: GpsHealth = field(default_factory=GpsHealth)
    vio: VioState = field(default_factory=VioState)
    last_known_gps: Optional[GeoPosition] = None
    gps_denied_since: Optional[float] = None


class NavigationManager:
    """Manages position sources and GPS/VIO failover."""

    # Thresholds for GPS quality assessment
    GPS_GOOD_SATS = 6
    GPS_MIN_SATS = 4
    GPS_GOOD_HDOP = 4.0
    GPS_MAX_HDOP = 8.0
    GPS_TIMEOUT_S = 5.0

    def __init__(
        self,
        drone_id: str,
        on_source_change: Optional[Callable[[PositionSource, PositionSource], None]] = None,
    ):
        self.drone_id = drone_id
        self.state = NavigationState()
        self._on_source_change = on_source_change
        self._running = False

    @property
    def position(self) -> GeoPosition:
        return self.state.position

    @property
    def source(self) -> PositionSource:
        return self.state.source

    def update_gps(
        self,
        position: GeoPosition,
        satellites: int,
        hdop: float,
        fix_type: int = 3,
    ) -> None:
        """Update GPS state from MAVLink GLOBAL_POSITION_INT + GPS_RAW_INT."""
        self.state.gps.satellites = satellites
        self.state.gps.hdop = hdop
        self.state.gps.fix_type = fix_type
        self.state.gps.last_fix = time.time()

        if self.state.gps.is_usable:
            self.state.last_known_gps = GeoPosition(
                latitude=position.latitude,
                longitude=position.longitude,
                altitude=position.altitude,
                heading=position.heading,
            )

            if self.state.gps.is_good:
                self._set_source(PositionSource.GPS)
                self.state.position = position
                self.state.gps_denied_since = None
            elif self.state.vio.is_tracking:
                # Fuse GPS + VIO when GPS is usable but not great
                self._set_source(PositionSource.GPS_VIO_FUSED)
                self.state.position = position  # GPS primary, VIO for smoothing

    def update_vio(
        self,
        delta_x: float,
        delta_y: float,
        delta_z: float,
        tracking_quality: float,
    ) -> None:
        """Update VIO state with relative position delta from camera/IMU.

        delta_x/y/z are in meters relative to VIO origin.
        In GPS-denied mode, these deltas are integrated from the last known GPS position.
        """
        self.state.vio.enabled = True
        self.state.vio.tracking_quality = tracking_quality
        self.state.vio.last_update = time.time()

        if self.state.source in (PositionSource.VIO, PositionSource.DEAD_RECKONING):
            # Integrate VIO deltas from last known GPS position
            if self.state.last_known_gps:
                # Approximate: 1 degree lat ≈ 111,320m, 1 degree lon varies
                import math
                lat = self.state.last_known_gps.latitude
                meters_per_deg_lat = 111_320.0
                meters_per_deg_lon = 111_320.0 * math.cos(math.radians(lat))

                self.state.position = GeoPosition(
                    latitude=lat + delta_y / meters_per_deg_lat,
                    longitude=self.state.last_known_gps.longitude + delta_x / meters_per_deg_lon,
                    altitude=self.state.last_known_gps.altitude + delta_z,
                    heading=self.state.position.heading,
                )

    def check_gps_health(self) -> None:
        """Periodically check if GPS is lost and switch to VIO."""
        now = time.time()
        gps = self.state.gps

        gps_age = now - gps.last_fix if gps.last_fix > 0 else float("inf")

        if not gps.is_usable or gps_age > self.GPS_TIMEOUT_S:
            # GPS lost
            if self.state.gps_denied_since is None:
                self.state.gps_denied_since = now
                logger.warning(
                    f"GPS DENIED — sats={gps.satellites} hdop={gps.hdop:.1f} "
                    f"age={gps_age:.1f}s"
                )

            if self.state.vio.is_tracking:
                self._set_source(PositionSource.VIO)
            else:
                self._set_source(PositionSource.DEAD_RECKONING)

    def _set_source(self, new_source: PositionSource) -> None:
        if new_source != self.state.source:
            old = self.state.source
            self.state.source = new_source
            logger.info(f"Position source: {old.value} → {new_source.value}")
            if self._on_source_change:
                self._on_source_change(old, new_source)

    async def run(self) -> None:
        """Background task to periodically check GPS health."""
        self._running = True
        while self._running:
            try:
                self.check_gps_health()
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"NavigationManager error: {e}")
                await asyncio.sleep(2.0)

    def stop(self) -> None:
        self._running = False
