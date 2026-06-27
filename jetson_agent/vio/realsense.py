"""RealSense D435i VIO backend (baseline scaffold).

Depth-scaled sparse optical flow for translation + gyro-integrated yaw. This is a
deliberately simple baseline behind the VioBackend interface; a production build
swaps `poll()` for VINS-Fusion / OpenVINS / RTAB-Map without touching the agent.

start() raises if pyrealsense2/opencv or the device is missing, so detect.py
falls back to NullVio. Needs a Jetson with the camera attached to validate — it
cannot be exercised in CI.
"""

from __future__ import annotations

import logging
import threading
import time

from .base import VioBackend, VioDelta

log = logging.getLogger("jetson_agent.vio.realsense")


class RealSenseVio(VioBackend):
    name = "realsense"

    def __init__(self, max_features: int = 200) -> None:
        self._rs = None
        self._cv2 = None
        self._np = None
        self._pipe = None
        self._max_features = max_features
        self._prev_gray = None
        self._prev_ts = None
        self._yaw_rate = 0.0
        self._lock = threading.Lock()
        self._ok = False

    @property
    def available(self) -> bool:
        return self._ok

    def start(self) -> None:
        import pyrealsense2 as rs   # raises ImportError if absent
        import numpy as np
        import cv2                  # raises if opencv missing

        self._rs, self._np, self._cv2 = rs, np, cv2
        pipe = rs.pipeline()
        cfg = rs.config()
        cfg.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
        cfg.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
        try:
            cfg.enable_stream(rs.stream.gyro)   # D435i has an IMU; D435 does not
        except Exception:
            pass
        pipe.start(cfg)             # raises if no device
        self._pipe = pipe
        self._ok = True
        log.info("RealSense VIO started")

    def poll(self) -> "VioDelta | None":
        if not self._ok:
            return None
        rs, np, cv2 = self._rs, self._np, self._cv2
        try:
            frames = self._pipe.poll_for_frames()
            if not frames:
                return None
            color = frames.get_color_frame()
            depth = frames.get_depth_frame()
            for f in frames:
                if f.is_motion_frame() and f.get_profile().stream_type() == rs.stream.gyro:
                    g = f.as_motion_frame().get_motion_data()
                    self._yaw_rate = float(g.y)  # camera up-axis angular rate
            if not color or not depth:
                return None

            gray = cv2.cvtColor(np.asanyarray(color.get_data()), cv2.COLOR_BGR2GRAY)
            now = time.monotonic()
            if self._prev_gray is None:
                self._prev_gray, self._prev_ts = gray, now
                return None
            dt = now - self._prev_ts

            p0 = cv2.goodFeaturesToTrack(self._prev_gray, self._max_features, 0.01, 7)
            if p0 is None:
                self._prev_gray, self._prev_ts = gray, now
                return VioDelta(dt=dt, tracking_ok=False)
            p1, st, _ = cv2.calcOpticalFlowPyrLK(self._prev_gray, gray, p0, None)
            good_new, good_old = p1[st == 1], p0[st == 1]
            self._prev_gray, self._prev_ts = gray, now
            if len(good_new) < 8:
                return VioDelta(dt=dt, tracking_ok=False)

            # Median pixel flow -> metric translation via median scene depth and a
            # pinhole approximation (fx ~= 380 px for the D435 color stream).
            flow = good_new - good_old
            du = float(np.median(flow[:, 0]))
            dv = float(np.median(flow[:, 1]))
            zc = float(np.median(np.asanyarray(depth.get_data()))) * depth.get_units()
            zc = zc if zc > 0.1 else 3.0
            fx = 380.0
            # Camera moves opposite to scene flow; +u (right) -> moved left, etc.
            d_right = -du * zc / fx
            d_forward = dv * zc / fx     # crude: vertical flow as forward proxy
            d_yaw = self._yaw_rate * dt
            return VioDelta(d_forward=d_forward, d_right=d_right, d_yaw=d_yaw,
                            dt=dt, cov=0.06, tracking_ok=True)
        except Exception as e:  # never raise into the agent
            log.debug("realsense poll failed: %s", e)
            return None

    def stop(self) -> None:
        self._ok = False
        if self._pipe is not None:
            try:
                self._pipe.stop()
            except Exception:
                pass
