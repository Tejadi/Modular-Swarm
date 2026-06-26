"""Entry point: python -m olympus_link

Run on the command station next to the gateway nRF. Configuration comes from
the environment (see config.py); the common knobs are also exposed as flags.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys

# Allow running both as `python -m olympus_link` (from nRF-swarm/) and directly.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "proto"))
sys.path.insert(0, os.path.dirname(__file__))

from config import Config  # noqa: E402
from service import SwarmLinkService  # noqa: E402


def main() -> None:
    cfg = Config.from_env()
    ap = argparse.ArgumentParser(description="Olympus swarm command-station link")
    ap.add_argument("--port", default=cfg.serial_port, help="gateway serial device / PTY")
    ap.add_argument("--prefix", default=cfg.key_prefix, help="Zenoh key prefix (ceres/olympus)")
    ap.add_argument("--sink", default=cfg.sink, choices=["rest", "zenoh", "dryrun"])
    ap.add_argument("--gateway-eui", default=cfg.gateway_eui)
    ap.add_argument("--zenoh-rest", default=cfg.zenoh_rest)
    ap.add_argument("--vehicle-api", default=cfg.vehicle_api)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    cfg.serial_port = args.port
    cfg.key_prefix = args.prefix
    cfg.sink = args.sink
    cfg.gateway_eui = args.gateway_eui
    cfg.zenoh_rest = args.zenoh_rest
    cfg.vehicle_api = args.vehicle_api

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    svc = SwarmLinkService(cfg)

    async def runner() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, svc.stop)
            except NotImplementedError:  # pragma: no cover
                pass
        await svc.run()

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
