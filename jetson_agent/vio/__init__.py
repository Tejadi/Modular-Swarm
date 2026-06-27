"""Pluggable camera VIO for the Jetson agent.

    from jetson_agent.vio import make_vio
    vio = make_vio()        # auto-detects a camera, returns a started backend
    delta = vio.poll()      # VioDelta or None
    vio.stop()
"""
from .base import VioBackend, VioDelta  # noqa: F401
from .detect import make_vio            # noqa: F401

__all__ = ["VioBackend", "VioDelta", "make_vio"]
