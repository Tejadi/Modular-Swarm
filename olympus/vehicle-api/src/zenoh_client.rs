use std::collections::HashMap;
use std::sync::Arc;

use chrono::Utc;
use tokio::sync::{broadcast, RwLock};
use tracing::{debug, error, info, warn};
use zenoh::prelude::r#async::*;

use crate::config::AppConfig;
use crate::models::{
    DetectionRecord, MissionState, Position, TaskRecord, TelemetryEvent, VehicleRecord,
};

pub struct ZenohClient {
    session: Arc<Session>,
    pub telemetry_tx: broadcast::Sender<TelemetryEvent>,
}

impl ZenohClient {
    pub async fn connect(config: &AppConfig) -> Result<Self, zenoh::Error> {
        let mut zenoh_config = zenoh::config::Config::default();

        let endpoints: Vec<EndPoint> = config
            .zenoh_endpoints
            .iter()
            .filter_map(|ep| ep.parse().ok())
            .collect();

        if !endpoints.is_empty() {
            // zenoh 0.11.x: connect.endpoints is a Vec<EndPoint>, assign directly.
            zenoh_config.connect.endpoints = endpoints;
        }

        info!(
            "Connecting to Zenoh at {:?}...",
            config.zenoh_endpoints
        );

        let session = zenoh::open(zenoh_config).res().await?;
        let session = Arc::new(session);

        let (telemetry_tx, _) = broadcast::channel(config.ws_broadcast_capacity);

        info!("Zenoh session established");

        Ok(Self {
            session,
            telemetry_tx,
        })
    }

    pub async fn try_connect(config: &AppConfig) -> Option<Self> {
        match Self::connect(config).await {
            Ok(client) => Some(client),
            Err(e) => {
                warn!("Failed to connect to Zenoh: {e}. Running in degraded mode.");
                None
            }
        }
    }

    pub async fn connect_peer_mode(config: &AppConfig) -> Result<Self, zenoh::Error> {
        let mut zenoh_config = zenoh::config::Config::default();
        // zenoh 0.11.x: set_mode returns the previous Option<WhatAmI>, not a Result.
        zenoh_config.set_mode(Some(zenoh::config::WhatAmI::Peer));

        info!("Opening Zenoh session in peer mode (no router)...");

        let session = zenoh::open(zenoh_config).res().await?;
        let session = Arc::new(session);

        let (telemetry_tx, _) = broadcast::channel(config.ws_broadcast_capacity);

        info!("Zenoh peer-mode session established (degraded -- no router)");

        Ok(Self {
            session,
            telemetry_tx,
        })
    }

    pub fn is_connected(&self) -> bool {
        true
    }

    pub fn subscribe_telemetry(&self) -> broadcast::Receiver<TelemetryEvent> {
        self.telemetry_tx.subscribe()
    }

    pub async fn publish_command(
        &self,
        vehicle_id: &str,
        command: &serde_json::Value,
    ) -> Result<(), String> {
        let key = format!("olympus/command/{vehicle_id}");
        let payload = serde_json::to_string(command).map_err(|e| e.to_string())?;
        self.session
            .put(&key, payload)
            .res()
            .await
            .map_err(|e| format!("Zenoh put failed: {e}"))?;
        debug!("Published command to {key}");
        Ok(())
    }

    pub async fn publish_telemetry(
        &self,
        vehicle_id: &str,
        data: &serde_json::Value,
    ) -> Result<(), String> {
        let key = format!("olympus/swarm/{vehicle_id}/telemetry");
        let payload = serde_json::to_string(data).map_err(|e| e.to_string())?;
        self.session
            .put(&key, payload)
            .res()
            .await
            .map_err(|e| format!("Zenoh put failed: {e}"))?;
        debug!("Published partner telemetry to {key}");
        Ok(())
    }

    pub async fn publish_raw(
        &self,
        key: &str,
        data: &serde_json::Value,
    ) -> Result<(), String> {
        let payload = serde_json::to_string(data).map_err(|e| e.to_string())?;
        self.session
            .put(key, payload)
            .res()
            .await
            .map_err(|e| format!("Zenoh put failed: {e}"))?;
        debug!("Published to {key}");
        Ok(())
    }

    pub async fn publish_global_command(
        &self,
        command: &serde_json::Value,
    ) -> Result<(), String> {
        let key = "olympus/command/broadcast";
        let payload = serde_json::to_string(command).map_err(|e| e.to_string())?;
        self.session
            .put(key, payload)
            .res()
            .await
            .map_err(|e| format!("Zenoh put failed: {e}"))?;
        debug!("Published global command to {key}");
        Ok(())
    }

