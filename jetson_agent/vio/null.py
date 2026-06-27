"""No-camera fallback: a VIO backend that never produces motion."""

from __future__ import annotations

from .base import VioBackend


class NullVio(VioBackend):
    name = "null"

    @property
    def available(self) -> bool:
        return False
