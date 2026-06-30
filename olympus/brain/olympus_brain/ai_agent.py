"""Strategic AI Agent — drift monitoring, recall/retrain/redeploy cycle.

Runs at the base station alongside the CentralAggregator. Monitors concept
drift (Page-Hinkley test), and when the distribution has shifted sufficiently:

1. Recalls all scouts (RECALL_FOR_UPDATE via strategic/TCP layer)
2. Retrains the global model on recent detections
3. Pushes converged weights to olympus/swarmnet/model/global
4. Redeploys scouts (REDEPLOY via strategic/TCP layer)

Also exposes a status dict for the Vehicle API to serve, and logs all
actions to a local SQLite audit trail.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import struct
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from olympus_brain.node import OlympusNode, NodeConfig
from olympus_brain.protocol import (
    CommandType,
    DroneRole,
    ZenohKeys,
)
from olympus_brain.swarmnet import CentralAggregator

logger = logging.getLogger(__name__)

DEFAULT_AGENT_DB = "/tmp/olympus_ai_agent.db"


class AIAgent:
    """Strategic-layer AI agent for fleet model lifecycle management."""

    def __init__(
        self,
        aggregator: CentralAggregator,
        node: OlympusNode,
        *,
        check_interval: float = 10.0,
        db_path: str = DEFAULT_AGENT_DB,
    ):
        self.aggregator = aggregator
        self.node = node
        self.check_interval = check_interval
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # Audit trail
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

        # Track state
        self._model_version = 0
        self._last_recall: Optional[datetime] = None
        self._last_redeploy: Optional[datetime] = None
        self._recall_count = 0
        self._retrain_count = 0

        # Model version ACK tracking
        self._pending_acks: set[str] = set()  # drone_ids we expect ACKs from
        self._received_acks: dict[str, int] = {}  # drone_id → version confirmed
        self._ack_timeout_s = 15.0  # max wait for all ACKs before redeploying anyway

    def _init_db(self) -> None:
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                model_version INTEGER,
                accuracy_before REAL,
                accuracy_after REAL,
                details TEXT
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_agent_actions_ts
            ON agent_actions(timestamp)
        """)
        self._conn.commit()
        logger.info(f"AI Agent audit DB initialized at {self._db_path}")

    def _log_action(
        self,
        action: str,
        accuracy_before: float = 0.0,
        accuracy_after: float = 0.0,
        details: str = "",
    ) -> None:
        try:
            self._conn.execute(
                "INSERT INTO agent_actions "
                "(action, timestamp, model_version, accuracy_before, accuracy_after, details) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    action,
                    datetime.now(timezone.utc).isoformat(),
                    self._model_version,
                    accuracy_before,
                    accuracy_after,
                    details,
                ),
            )
            self._conn.commit()
        except Exception as e:
            logger.error(f"Failed to log agent action: {e}")

    async def start(self) -> None:
        logger.info("AI Agent starting — monitoring drift and managing fleet model lifecycle")
        self._running = True

        # Subscribe to detections so the aggregator trains
        self.node.on_detection(self.aggregator.receive_detection)

        # Listen for MODEL_ACK commands from drones
        self.node.on_command(self._on_command)

        self._task = asyncio.create_task(self._monitor_loop())

    def _on_command(self, cmd) -> None:
        """Handle incoming commands — specifically MODEL_ACK from drones."""
        if cmd.command.type == CommandType.MODEL_ACK:
            payload = cmd.command.payload or {}
            drone_id = payload.get("drone_id", cmd.issued_by)
            version = payload.get("model_version", 0)
            self._received_acks[drone_id] = version
            self._pending_acks.discard(drone_id)
            logger.info(f"MODEL_ACK received from {drone_id} (v{version})")

    async def stop(self) -> None:
        logger.info("AI Agent stopping")
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._conn:
            self._conn.close()
            self._conn = None

    async def _monitor_loop(self) -> None:
        while self._running:
            try:
                if self.aggregator.should_publish():
                    accuracy_before = self.aggregator.trainer.get_accuracy()
                    logger.warning(
                        f"Drift detected or retrain interval reached "
                        f"(accuracy={accuracy_before:.3f}). Initiating recall/retrain/redeploy."
                    )
                    await self._recall_retrain_redeploy(accuracy_before)

                await asyncio.sleep(self.check_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"AI Agent monitor error: {e}")
                await asyncio.sleep(self.check_interval)

    async def _recall_retrain_redeploy(self, accuracy_before: float) -> None:
        """Full recall → retrain → push model → wait for ACKs → redeploy cycle."""

        # 1. Recall all scouts
        await self.recall_fleet()

        # 2. Brief pause for scouts to stop scanning
        await asyncio.sleep(2.0)

        # 3. Get new global weights from aggregator
        weights, version = self.aggregator.get_global_weights()
        self._model_version = version

        # 4. Track which drones need to ACK
        scouts = self.node.swarm_state.get_scouts()
        self._pending_acks = {s.drone_id for s in scouts}
        self._received_acks.clear()

        # 5. Publish weights to Zenoh (strategic layer)
        self._publish_global_model(weights, version)
        accuracy_after = self.aggregator.trainer.get_accuracy()

        self._retrain_count += 1
        self._log_action(
            "retrain_push",
            accuracy_before=accuracy_before,
            accuracy_after=accuracy_after,
            details=f"model_v{version}, {len(weights)} bytes",
        )

        logger.info(
            f"Global model v{version} pushed ({len(weights)}B). "
            f"Accuracy: {accuracy_before:.3f} → {accuracy_after:.3f}"
        )

        # 6. Wait for MODEL_ACKs from all scouts (with timeout)
        ack_start = time.monotonic()
        while self._pending_acks and (time.monotonic() - ack_start) < self._ack_timeout_s:
            await asyncio.sleep(0.5)

        if self._pending_acks:
            missing = list(self._pending_acks)
            logger.warning(
                f"Model ACK timeout — {len(missing)} drones did not confirm: {missing}. "
                f"Redeploying anyway."
            )
            self._log_action(
                "ack_timeout",
                details=f"missing_acks={missing}, version={version}",
            )
        else:
            logger.info(f"All {len(self._received_acks)} drones confirmed model v{version}")

        # 7. Redeploy fleet
        await self.redeploy_fleet()

    async def recall_fleet(self) -> None:
        """Send RECALL_FOR_UPDATE to all scouts (strategic layer)."""
        logger.warning("RECALL: sending RECALL_FOR_UPDATE to all scouts")
        self.node.send_command("*", CommandType.RECALL_FOR_UPDATE)
        self._last_recall = datetime.now(timezone.utc)
        self._recall_count += 1
        self._log_action("recall", details="all scouts")

    async def redeploy_fleet(self) -> None:
        """Send REDEPLOY to all scouts (strategic layer)."""
        logger.info("REDEPLOY: sending REDEPLOY to all scouts")
        self.node.send_command("*", CommandType.REDEPLOY)
        self._last_redeploy = datetime.now(timezone.utc)
        self._log_action("redeploy", details="all scouts")

    async def force_retrain(self) -> None:
        """Manually trigger the full recall/retrain/redeploy cycle."""
        accuracy_before = self.aggregator.trainer.get_accuracy()
        logger.info(f"Force retrain requested (current accuracy={accuracy_before:.3f})")
        self._log_action("force_retrain_requested", accuracy_before=accuracy_before)
        await self._recall_retrain_redeploy(accuracy_before)

    def _publish_global_model(self, weights: bytes, version: int) -> None:
        """Publish version-prefixed model weights to Zenoh."""
        header = struct.pack("<I", version)
        payload = header + weights
        key = ZenohKeys.swarmnet_global_model()
        if self.node._session:
            self.node._session.put(key, payload)
            logger.info(f"Published global model v{version} to {key}")

    def get_status(self) -> dict:
        """Return status dict for Vehicle API consumption."""
        status = self.aggregator.get_status()
        return {
            "model_version": self._model_version,
            "accuracy": status.accuracies.get("global", 0.0),
            "active_drones": status.active_drones,
            "contributions": status.contributions,
            "drift_detected": self.aggregator._drift_retrain_count > 0,
            "recall_count": self._recall_count,
            "retrain_count": self._retrain_count,
            "last_recall": self._last_recall.isoformat() if self._last_recall else None,
            "last_redeploy": self._last_redeploy.isoformat() if self._last_redeploy else None,
        }

    def get_history(self, limit: int = 50) -> list[dict]:
        """Return recent agent actions from audit log."""
        try:
            rows = self._conn.execute(
                "SELECT id, action, timestamp, model_version, "
                "accuracy_before, accuracy_after, details "
                "FROM agent_actions ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [
                {
                    "id": r[0],
                    "action": r[1],
                    "timestamp": r[2],
                    "model_version": r[3],
                    "accuracy_before": r[4],
                    "accuracy_after": r[5],
                    "details": r[6],
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Failed to query agent history: {e}")
            return []
