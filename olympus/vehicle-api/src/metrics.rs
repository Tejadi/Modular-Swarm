use std::path::Path;
use std::sync::Arc;

use chrono::{DateTime, Utc};
use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use tokio::sync::Mutex;
use tracing::{error, info};

use crate::models::Position;

#[derive(Debug, Serialize, Deserialize)]
pub struct TelemetryLogEntry {
    pub vehicle_id: String,
    pub timestamp: DateTime<Utc>,
    pub latitude: f64,
    pub longitude: f64,
    pub altitude: f64,
    pub battery_pct: u8,
    pub status: String,
    pub partner_id: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct DetectionLogEntry {
    pub id: String,
    pub detection_type: String,
    pub position: Position,
    pub confidence: f32,
    pub timestamp: DateTime<Utc>,
    pub detected_by: String,
    pub status: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct CommandLogEntry {
    pub command_id: String,
    pub vehicle_id: String,
    pub command: String,
    pub partner_id: String,
    pub timestamp: DateTime<Utc>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct MissionLogEntry {
    pub phase: String,
    pub start_time: Option<DateTime<Utc>>,
    pub end_time: Option<DateTime<Utc>>,
    pub vehicle_count: u32,
    pub task_count: u32,
    pub detection_count: u32,
}

#[derive(Debug, Serialize)]
pub struct DetectionSummary {
    pub detection_type: String,
    pub count: u64,
}

pub struct MetricsDb {
    conn: Arc<Mutex<Connection>>,
}

impl MetricsDb {
    pub fn open(path: &str) -> Result<Self, rusqlite::Error> {
        let needs_init = !Path::new(path).exists();
        let conn = Connection::open(path)?;
        conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")?;

        let db = Self {
            conn: Arc::new(Mutex::new(conn)),
        };

        if needs_init {
            db.init_sync()?;
        }

        info!("Metrics database opened at {path}");
        Ok(db)
    }

    fn init_sync(&self) -> Result<(), rusqlite::Error> {
        let conn = self.conn.try_lock().expect("init called before any async");
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS telemetry_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vehicle_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                altitude REAL NOT NULL,
                battery_pct INTEGER NOT NULL,
                status TEXT NOT NULL,
                partner_id TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_telem_vehicle ON telemetry_log(vehicle_id);
            CREATE INDEX IF NOT EXISTS idx_telem_ts ON telemetry_log(timestamp);

            CREATE TABLE IF NOT EXISTS detection_log (
                id TEXT PRIMARY KEY,
                detection_type TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                altitude REAL NOT NULL,
                confidence REAL NOT NULL,
                timestamp TEXT NOT NULL,
                detected_by TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_det_type ON detection_log(detection_type);

            CREATE TABLE IF NOT EXISTS command_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                command_id TEXT NOT NULL,
                vehicle_id TEXT NOT NULL,
                command TEXT NOT NULL,
                partner_id TEXT NOT NULL,
                timestamp TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cmd_partner ON command_log(partner_id);

            CREATE TABLE IF NOT EXISTS mission_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phase TEXT NOT NULL,
                start_time TEXT,
                end_time TEXT,
                vehicle_count INTEGER NOT NULL DEFAULT 0,
                task_count INTEGER NOT NULL DEFAULT 0,
                detection_count INTEGER NOT NULL DEFAULT 0
            );",
        )?;
        Ok(())
    }

    pub async fn log_telemetry(&self, entry: &TelemetryLogEntry) {
        let conn = self.conn.lock().await;
        if let Err(e) = conn.execute(
            "INSERT INTO telemetry_log (vehicle_id, timestamp, latitude, longitude, altitude, battery_pct, status, partner_id) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                entry.vehicle_id,
                entry.timestamp.to_rfc3339(),
                entry.latitude,
                entry.longitude,
                entry.altitude,
                entry.battery_pct,
                entry.status,
                entry.partner_id,
            ],
        ) {
            error!("Failed to log telemetry: {e}");
        }
    }

    pub async fn log_detection(&self, entry: &DetectionLogEntry) {
        let conn = self.conn.lock().await;
        if let Err(e) = conn.execute(
            "INSERT OR REPLACE INTO detection_log (id, detection_type, latitude, longitude, altitude, confidence, timestamp, detected_by, status) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![
                entry.id,
                entry.detection_type,
                entry.position.latitude,
                entry.position.longitude,
                entry.position.altitude,
                entry.confidence,
                entry.timestamp.to_rfc3339(),
                entry.detected_by,
                entry.status,
            ],
        ) {
            error!("Failed to log detection: {e}");
        }
    }

