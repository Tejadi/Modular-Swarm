"""Entry point: python -m jetson_perception"""

from __future__ import annotations

import argparse
import logging

from .detector import DEFAULT_PORT, run_fake, run_yolo


def main() -> None:
    ap = argparse.ArgumentParser(description="Swarm vehicle perception process")
    ap.add_argument("--fake", action="store_true", help="emit synthetic detections")
    ap.add_argument("--model", default="yolov8n.pt", help="Ultralytics model path")
    ap.add_argument("--source", default="0", help="camera index / video source")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--conf", type=float, default=0.4)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    if args.fake:
        run_fake(port=args.port)
    else:
        run_yolo(args.model, args.source, port=args.port, conf=args.conf)


if __name__ == "__main__":
    main()
