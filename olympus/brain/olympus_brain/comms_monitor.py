"""Communications link quality monitor with degraded operating modes.

Tracks link quality for each comm channel (LoRa RSSI, ELRS LQ%, WiFi signal)
and transitions between operating modes based on available comms:
  FULL_COMMS → DEGRADED → MINIMAL → DENIED

Transitions have hysteresis to prevent flapping between modes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class CommsMode(Enum):
    FULL_COMMS = "full_comms"    # All channels up — normal operation
    DEGRADED = "degraded"        # WiFi down, LoRa/ELRS only — reduce rates
    MINIMAL = "minimal"          # Only LoRa — critical telemetry only
    DENIED = "denied"            # All comms lost — store-and-forward


@dataclass
class LinkState:
    """State of a single communication link."""
    name: str
    connected: bool = False
    quality: float = 0.0      # 0.0 to 1.0
    rssi_dbm: int = -120
    last_rx: float = 0.0      # time.time() of last received packet
    timeout_s: float = 10.0   # Consider disconnected after this


@dataclass
class CommsState:
    """Aggregate comms state across all links."""
    mode: CommsMode = CommsMode.FULL_COMMS
    wifi: LinkState = field(default_factory=lambda: LinkState(name="wifi", timeout_s=10.0))
    elrs: LinkState = field(default_factory=lambda: LinkState(name="elrs", timeout_s=5.0))
    lora: LinkState = field(default_factory=lambda: LinkState(name="lora", timeout_s=15.0))
    mode_since: float = field(default_factory=time.time)

    @property
    def any_link_up(self) -> bool:
        return self.wifi.connected or self.elrs.connected or self.lora.connected

    @property
    def active_links(self) -> list[str]:
        links = []
        if self.wifi.connected:
            links.append("wifi")
        if self.elrs.connected:
            links.append("elrs")
        if self.lora.connected:
            links.append("lora")
        return links


@dataclass
class CommsModePolicy:
    """Telemetry rate and behavior per comms mode."""
    telemetry_rate_hz: float
    detection_batch_interval_s: float
    model_updates_enabled: bool
    text_commands_only: bool

    @classmethod
    def for_mode(cls, mode: CommsMode) -> CommsModePolicy:
        return {
            CommsMode.FULL_COMMS: cls(
                telemetry_rate_hz=5.0,
                detection_batch_interval_s=0.0,
                model_updates_enabled=True,
                text_commands_only=False,
            ),
            CommsMode.DEGRADED: cls(
                telemetry_rate_hz=1.0,
                detection_batch_interval_s=5.0,
                model_updates_enabled=False,
                text_commands_only=True,
            ),
            CommsMode.MINIMAL: cls(
                telemetry_rate_hz=0.2,
                detection_batch_interval_s=30.0,
                model_updates_enabled=False,
                text_commands_only=True,
            ),
            CommsMode.DENIED: cls(
                telemetry_rate_hz=0.0,
                detection_batch_interval_s=0.0,  # buffer locally
                model_updates_enabled=False,
                text_commands_only=True,
            ),
        }[mode]


class CommsMonitor:
    """Monitors communication links and manages operating mode transitions."""

    # Hysteresis: require N consecutive checks before changing mode
    HYSTERESIS_COUNT = 3

    def __init__(
        self,
        drone_id: str,
        on_mode_change: Optional[Callable[[CommsMode, CommsMode], None]] = None,
    ):
        self.drone_id = drone_id
        self.state = CommsState()
        self._on_mode_change = on_mode_change
        self._pending_mode: Optional[CommsMode] = None
        self._pending_count: int = 0
        self._running = False

    @property
    def mode(self) -> CommsMode:
        return self.state.mode

    @property
    def policy(self) -> CommsModePolicy:
        return CommsModePolicy.for_mode(self.state.mode)

    def update_link(self, link_name: str, rssi: int = -120, quality: float = 0.0) -> None:
        """Update a link's state on packet reception."""
        link = getattr(self.state, link_name, None)
        if link is None:
            return
        link.connected = True
        link.rssi_dbm = rssi
        link.quality = quality
        link.last_rx = time.time()

    def mark_disconnected(self, link_name: str) -> None:
        """Explicitly mark a link as disconnected."""
        link = getattr(self.state, link_name, None)
        if link is not None:
            link.connected = False

    async def run(self) -> None:
        """Main monitoring loop — checks link timeouts and mode transitions."""
        self._running = True
        while self._running:
            try:
                now = time.time()

                # Check timeouts
                for link in [self.state.wifi, self.state.elrs, self.state.lora]:
                    if link.connected and (now - link.last_rx) > link.timeout_s:
                        link.connected = False
                        logger.warning(
                            f"Link {link.name} timed out "
                            f"(last rx {now - link.last_rx:.0f}s ago)"
                        )

                # Determine target mode
                target = self._evaluate_mode()

                # Apply hysteresis
                if target != self.state.mode:
                    if target == self._pending_mode:
                        self._pending_count += 1
                    else:
                        self._pending_mode = target
                        self._pending_count = 1

                    if self._pending_count >= self.HYSTERESIS_COUNT:
                        old_mode = self.state.mode
                        self.state.mode = target
                        self.state.mode_since = now
                        self._pending_mode = None
                        self._pending_count = 0

                        logger.info(
                            f"Comms mode: {old_mode.value} → {target.value} "
                            f"(links: {', '.join(self.state.active_links) or 'none'})"
                        )

                        if self._on_mode_change:
                            self._on_mode_change(old_mode, target)
                else:
                    self._pending_mode = None
                    self._pending_count = 0

                await asyncio.sleep(1.0)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"CommsMonitor error: {e}")
                await asyncio.sleep(2.0)

    def _evaluate_mode(self) -> CommsMode:
        """Determine the operating mode based on active links."""
        wifi = self.state.wifi.connected
        elrs = self.state.elrs.connected
        lora = self.state.lora.connected

        if wifi and (elrs or lora):
            return CommsMode.FULL_COMMS
        elif wifi or elrs:
            return CommsMode.DEGRADED if not wifi else CommsMode.FULL_COMMS
        elif lora:
            return CommsMode.MINIMAL
        else:
            return CommsMode.DENIED

    def stop(self) -> None:
        self._running = False