    pub async fn log_command(&self, entry: &CommandLogEntry) {
        let conn = self.conn.lock().await;
        if let Err(e) = conn.execute(
            "INSERT INTO command_log (command_id, vehicle_id, command, partner_id, timestamp) VALUES (?1, ?2, ?3, ?4, ?5)",
            params![
                entry.command_id,
                entry.vehicle_id,
                entry.command,
                entry.partner_id,
                entry.timestamp.to_rfc3339(),
            ],
        ) {
            error!("Failed to log command: {e}");
        }
    }

    pub async fn query_telemetry(
        &self,
        vehicle_id: Option<&str>,
        from: Option<DateTime<Utc>>,
        to: Option<DateTime<Utc>>,
        limit: u32,
    ) -> Vec<TelemetryLogEntry> {
        let conn = self.conn.lock().await;
        let mut sql = "SELECT vehicle_id, timestamp, latitude, longitude, altitude, battery_pct, status, partner_id FROM telemetry_log WHERE 1=1".to_string();
        let mut bind_values: Vec<String> = Vec::new();

        if let Some(vid) = vehicle_id {
            sql.push_str(&format!(" AND vehicle_id = '{}'", vid.replace('\'', "''")));
        }
        if let Some(f) = from {
            bind_values.push(f.to_rfc3339());
            sql.push_str(&format!(" AND timestamp >= '{}'", bind_values.last().unwrap()));
        }
        if let Some(t) = to {
            bind_values.push(t.to_rfc3339());
            sql.push_str(&format!(" AND timestamp <= '{}'", bind_values.last().unwrap()));
        }
        sql.push_str(&format!(" ORDER BY timestamp DESC LIMIT {limit}"));

        let mut stmt = match conn.prepare(&sql) {
            Ok(s) => s,
            Err(e) => {
                error!("Failed to query telemetry: {e}");
                return Vec::new();
            }
        };

        let rows = stmt
            .query_map([], |row| {
                let ts_str: String = row.get(1)?;
                let ts = DateTime::parse_from_rfc3339(&ts_str)
                    .map(|d| d.with_timezone(&Utc))
                    .unwrap_or_else(|_| Utc::now());
                Ok(TelemetryLogEntry {
                    vehicle_id: row.get(0)?,
                    timestamp: ts,
                    latitude: row.get(2)?,
                    longitude: row.get(3)?,
                    altitude: row.get(4)?,
                    battery_pct: row.get::<_, u8>(5)?,
                    status: row.get(6)?,
                    partner_id: row.get(7)?,
                })
            })
            .ok();

        match rows {
            Some(iter) => iter.filter_map(|r| r.ok()).collect(),
            None => Vec::new(),
        }
    }

    pub async fn detection_summary(&self) -> Vec<DetectionSummary> {
        let conn = self.conn.lock().await;
        let mut stmt = match conn.prepare(
            "SELECT detection_type, COUNT(*) as cnt FROM detection_log GROUP BY detection_type ORDER BY cnt DESC",
        ) {
            Ok(s) => s,
            Err(e) => {
                error!("Failed to query detection summary: {e}");
                return Vec::new();
            }
        };

        stmt.query_map([], |row| {
            Ok(DetectionSummary {
                detection_type: row.get(0)?,
                count: row.get(1)?,
            })
        })
        .ok()
        .map(|iter| iter.filter_map(|r| r.ok()).collect())
        .unwrap_or_default()
    }

    pub async fn prune_older_than_days(&self, days: i64) {
        let cutoff = Utc::now() - chrono::Duration::days(days);
        let cutoff_str = cutoff.to_rfc3339();
        let conn = self.conn.lock().await;

        for table in &["telemetry_log", "command_log"] {
            if let Err(e) = conn.execute(
                &format!("DELETE FROM {table} WHERE timestamp < ?1"),
                params![cutoff_str],
            ) {
                error!("Failed to prune {table}: {e}");
            }
        }
    }
}

impl Clone for MetricsDb {
    fn clone(&self) -> Self {
        Self {
            conn: self.conn.clone(),
        }
    }
}