    pub async fn spawn_subscribers(
        self: &Arc<Self>,
        vehicles: Arc<RwLock<HashMap<String, VehicleRecord>>>,
        detections: Arc<RwLock<HashMap<String, DetectionRecord>>>,
        tasks: Arc<RwLock<HashMap<String, TaskRecord>>>,
        _mission: Arc<RwLock<MissionState>>,
    ) {
        {
            let session = self.session.clone();
            let vehicles = vehicles.clone();
            let tx = self.telemetry_tx.clone();

            tokio::spawn(async move {
                let subscriber = match session
                    .declare_subscriber("olympus/swarm/*/telemetry")
                    .res()
                    .await
                {
                    Ok(sub) => sub,
                    Err(e) => {
                        error!("Failed to subscribe to telemetry: {e}");
                        return;
                    }
                };

                info!("Subscribed to olympus/swarm/*/telemetry");

                while let Ok(sample) = subscriber.recv_async().await {
                    let payload = match std::str::from_utf8(&sample.value.payload.contiguous()) {
                        Ok(s) => s.to_string(),
                        Err(_) => {
                            warn!("Non-UTF8 telemetry payload, skipping");
                            continue;
                        }
                    };

                    let json_val: serde_json::Value = match serde_json::from_str(&payload) {
                        Ok(v) => v,
                        Err(e) => {
                            warn!("Failed to parse telemetry JSON: {e}");
                            continue;
                        }
                    };

                    let drone_id = json_val
                        .get("drone_id")
                        .and_then(|v| v.as_str())
                        .map(|s| s.to_string())
                        .unwrap_or_else(|| {
                            let key = sample.key_expr.as_str();
                            key.split('/')
                                .nth(2)
                                .unwrap_or("unknown")
                                .to_string()
                        });

                    {
                        let mut map = vehicles.write().await;
                        let record = map.entry(drone_id.clone()).or_insert_with(|| VehicleRecord::new(drone_id.clone()));

                        // Update telemetry fields, preserve trust/registration fields
                        record.role = json_val
                            .get("role")
                            .and_then(|v| v.as_str())
                            .unwrap_or("scout")
                            .to_string();
                        record.status = json_val
                            .get("status")
                            .and_then(|v| v.as_str())
                            .unwrap_or("idle")
                            .to_string();
                        record.position = Position {
                            latitude: json_val
                                .pointer("/position/latitude")
                                .and_then(|v| v.as_f64())
                                .unwrap_or(0.0),
                            longitude: json_val
                                .pointer("/position/longitude")
                                .and_then(|v| v.as_f64())
                                .unwrap_or(0.0),
                            altitude: json_val
                                .pointer("/position/altitude")
                                .and_then(|v| v.as_f64())
                                .unwrap_or(0.0),
                            heading: json_val
                                .pointer("/position/heading")
                                .and_then(|v| v.as_f64())
                                .unwrap_or(0.0)
                                as f32,
                        };
                        record.battery_pct = json_val
                            .pointer("/battery/percentage")
                            .and_then(|v| v.as_u64())
                            .unwrap_or(0) as u8;
                        record.signal_rssi = json_val
                            .get("mesh_rssi")
                            .and_then(|v| v.as_i64())
                            .unwrap_or(0) as i16;
                        record.current_task = json_val
                            .get("current_task_id")
                            .and_then(|v| v.as_str())
                            .map(|s| s.to_string());
                        record.last_seen = Utc::now();
                        record.tank_level = json_val
                            .get("tank_level")
                            .and_then(|v| v.as_f64())
                            .map(|v| v as f32);
                        // Only update capabilities if provided in telemetry
                        if let Some(caps) = json_val
                            .get("capabilities")
                            .and_then(|v| v.as_array())
                        {
                            record.capabilities = caps.iter()
                                .filter_map(|v| v.as_str().map(|s| s.to_string()))
                                .collect();
                        }
                    }

                    let event = TelemetryEvent {
                        event: "telemetry".to_string(),
                        vehicle_id: drone_id,
                        data: json_val,
                        timestamp: Utc::now(),
                    };
                    let _ = tx.send(event);
                }
            });
        }

        {
            let session = self.session.clone();
            let detections = detections.clone();

            tokio::spawn(async move {
                let subscriber = match session
                    .declare_subscriber("olympus/detection/**")
                    .res()
                    .await
                {
                    Ok(sub) => sub,
                    Err(e) => {
                        error!("Failed to subscribe to detections: {e}");
                        return;
                    }
                };

                info!("Subscribed to olympus/detection/**");

                while let Ok(sample) = subscriber.recv_async().await {
                    let payload = match std::str::from_utf8(&sample.value.payload.contiguous()) {
                        Ok(s) => s.to_string(),
                        Err(_) => continue,
                    };

                    let json_val: serde_json::Value = match serde_json::from_str(&payload) {
                        Ok(v) => v,
                        Err(e) => {
                            warn!("Failed to parse detection JSON: {e}");
                            continue;
                        }
                    };

                    let det_id = json_val
                        .get("id")
                        .and_then(|v| v.as_str())
                        .unwrap_or_else(|| "unknown")
                        .to_string();

                    let record = DetectionRecord {
                        id: det_id.clone(),
                        detection_type: json_val
                            .get("detection_type")
                            .and_then(|v| v.as_str())
                            .unwrap_or("unknown")
                            .to_string(),
                        position: Position {
                            latitude: json_val
                                .pointer("/position/latitude")
                                .and_then(|v| v.as_f64())
                                .unwrap_or(0.0),
                            longitude: json_val
                                .pointer("/position/longitude")
                                .and_then(|v| v.as_f64())
                                .unwrap_or(0.0),
                            altitude: json_val
                                .pointer("/position/altitude")
                                .and_then(|v| v.as_f64())
                                .unwrap_or(0.0),
                            heading: 0.0,
                        },
                        confidence: json_val
                            .get("confidence")
                            .and_then(|v| v.as_f64())
                            .unwrap_or(0.0) as f32,
                        timestamp: Utc::now(),
                        status: json_val
                            .get("status")
                            .and_then(|v| v.as_str())
                            .unwrap_or("new")
                            .to_string(),
                        detected_by: json_val
                            .get("detected_by")
                            .and_then(|v| v.as_str())
                            .unwrap_or("unknown")
                            .to_string(),
                        assigned_to: json_val
                            .get("assigned_to")
                            .and_then(|v| v.as_str())
                            .map(|s| s.to_string()),
                    };

                    let mut map = detections.write().await;

                    // Cap detection store at 10,000 entries to prevent OOM.
                    // Evict oldest entries by timestamp when at capacity.
                    const MAX_DETECTIONS: usize = 10_000;
                    if map.len() >= MAX_DETECTIONS {
                        // Find and remove the oldest entry
                        if let Some(oldest_id) = map
                            .iter()
                            .min_by_key(|(_, r)| r.timestamp)
                            .map(|(id, _)| id.clone())
                        {
                            map.remove(&oldest_id);
                        }
                    }

                    map.insert(det_id, record);
                }
            });
        }

        {
            let session = self.session.clone();
            let tasks = tasks;

            tokio::spawn(async move {
                let subscriber = match session
                    .declare_subscriber("olympus/task/**")
                    .res()
                    .await
                {
                    Ok(sub) => sub,
                    Err(e) => {
                        error!("Failed to subscribe to tasks: {e}");
                        return;
                    }
                };

                info!("Subscribed to olympus/task/**");

                while let Ok(sample) = subscriber.recv_async().await {
                    let payload = match std::str::from_utf8(&sample.value.payload.contiguous()) {
                        Ok(s) => s.to_string(),
                        Err(_) => continue,
                    };

                    let json_val: serde_json::Value = match serde_json::from_str(&payload) {
                        Ok(v) => v,
                        Err(e) => {
                            warn!("Failed to parse task JSON: {e}");
                            continue;
                        }
                    };

                    let task_id = json_val
                        .get("id")
                        .and_then(|v| v.as_str())
                        .unwrap_or("unknown")
                        .to_string();

                    let record = TaskRecord {
                        id: task_id.clone(),
                        task_type: json_val
                            .get("task_type")
                            .and_then(|v| v.as_str())
                            .unwrap_or("unknown")
                            .to_string(),
                        target_position: Position {
                            latitude: json_val
                                .pointer("/target_position/latitude")
                                .and_then(|v| v.as_f64())
                                .unwrap_or(0.0),
                            longitude: json_val
                                .pointer("/target_position/longitude")
                                .and_then(|v| v.as_f64())
                                .unwrap_or(0.0),
                            altitude: json_val
                                .pointer("/target_position/altitude")
                                .and_then(|v| v.as_f64())
                                .unwrap_or(0.0),
                            heading: 0.0,
                        },
                        status: json_val
                            .get("state")
                            .or_else(|| json_val.get("status"))
                            .and_then(|v| v.as_str())
                            .unwrap_or("pending")
                            .to_string(),
                        assigned_to: json_val
                            .get("assigned_to")
                            .and_then(|v| v.as_str())
                            .map(|s| s.to_string()),
                        priority: json_val
                            .get("priority")
                            .and_then(|v| v.as_u64())
                            .unwrap_or(5) as u8,
                    };

                    let mut map = tasks.write().await;
                    map.insert(task_id, record);
                }
            });
        }
    }
}
