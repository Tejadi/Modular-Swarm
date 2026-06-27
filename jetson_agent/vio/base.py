"""VIO backend interface.

A backend yields incremental motion (a VioDelta) in the vehicle's local ENU frame
between polls; the agent folds it into the Jetson EKF (ekf.update_vio). Backends
run their own capture loop and must never raise into the agent — a missing camera
or lost tracking degrades to "no VIO", returning None from poll().
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VioDelta:
    # Body-frame motion since the last poll (the agent rotates it to ENU using the
    # current heading before feeding the EKF — see agent._fuse_vio).
    d_forward: float = 0.0  # meters, vehicle forward (camera +Z / optical axis)
    d_right: float = 0.0    # meters, vehicle right
    d_yaw: float = 0.0      # radians turned (right-hand, +CCW about up)
    dt: float = 0.0         # seconds elapsed
    cov: float = 0.04       # per-axis measurement variance (m^2)
    tracking_ok: bool = False


class VioBackend:
    """Subclasses implement start/poll/stop. The default is an inert no-op."""

    name = "base"

    def start(self) -> None:
        pass

    def poll(self) -> "VioDelta | None":
        """Motion since the last poll, or None if no new frame / tracking lost."""
        return None

    def stop(self) -> None:
        pass

    @property
    def available(self) -> bool:
        return False
