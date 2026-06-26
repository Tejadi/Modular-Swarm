"""Entry point: python -m jetson_agent --port /dev/ttyACM<data>

Runs on each agent's Jetson next to its local nRF module's data port.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "proto"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "olympus_link"))
sys.path.insert(0, os.path.dirname(__file__))

from config import Config  # noqa: E402
from agent import JetsonAgent  # noqa: E402


def main() -> None:
    cfg = Config.from_env()
    ap = argparse.ArgumentParser(description="Olympus swarm Jetson agent")
    ap.add_argument("--port", default=os.environ.get("SWARM_NRF_PORT", "/dev/ttyACM1"),
                    help="local nRF swarm data port")
    ap.add_argument("--prefix", default=cfg.key_prefix)
    ap.add_argument("--sink", default=cfg.sink, choices=["rest", "zenoh", "dryrun"])
    ap.add_argument("--zenoh-rest", default=cfg.zenoh_rest)
    ap.add_argument("--vehicle-api", default=cfg.vehicle_api)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    cfg.key_prefix = args.prefix
    cfg.sink = args.sink
    cfg.zenoh_rest = args.zenoh_rest
    cfg.vehicle_api = args.vehicle_api

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    agent = JetsonAgent(cfg, args.port)

    async def runner() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, agent.stop)
            except NotImplementedError:  # pragma: no cover
                pass
        await agent.run()

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
