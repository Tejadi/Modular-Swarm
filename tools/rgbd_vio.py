#!/usr/bin/env python3
"""Metric RGB-D visual odometry on a RealSense D435i (depth-scaled, EKF-ready).

Color ORB features + per-feature depth -> solvePnP gives the camera's motion
between frames in REAL METERS (depth supplies the scale monocular VIO lacks, and
solvePnP stays well-conditioned when static instead of degenerate). Emits a
VioDelta-style (d_forward, d_right, d_yaw, dt, tracking) line per few frames.

Needs only opencv + pyrealsense2 + numpy (no torch). Standalone bring-up tool;
the integrated backend is jetson_agent/vio/realsense.py.

Usage: python3 rgbd_vio.py [run_s]
"""
import pyrealsense2 as rs, numpy as np, cv2, time, sys

RUN_S = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0

pipe = rs.pipeline(); cfg = rs.config()
cfg.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
cfg.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
prof = pipe.start(cfg)
align = rs.align(rs.stream.color)
intr = prof.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()
K = np.array([[intr.fx, 0, intr.ppx], [0, intr.fy, intr.ppy], [0, 0, 1]], dtype=np.float64)
dist = np.array(intr.coeffs[:5], dtype=np.float64)

orb = cv2.ORB_create(1000)
bf = cv2.BFMatcher(cv2.NORM_HAMMING)
prev_kp = prev_des = prev_depth = None
t_last = time.time(); t_start = t_last; n = 0
print(f"RGB-D VIO 640x480 fx={intr.fx:.0f} cx={intr.ppx:.0f} for ~{RUN_S:.0f}s")
print(f"{'frame':>5} {'feat':>5} {'pnp':>4} {'dFwd(m)':>8} {'dRt(m)':>8} {'dYaw':>6} {'fps':>5} track")
try:
    while time.time() - t_start < RUN_S:
        fr = align.process(pipe.wait_for_frames())
        d = fr.get_depth_frame(); c = fr.get_color_frame()
        if not d or not c:
            continue
        color = np.asanyarray(c.get_data())
        depth = np.asanyarray(d.get_data()).astype(np.float32) * 0.001  # meters
        gray = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)
        kp, des = orb.detectAndCompute(gray, None)
        now = time.time(); dt = now - t_last; t_last = now; n += 1
        fps = 1.0 / dt if dt > 0 else 0.0
        dfwd = dright = dyaw = 0.0; npnp = 0; track = "no"
        if prev_des is not None and des is not None and len(kp) > 8 and len(prev_kp) > 8:
            knn = bf.knnMatch(prev_des, des, k=2)
            good = [m for m, n2 in knn if m.distance < 0.75 * n2.distance] if knn and len(knn[0]) == 2 else []
            obj, img = [], []
            for m in good:
                u, v = prev_kp[m.queryIdx].pt
                iu, iv = int(u), int(v)
                z = prev_depth[iv, iu] if (0 <= iv < 480 and 0 <= iu < 640) else 0.0
                if 0.2 < z < 10.0:
                    x = (u - intr.ppx) / intr.fx * z
                    y = (v - intr.ppy) / intr.fy * z
                    obj.append([x, y, z]); img.append(kp[m.trainIdx].pt)
            if len(obj) >= 6:
                obj = np.array(obj, np.float32); img = np.array(img, np.float32)
                ok, rvec, tvec, inl = cv2.solvePnPRansac(obj, img, K, dist,
                                                         reprojectionError=3.0, iterationsCount=100)
                if ok and inl is not None:
                    npnp = len(inl)
                    t = tvec.flatten()
                    dfwd, dright = float(t[2]), float(t[0])
                    R, _ = cv2.Rodrigues(rvec)
                    dyaw = float(np.degrees(np.arctan2(-R[2, 0], R[0, 0])))
                    moving = abs(dfwd) + abs(dright) > 0.01 or abs(dyaw) > 0.5
                    track = ("MOVING" if moving else "still") if npnp >= 8 else "weak"
        if n % 5 == 0:
            print(f"{n:5d} {len(kp):5d} {npnp:4d} {dfwd:+8.3f} {dright:+8.3f} {dyaw:+6.1f} {fps:5.1f} {track}")
        prev_kp, prev_des, prev_depth = kp, des, depth
finally:
    pipe.stop()
    print("VIO stopped.")
