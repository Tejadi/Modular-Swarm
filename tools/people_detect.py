#!/usr/bin/env python3
"""YOLOv8n people detector on a RealSense D435i, with depth -> metric 3D -> geo.

Runs YOLO through opencv's DNN module reading yolov8n.onnx, so the Jetson needs
NO torch/ultralytics (just opencv + pyrealsense2 + numpy). Filters to COCO class
0 (person). Each detection gets:
  - robust depth (median over the upper-torso band of the box, metric)
  - 3D position in the camera frame (deprojected, meters)
  - geo position (lat/lon) from the vehicle pose + heading
and is published to zenoh (ceres/detection/{id}) so the dashboard renders it.

Standalone bring-up tool (no nRF needed). For the integrated runtime see
jetson_perception/ + jetson_agent/vio/realsense.py.

Usage: python3 people_detect.py [onnx] [run_s] [veh_lat] [veh_lon] [veh_heading_deg]
Env:   DET_PUBLISH=0 disables publish; DET_CONF, ZENOH_REST, DET_BY override.
"""
import pyrealsense2 as rs, numpy as np, cv2, time, sys, math, os, json, urllib.request

ONNX  = sys.argv[1] if len(sys.argv) > 1 else "yolov8n.onnx"
RUN_S = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
VLAT  = float(sys.argv[3]) if len(sys.argv) > 3 else 39.9526      # vehicle pose
VLON  = float(sys.argv[4]) if len(sys.argv) > 4 else -75.1652     # (Philly placeholder)
VHEAD = float(sys.argv[5]) if len(sys.argv) > 5 else 0.0          # heading deg from North, CW
CONF  = float(os.environ.get("DET_CONF", "0.5"))
IOU, INP = 0.45, 640

ZENOH_REST = os.environ.get("ZENOH_REST", "http://localhost:8000")
DET_BY     = os.environ.get("DET_BY", "command-station")
PUBLISH    = os.environ.get("DET_PUBLISH", "1") == "1"
PUB_EVERY  = 1.0


def publish_detection(det_id, payload):
    if not PUBLISH:
        return
    try:
        req = urllib.request.Request(
            f"{ZENOH_REST}/ceres/detection/{det_id}",
            data=json.dumps(payload).encode(), method="PUT",
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=2)
    except Exception as e:
        print(f"  [publish failed: {e}]")


def letterbox(img, new=640):
    h, w = img.shape[:2]
    r = min(new / h, new / w)
    nw, nh = int(round(w * r)), int(round(h * r))
    canvas = np.full((new, new, 3), 114, np.uint8)
    top, left = (new - nh) // 2, (new - nw) // 2
    canvas[top:top + nh, left:left + nw] = cv2.resize(img, (nw, nh))
    return canvas, r, left, top


def cam_to_geo(pt, vlat, vlon, vhead_deg):
    """camera xyz (x=right,y=down,z=forward,m) -> (lat,lon) via vehicle pose."""
    psi = math.radians(vhead_deg)
    fwd, right = pt[2], pt[0]
    dN = fwd * math.cos(psi) - right * math.sin(psi)
    dE = fwd * math.sin(psi) + right * math.cos(psi)
    return (vlat + dN / 111320.0,
            vlon + dE / (111320.0 * math.cos(math.radians(vlat))))


net = cv2.dnn.readNetFromONNX(ONNX)
net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)

pipe = rs.pipeline(); cfg = rs.config()
cfg.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
cfg.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
prof = pipe.start(cfg)
align = rs.align(rs.stream.color)
intr = prof.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()

t_start = time.time(); n = 0; last = 0.0
print(f"YOLOv8n people detector (opencv DNN/CPU) on D435i  for ~{RUN_S:.0f}s")
print(f"vehicle pose: lat={VLAT:.5f} lon={VLON:.5f} heading={VHEAD:.0f}deg  publish={PUBLISH}")
try:
    while time.time() - t_start < RUN_S:
        fr = align.process(pipe.wait_for_frames())
        d = fr.get_depth_frame(); c = fr.get_color_frame()
        if not d or not c:
            continue
        color = np.asanyarray(c.get_data()); n += 1
        lb, r, padx, pady = letterbox(color, INP)
        blob = cv2.dnn.blobFromImage(lb, 1 / 255.0, (INP, INP), swapRB=True, crop=False)
        net.setInput(blob)
        out = net.forward()[0].T                      # (8400, 84)
        person = out[:, 4]                            # class 0 score
        keep = person >= CONF
        if not keep.any():
            continue
        rows = out[keep]; scores = person[keep]
        boxes = []
        for (cx, cy, w, h) in rows[:, :4]:
            x = (cx - w / 2 - padx) / r; y = (cy - h / 2 - pady) / r
            boxes.append([int(x), int(y), int(w / r), int(h / r)])
        idxs = cv2.dnn.NMSBoxes(boxes, scores.tolist(), CONF, IOU)
        dets = []
        for i in np.array(idxs).flatten():
            x, y, w, h = boxes[i]
            # robust depth: median over the person's upper-torso band
            x0, x1 = max(0, int(x + w * 0.35)), min(639, int(x + w * 0.65))
            y0, y1 = max(0, int(y + h * 0.20)), min(479, int(y + h * 0.55))
            ucx, ucy = (x0 + x1) // 2, (y0 + y1) // 2
            sx = max(1, (x1 - x0) // 6); sy = max(1, (y1 - y0) // 6)
            samples = [zz for vv in range(y0, y1 + 1, sy) for uu in range(x0, x1 + 1, sx)
                       if 0.2 < (zz := d.get_distance(uu, vv)) < 12.0]
            z = float(np.median(samples)) if samples else 0.0
            pt = rs.rs2_deproject_pixel_to_point(intr, [ucx, ucy], z) if z > 0 else [0, 0, 0]
            rng = math.sqrt(pt[0] ** 2 + pt[1] ** 2 + pt[2] ** 2)
            bearing = math.degrees(math.atan2(pt[0], pt[2])) if z > 0 else 0.0
            lat, lon = cam_to_geo(pt, VLAT, VLON, VHEAD) if z > 0 else (VLAT, VLON)
            dets.append((float(scores[i]), z, rng, bearing, pt, lat, lon))
        if dets and time.time() - last > PUB_EVERY:
            last = time.time()
            print(f"--- frame {n}: {len(dets)} person(s) ---")
            for j, (s, z, rng, br, pt, lat, lon) in enumerate(dets):
                print(f"  person {s:.2f}  depth={z:4.2f}m  range={rng:4.2f}m  bearing={br:+4.0f}deg "
                      f"cam=({pt[0]:+.2f},{pt[1]:+.2f},{pt[2]:+.2f})  geo=({lat:.6f},{lon:.6f})")
                publish_detection(f"person-{j}", {
                    "type": "PERSON",
                    "detectedBy": DET_BY,
                    "position": {"latitude": lat, "longitude": lon, "altitude": 0.0},
                    "confidence": round(s, 2),
                    "range_m": round(rng, 2),
                    "bearing_deg": round(br, 1),
                    "timestamp": int(time.time() * 1000),
                    "status": "PENDING",
                })
finally:
    pipe.stop()
    print("detector stopped.")
