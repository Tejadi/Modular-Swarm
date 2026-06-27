"""Perception pipeline: publisher (detector) -> UDP -> reader (agent).

    python3 sim/test_perception.py
"""

from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "jetson_agent"))
sys.path.insert(0, os.path.join(HERE, "..", "jetson_perception"))

from detections import DetectionReader      # jetson_agent/detections.py
from detector import DetectionPublisher     # jetson_perception/detector.py

PORT = 47811   # avoid clashing with a real perception process on the default port


def test_publish_then_read():
    reader = DetectionReader(port=PORT)
    pub = DetectionPublisher(port=PORT)
    try:
        assert reader.poll() == []                      # nothing yet
        items = [{"cls": "person", "conf": 0.91, "bbox": [10, 20, 40, 60],
                  "bearing_deg": -15.0, "range_m": 8.0}]
        pub.publish(items)
        time.sleep(0.05)
        got = reader.poll()
        assert len(got) == 1 and got[0]["cls"] == "person", got
        # a newer frame supersedes the old cached list
        pub.publish([])
        time.sleep(0.05)
        assert reader.poll() == []
    finally:
        pub.close()
        reader.close()


def test_reader_no_publisher_is_empty():
    reader = DetectionReader(port=PORT + 1)
    try:
        assert reader.poll() == []      # best-effort: no publisher -> empty, no error
    finally:
        reader.close()


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  FAIL {fn.__name__}: {e!r}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run())
