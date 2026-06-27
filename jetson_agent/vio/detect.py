"""Camera auto-detection -> a started VIO backend (NullVio if none).

Tries the backends in order of capability and returns the first that starts
cleanly. Env override: SWARM_VIO=none|realsense forces a choice.
"""

from __future__ import annotations

import logging
import os

from .base import VioBackend
from .null import NullVio

log = logging.getLogger("jetson_agent.vio.detect")


def make_vio(prefer: "str | None" = None) -> VioBackend:
    choice = (prefer or os.environ.get("SWARM_VIO", "auto")).lower()

    if choice == "none":
        log.info("VIO disabled (SWARM_VIO=none)")
        return NullVio()

    if choice in ("auto", "realsense"):
        try:
            from .realsense import RealSenseVio
            vio = RealSenseVio()
            vio.start()
            log.info("VIO backend: realsense")
            return vio
        except Exception as e:
            log.info("no RealSense VIO (%s); running without VIO", e)
            if choice == "realsense":
                log.warning("SWARM_VIO=realsense requested but unavailable")

    return NullVio()
