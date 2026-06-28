# Camera bring-up (RealSense D435i on a scout Jetson)

Standalone tools to validate a depth camera on a Jetson and feed the swarm:
metric RGB-D visual odometry, plus a YOLO people detector that projects each
detection to map (lat/lon) coordinates and publishes it to the dashboard.

These are **bring-up / bench tools** (no nRF required). The integrated runtime
lives in `jetson_perception/` and `jetson_agent/vio/realsense.py`.

## What you get

- `rgbd_vio.py` — metric visual odometry: color ORB features + per-feature depth
  → `solvePnP` → camera motion in **real meters** (depth gives the scale that
  monocular VIO can't). Stable when static, ~25 fps at 640×480.
- `people_detect.py` — YOLOv8n person detector via **opencv's DNN** (so the
  Jetson needs no torch/ultralytics). Each detection gets a robust depth (median
  over the torso band), a 3D camera-frame position, and a geo (lat/lon) position
  from the vehicle pose, then publishes to `ceres/detection/{id}` for the map.

## 1. Dependencies

opencv + numpy ship with JetPack — reuse them. Only `pyrealsense2` is usually
missing. Online Jetson:

```
pip install --user -r tools/requirements-jetson.txt
```

Offline Jetson (or one behind a captive portal, where pip can't reach PyPI):
stage the wheel from a connected host and install with `--no-index`:

```
# on a host with internet:
pip download pyrealsense2 --only-binary=:all: --platform manylinux2014_aarch64 \
    --python-version 310 --implementation cp -d wheels/
scp wheels/*.whl <jetson>:~/wheels/
# on the jetson:
pip install --user --no-index --find-links ~/wheels pyrealsense2
```

Verify: `python3 -c "import pyrealsense2 as rs; print([d.get_info(rs.camera_info.name) for d in rs.context().query_devices()])"`

## 2. YOLO model (one file, no torch on the Jetson)

Export `yolov8n.onnx` once on any host with internet, then copy it over:

```
pip install ultralytics
python3 -c "from ultralytics import YOLO; YOLO('yolov8n.pt').export(format='onnx', imgsz=640, opset=12)"
scp yolov8n.onnx <jetson>:~/yolov8n.onnx
```

## 3. Run

```
# metric VIO — move the camera, watch dFwd/dRt (meters) and dYaw respond:
python3 tools/rgbd_vio.py 20

# people detector + depth->geo + publish to the dashboard:
DET_PUBLISH=1 ZENOH_REST=http://<command-station>:8000 DET_BY=<node-id> \
  python3 tools/people_detect.py ~/yolov8n.onnx 30 <veh_lat> <veh_lon> <veh_heading_deg>
```

`DET_PUBLISH=0` runs detection without publishing. `DET_CONF` overrides the
confidence threshold (default 0.5).

## Notes

- **Camera on USB 3** — the D435i needs a USB3 port to stream depth+color.
- **IMU**: the D435i's onboard IMU is not exposed through the stock V4L2 stack
  (it needs kernel HID/IIO modules). That's fine — depth already makes the VIO
  metric, and the swarm's IMU is the nRF's BNO055 feeding EKF #1. The Jetson's
  job is the visual odometry delta.
- The depth map (`Z16`) and stereo IR (`Y8I`) come through `pyrealsense2`; plain
  opencv/V4L2 only cleanly exposes the color stream.
