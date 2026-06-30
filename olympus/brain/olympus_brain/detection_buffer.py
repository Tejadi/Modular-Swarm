"""SQLite detection archive — primary on-device storage for all detections.

Stores detections locally with rich queryable columns. Serves dual purpose:
1. Ring buffer during comms blackout (sync to base when restored)
2. Local archive for AI Agent drift analysis and accuracy metrics

Deduplication by detection_id. LRU eviction when over max capacity.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import asyncio
from datetime import datetime, timezone
from typing import Optional

from olympus_brain.protocol import Detection

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "/tmp/olympus_detection_buffer.db"
MAX_BUFFERED_DETECTIONS = 50_000


class DetectionBuffer:
    """SQLite-backed detection archive with queryable columns."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH, max_size: int = MAX_BUFFERED_DETECTIONS):
        self.db_path = db_path
        self.max_size = max_size
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self) -> None:
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        # FULL sync ensures data survives sudden power loss (Jetson vibration/brownout)
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS detections (
                id TEXT PRIMARY KEY,
                detection_type TEXT NOT NULL,
                latitude REAL NOT NULL DEFAULT 0.0,
                longitude REAL NOT NULL DEFAULT 0.0,
                altitude REAL NOT NULL DEFAULT 0.0,
                confidence REAL NOT NULL DEFAULT 0.0,
                severity INTEGER NOT NULL DEFAULT 5,
                detected_by TEXT NOT NULL DEFAULT 'unknown',
                timestamp TEXT NOT NULL,
                metadata TEXT,
                synced INTEGER DEFAULT 0,
                data TEXT NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_detections_synced
            ON detections(synced, timestamp)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_detections_type
            ON detections(detection_type)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_detections_detected_by
            ON detections(detected_by)
        """)
        self._conn.commit()
        logger.info(f"Detection buffer initialized at {self.db_path} (max={self.max_size})")

    def buffer(self, detection: Detection) -> None:
        """Store a detection in the local archive."""
        try:
            data = detection.model_dump_json()
            ts = detection.timestamp.isoformat()

            self._conn.execute(
                "INSERT OR REPLACE INTO detections "
                "(id, detection_type, latitude, longitude, altitude, confidence, "
                "severity, detected_by, timestamp, metadata, synced, data) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
                (
                    detection.id,
                    detection.detection_type.value,
                    detection.position.latitude,
                    detection.position.longitude,
                    detection.position.altitude,
                    detection.confidence,
                    detection.severity,
                    detection.detected_by,
                    ts,
                    json.dumps(detection.metadata) if detection.metadata else None,
                    data,
                ),
            )
            self._conn.commit()

            # Enforce ring buffer: evict synced records FIRST, then oldest unsynced
            # This preserves unsynced detections during comms blackout
            count = self._conn.execute("SELECT COUNT(*) FROM detections").fetchone()[0]
            if count > self.max_size:
                excess = count - self.max_size
                # Phase 1: evict oldest SYNCED records first (already sent to base)
                synced_count = self._conn.execute(
                    "SELECT COUNT(*) FROM detections WHERE synced = 1"
                ).fetchone()[0]
                synced_to_evict = min(excess, synced_count)
                if synced_to_evict > 0:
                    self._conn.execute(
                        "DELETE FROM detections WHERE id IN "
                        "(SELECT id FROM detections WHERE synced = 1 "
                        "ORDER BY timestamp ASC LIMIT ?)",
                        (synced_to_evict,),
                    )
                # Phase 2: if still over capacity, evict oldest unsynced
                remaining_excess = excess - synced_to_evict
                if remaining_excess > 0:
                    self._conn.execute(
                        "DELETE FROM detections WHERE id IN "
                        "(SELECT id FROM detections ORDER BY timestamp ASC LIMIT ?)",
                        (remaining_excess,),
                    )
                    logger.warning(
                        f"Ring buffer: evicted {remaining_excess} UNSYNCED detections "
                        f"(no synced records left to evict)"
                    )
                self._conn.commit()
                logger.debug(
                    f"Ring buffer: evicted {excess} detections "
                    f"({synced_to_evict} synced, {remaining_excess if remaining_excess > 0 else 0} unsynced)"
                )

            logger.debug(f"Buffered detection {detection.id}")

        except Exception as e:
            logger.error(f"Failed to buffer detection: {e}")

    def get_unsynced(self, limit: int = 100) -> list[Detection]:
        """Retrieve unsynced detections for bulk upload."""
        try:
            rows = self._conn.execute(
                "SELECT id, data FROM detections WHERE synced = 0 ORDER BY timestamp ASC LIMIT ?",
                (limit,),
            ).fetchall()

            detections = []
            for row_id, data in rows:
                try:
                    det = Detection.model_validate_json(data)
                    detections.append(det)
                except Exception as e:
                    logger.warning(f"Failed to parse buffered detection {row_id}: {e}")

            return detections

        except Exception as e:
            logger.error(f"Failed to retrieve unsynced detections: {e}")
            return []

    def mark_synced(self, detection_ids: list[str]) -> None:
        """Mark detections as synced after successful upload."""
        if not detection_ids:
            return
        try:
            placeholders = ",".join("?" * len(detection_ids))
            self._conn.execute(
                f"UPDATE detections SET synced = 1 WHERE id IN ({placeholders})",
                detection_ids,
            )
            self._conn.commit()
            logger.info(f"Marked {len(detection_ids)} detections as synced")
        except Exception as e:
            logger.error(f"Failed to mark detections synced: {e}")

    def purge_synced(self) -> int:
        """Delete all synced detections from the buffer."""
        try:
            cursor = self._conn.execute("DELETE FROM detections WHERE synced = 1")
            self._conn.commit()
            count = cursor.rowcount
            if count > 0:
                logger.info(f"Purged {count} synced detections from buffer")
            return count
        except Exception as e:
            logger.error(f"Failed to purge synced detections: {e}")
            return 0

    def query(
        self,
        detection_type: Optional[str] = None,
        detected_by: Optional[str] = None,
        min_confidence: Optional[float] = None,
        limit: int = 100,
    ) -> list[Detection]:
        """Query detections with optional filters. Used by AI Agent for drift analysis."""
        try:
            conditions = []
            params: list = []

            if detection_type:
                conditions.append("detection_type = ?")
                params.append(detection_type)
            if detected_by:
                conditions.append("detected_by = ?")
                params.append(detected_by)
            if min_confidence is not None:
                conditions.append("confidence >= ?")
                params.append(min_confidence)

            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            params.append(limit)

            rows = self._conn.execute(
                f"SELECT data FROM detections {where} ORDER BY timestamp DESC LIMIT ?",
                params,
            ).fetchall()

            results = []
            for (data,) in rows:
                try:
                    results.append(Detection.model_validate_json(data))
                except Exception:
                    pass
            return results

        except Exception as e:
            logger.error(f"Detection query failed: {e}")
            return []

    def type_distribution(self) -> dict[str, int]:
        """Count detections by type. Used for drift monitoring."""
        try:
            rows = self._conn.execute(
                "SELECT detection_type, COUNT(*) FROM detections GROUP BY detection_type"
            ).fetchall()
            return {r[0]: r[1] for r in rows}
        except Exception:
            return {}

    def confidence_stats(self) -> dict[str, float]:
        """Aggregate confidence statistics for drift analysis."""
        try:
            row = self._conn.execute(
                "SELECT AVG(confidence), MIN(confidence), MAX(confidence), COUNT(*) FROM detections"
            ).fetchone()
            if row and row[3] > 0:
                return {
                    "mean": round(row[0], 4),
                    "min": round(row[1], 4),
                    "max": round(row[2], 4),
                    "count": row[3],
                }
        except Exception:
            pass
        return {"mean": 0.0, "min": 0.0, "max": 0.0, "count": 0}

    def recent_accuracy_window(self, window_size: int = 100) -> list[float]:
        """Return confidence values for the most recent N detections (proxy for accuracy)."""
        try:
            rows = self._conn.execute(
                "SELECT confidence FROM detections ORDER BY timestamp DESC LIMIT ?",
                (window_size,),
            ).fetchall()
            return [r[0] for r in rows]
        except Exception:
            return []

    @property
    def pending_count(self) -> int:
        try:
            return self._conn.execute(
                "SELECT COUNT(*) FROM detections WHERE synced = 0"
            ).fetchone()[0]
        except Exception:
            return 0

    @property
    def total_count(self) -> int:
        try:
            return self._conn.execute("SELECT COUNT(*) FROM detections").fetchone()[0]
        except Exception:
            return 0

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


class DetectionSyncer:
    """Syncs buffered detections to the base station when comms restore."""

    def __init__(
        self,
        buffer: DetectionBuffer,
        publish_callback,
        batch_size: int = 50,
        sync_interval_s: float = 5.0,
    ):
        self._buffer = buffer
        self._publish = publish_callback
        self._batch_size = batch_size
        self._sync_interval = sync_interval_s
        self._running = False

    async def run(self) -> None:
        """Continuously sync buffered detections."""
        self._running = True
        while self._running:
            try:
                pending = self._buffer.pending_count
                if pending > 0:
                    detections = self._buffer.get_unsynced(limit=self._batch_size)
                    synced_ids = []

                    for det in detections:
                        try:
                            self._publish(det)
                            synced_ids.append(det.id)
                        except Exception as e:
                            logger.warning(f"Failed to sync detection {det.id}: {e}")
                            break  # Stop on first failure — comms may be down again

                    if synced_ids:
                        self._buffer.mark_synced(synced_ids)
                        logger.info(
                            f"Synced {len(synced_ids)}/{pending} buffered detections"
                        )

                    # Periodically purge old synced entries
                    self._buffer.purge_synced()

                await asyncio.sleep(self._sync_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"DetectionSyncer error: {e}")
                await asyncio.sleep(self._sync_interval * 2)

    def stop(self) -> None:
        self._running = False
