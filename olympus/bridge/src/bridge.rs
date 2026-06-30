use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::{mpsc, RwLock};
use tokio::time::{interval, Duration};
use tracing::{info, warn, error, debug};

use crate::config::BridgeConfig;
use crate::mavlink_handler::{MavlinkCommand, MavlinkEvent, MavlinkHandler, MavlinkTelemetry, TunnelDetection};
use crate::protocol::{
    DroneTelemetry, DroneStatus, DroneRole, GeoPosition, BatteryState,
    CommandMessage, DroneCommand, Detection, Task, TaskBid,
    SwarmNetStatus,
    keys,
};
use crate::zenoh_handler::{ZenohHandler, encode_msgpack};
use crate::lora::LoRaBridge;
use crate::elrs::{ElrsBridge, ElrsLinkStats, ElrsGps, CrsfFrame};

#[derive(Debug, Default)]
pub struct BridgeState {
    pub telemetry: DroneTelemetry,
    pub swarm_positions: HashMap<String, SwarmMember>,
    pub pending_tasks: HashMap<String, Task>,
    pub active_detections: HashMap<String, Detection>,
    pub wifi_connected: bool,
    pub lora_connected: bool,
    pub elrs_connected: bool,
    pub elrs_link_quality: u8,
    pub elrs_rssi: i8,
    pub last_command: Option<CommandMessage>,
    pub base_station_last_seen: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone)]
pub struct SwarmMember {
    pub drone_id: String,
    pub role: DroneRole,
    pub status: DroneStatus,
    pub position: GeoPosition,
    pub battery_percent: u8,
    pub last_seen: DateTime<Utc>,
    pub via_lora: bool,
}

impl From<&DroneTelemetry> for SwarmMember {
    fn from(t: &DroneTelemetry) -> Self {
        Self {
            drone_id: t.drone_id.clone(),
            role: t.role,
            status: t.status,
            position: t.position.clone(),
            battery_percent: t.battery.percentage,
            last_seen: t.timestamp,
            via_lora: false,
        }
    }
}

#[derive(Debug)]
pub enum BridgeMessage {
    TelemetryUpdate(DroneTelemetry),
    CommandReceived(CommandMessage),
    DetectionEvent(Detection),
    TaskReceived(Task),
    SwarmUpdate(DroneTelemetry),
    LoRaPacket(Vec<u8>, u32),
    ElrsPacket(Vec<u8>, u8),          // payload, crsf_frame_type
    ElrsLinkStats(ElrsLinkStats),
    ElrsGps(ElrsGps),
    MavlinkTelemetry(MavlinkTelemetry),
    MavlinkTunnelDetection(TunnelDetection),
    SwarmNetStatusUpdate(SwarmNetStatus),
    LoRaFeatures(Vec<u8>, u32),       // feature_data, sender_node_id
    Shutdown,
}

pub struct OlympusBridge {
    config: BridgeConfig,
    state: Arc<RwLock<BridgeState>>,
    zenoh: Option<ZenohHandler>,
    lora: Option<LoRaBridge>,
    elrs: Option<ElrsBridge>,
    mavlink_cmd_tx: Option<mpsc::Sender<MavlinkCommand>>,
    mavlink_event_rx: Option<mpsc::Receiver<MavlinkEvent>>,
    message_tx: mpsc::Sender<BridgeMessage>,
    message_rx: mpsc::Receiver<BridgeMessage>,
}

impl OlympusBridge {
    pub async fn new(
        config: BridgeConfig,
        state: Arc<RwLock<BridgeState>>,
    ) -> Result<Self> {
        {
            let mut s = state.write().await;
            s.telemetry = DroneTelemetry::new(config.drone_id.clone(), config.role);
        }

        let (message_tx, message_rx) = mpsc::channel(256);

        let zenoh = ZenohHandler::new(&config.zenoh, message_tx.clone()).await
            .context("Failed to initialize Zenoh")?;
        info!("Zenoh handler initialized");

        let lora = if config.lora.enabled {
            match LoRaBridge::new(&config.lora, message_tx.clone()).await {
                Ok(l) => {
                    info!("LoRa bridge initialized on {}", config.lora.serial_port);
                    Some(l)
                }
                Err(e) => {
                    warn!("LoRa bridge failed to initialize: {}. Continuing without LoRa.", e);
                    None
                }
            }
        } else {
            info!("LoRa bridge disabled in config");
            None
        };

        let elrs = if config.elrs.enabled {
            match ElrsBridge::new(&config.elrs, message_tx.clone()).await {
                Ok(e) => {
                    info!("ELRS bridge initialized on {} (CRSF @ {} baud)",
                        config.elrs.serial_port, config.elrs.baud_rate);
                    Some(e)
                }
                Err(e) => {
                    warn!("ELRS bridge failed to initialize: {}. Continuing without ELRS.", e);
                    None
                }
            }
        } else {
            info!("ELRS bridge disabled in config");
            None
        };

        let (mavlink_cmd_tx, mavlink_event_rx) = if config.mavlink.enabled {
            let (handler, cmd_tx, event_rx) = MavlinkHandler::new(config.mavlink.clone());
            let _mavlink_handle = handler.start();
            info!("MAVLink handler spawned for {}", config.mavlink.connection_string);
            (Some(cmd_tx), Some(event_rx))
        } else {
            info!("MAVLink handler disabled in config");
            (None, None)
        };

        Ok(Self {
            config,
            state,
            zenoh: Some(zenoh),
            lora,
            elrs,
            mavlink_cmd_tx,
            mavlink_event_rx,
            message_tx,
            message_rx,
        })
    }

    pub async fn run(mut self) -> Result<()> {
        info!("Starting bridge event loop");

        let state_clone = self.state.clone();
        let config_clone = self.config.clone();
        let tx_clone = self.message_tx.clone();
        let telemetry_handle = tokio::spawn(async move {
            Self::telemetry_publisher_task(state_clone, config_clone, tx_clone).await
        });

        let state_clone2 = self.state.clone();
        let config_clone2 = self.config.clone();
        let lora_tx = self.lora.as_ref().map(|l| l.get_tx_channel());
        let heartbeat_handle = if let Some(tx) = lora_tx {
            Some(tokio::spawn(async move {
                Self::lora_heartbeat_task(state_clone2, config_clone2, tx).await
            }))
        } else {
            None
        };

        let state_clone_elrs = self.state.clone();
        let config_clone_elrs = self.config.clone();
        let elrs_tx = self.elrs.as_ref().map(|e| e.get_tx_channel());
        let elrs_heartbeat_handle = if let Some(tx) = elrs_tx {
            Some(tokio::spawn(async move {
                Self::elrs_heartbeat_task(state_clone_elrs, config_clone_elrs, tx).await
            }))
        } else {
            None
        };

        let state_clone3 = self.state.clone();
        let config_clone3 = self.config.clone();
        let timeout_handle = tokio::spawn(async move {
            Self::peer_timeout_task(state_clone3, config_clone3).await
        });

        let mavlink_forward_handle = if let Some(mut event_rx) = self.mavlink_event_rx.take() {
            let bridge_tx = self.message_tx.clone();
            Some(tokio::spawn(async move {
                while let Some(event) = event_rx.recv().await {
                    match event {
                        MavlinkEvent::TelemetryUpdate(telem) => {
                            let _ = bridge_tx
                                .send(BridgeMessage::MavlinkTelemetry(telem))
                                .await;
                        }
                        MavlinkEvent::TunnelDetection(det) => {
                            let _ = bridge_tx
                                .send(BridgeMessage::MavlinkTunnelDetection(det))
                                .await;
                        }
                        MavlinkEvent::Alert(alert) => {
                            info!("MAVLink alert [sev={}]: {}", alert.severity, alert.text);
                        }
                        MavlinkEvent::ConnectionState(connected) => {
                            if connected {
                                info!("MAVLink: connected to flight controller");
                            } else {
                                warn!("MAVLink: disconnected from flight controller");
                            }
                        }
                    }
                }
            }))
        } else {
            None
        };

        loop {
            tokio::select! {
                Some(msg) = self.message_rx.recv() => {
                    match msg {
                        BridgeMessage::Shutdown => {
                            info!("Shutdown signal received");
                            break;
                        }
                        _ => {
                            if let Err(e) = self.handle_message(msg).await {
                                error!("Error handling message: {}", e);
                            }
                        }
                    }
                }
                _ = tokio::signal::ctrl_c() => {
                    info!("Ctrl+C received, shutting down");
                    break;
                }
            }
        }

        telemetry_handle.abort();
        timeout_handle.abort();
        if let Some(h) = heartbeat_handle {
            h.abort();
        }
        if let Some(h) = elrs_heartbeat_handle {
            h.abort();
        }
        if let Some(h) = mavlink_forward_handle {
            h.abort();
        }

        Ok(())
    }

    async fn handle_message(&mut self, msg: BridgeMessage) -> Result<()> {
        match msg {
            BridgeMessage::TelemetryUpdate(telem) => {
                self.handle_telemetry_update(telem).await?;
            }
            BridgeMessage::CommandReceived(cmd) => {
                self.handle_command(cmd).await?;
            }
            BridgeMessage::DetectionEvent(detection) => {
                self.handle_detection(detection).await?;
            }
            BridgeMessage::TaskReceived(task) => {
                self.handle_task(task).await?;
            }
            BridgeMessage::SwarmUpdate(telem) => {
                self.handle_swarm_update(telem).await?;
            }
            BridgeMessage::LoRaPacket(payload, sender) => {
                self.handle_lora_packet(payload, sender).await?;
            }
            BridgeMessage::ElrsPacket(payload, frame_type) => {
                self.handle_elrs_packet(payload, frame_type).await?;
            }
            BridgeMessage::ElrsLinkStats(stats) => {
                self.handle_elrs_link_stats(stats).await?;
            }
            BridgeMessage::ElrsGps(gps) => {
                self.handle_elrs_gps(gps).await?;
            }
            BridgeMessage::MavlinkTelemetry(mav_telem) => {
                self.handle_mavlink_telemetry(mav_telem).await?;
            }
            BridgeMessage::MavlinkTunnelDetection(tunnel_det) => {
                self.handle_tunnel_detection(tunnel_det).await?;
            }
            BridgeMessage::SwarmNetStatusUpdate(status) => {
                self.handle_swarmnet_status(status).await?;
            }
            BridgeMessage::LoRaFeatures(data, sender) => {
                self.handle_lora_features(data, sender).await?;
            }
            BridgeMessage::Shutdown => unreachable!(),
        }
        Ok(())
    }

    async fn handle_telemetry_update(&mut self, telem: DroneTelemetry) -> Result<()> {
        {
            let mut state = self.state.write().await;
            state.telemetry = telem.clone();
        }

        if let Some(ref zenoh) = self.zenoh {
            let key = keys::telemetry(&self.config.drone_id);
            let payload = encode_msgpack(&telem);
            zenoh.publish(&key, payload).await?;

            if let Some(ref features) = telem.intermediate_features {
                if !features.is_empty() {
                    let feat_key = keys::features(&self.config.drone_id);
                    zenoh.publish(&feat_key, features.clone()).await?;
                }
            }
        }

        debug!("Published telemetry for {}", self.config.drone_id);
        Ok(())
    }

    async fn handle_command(&mut self, cmd: CommandMessage) -> Result<()> {
        info!("Received command: {:?}", cmd.command);

        if cmd.target_drone != "*" && cmd.target_drone != self.config.drone_id {
            return Ok(());
        }

        {
            let mut state = self.state.write().await;
            state.last_command = Some(cmd.clone());
        }

        if let Some(ref mavlink_tx) = self.mavlink_cmd_tx {
            if let Some(mav_cmd) = MavlinkCommand::from_drone_command(&cmd.command) {
                if let Err(e) = mavlink_tx.send(mav_cmd).await {
                    error!("Failed to forward command to MAVLink handler: {}", e);
                }
            }
        }

        match cmd.command {
            DroneCommand::EmergencyStop => {
                error!("EMERGENCY STOP RECEIVED – forwarded to flight controller");
            }
            DroneCommand::ReturnToLaunch => {
                info!("RTL command received – forwarded to flight controller");
            }
            DroneCommand::GoTo { ref position } => {
                info!("GoTo command: {:?} – forwarded to flight controller", position);
            }
            DroneCommand::Pause => {
                info!("Pause command – forwarded to flight controller");
            }
            DroneCommand::Resume => {
                info!("Resume command – forwarded to flight controller");
            }
            DroneCommand::SetAltitude { altitude } => {
                info!("SetAltitude {:.1}m – forwarded to flight controller", altitude);
            }
            DroneCommand::RecallForUpdate => {
                warn!("RECALL FOR UPDATE — pausing operations for model update");
            }
            DroneCommand::Redeploy => {
                info!("REDEPLOY — resuming operations with updated model");
            }
            DroneCommand::UpdateModel { version } => {
                info!("UPDATE MODEL v{} — forwarded to local SwarmNet", version);
            }
            _ => {
                debug!("Unhandled command type");
            }
        }

        if let Some(ref zenoh) = self.zenoh {
            let ack = CommandMessage::new(
                "base_station".to_string(),
                DroneCommand::Ack { command_id: cmd.id.clone() },
                self.config.drone_id.clone(),
            );
            let key = keys::command("base_station");
            let payload = encode_msgpack(&ack);
            zenoh.publish(&key, payload).await?;
        }

        Ok(())
    }

    async fn handle_detection(&mut self, detection: Detection) -> Result<()> {
        info!(
            "Detection: {:?} at ({:.6}, {:.6}) conf={:.2}",
            detection.detection_type,
            detection.position.latitude,
            detection.position.longitude,
            detection.confidence
        );

        {
            let mut state = self.state.write().await;
            state.active_detections.insert(detection.id.clone(), detection.clone());
        }

        if let Some(ref zenoh) = self.zenoh {
            let key = keys::detection(&self.config.drone_id);
            let payload = encode_msgpack(&detection);
            zenoh.publish(&key, payload).await?;
        }

        // Forward high-confidence detections over LoRa (port 0x44)
        if let Some(ref lora) = self.lora {
            if detection.confidence >= 0.7 {
                let compact = Self::encode_detection_compact(&detection);
                if compact.len() <= 200 {
                    let lora_tx = lora.get_tx_channel();
                    let packet = crate::lora::MeshPacket::broadcast(compact, 0x44);
                    let _ = lora_tx.send(packet.encode()).await;
                    debug!("Forwarded detection to LoRa mesh (port 0x44)");
                }
            }
        }

        Ok(())
    }

    /// Encode a Detection into compact binary for LoRa transmission (≤100 bytes).
    /// Same format as TUNNEL but without the 0x4F magic prefix.
    fn encode_detection_compact(detection: &Detection) -> Vec<u8> {
        use crate::protocol::DetectionType;

        let det_type_idx: u8 = match detection.detection_type {
            Some(DetectionType::Weed) => 0,
            Some(DetectionType::Pest) => 1,
            Some(DetectionType::Disease) => 2,
            Some(DetectionType::NutrientDeficiency) => 3,
            Some(DetectionType::IrrigationLeak) => 4,
            Some(DetectionType::CropStress) => 5,
            Some(DetectionType::Obstacle) => 6,
            Some(DetectionType::HostileActivity) => 7,
            Some(DetectionType::VehicleDetected) => 8,
            Some(DetectionType::PersonDetected) => 9,
            Some(DetectionType::IedSuspected) => 10,
            Some(DetectionType::StructuralChange) => 11,
            Some(DetectionType::StructuralCrack) => 12,
            Some(DetectionType::Corrosion) => 13,
            Some(DetectionType::ThermalAnomaly) => 14,
            Some(DetectionType::LeakDetected) => 15,
            Some(DetectionType::VegetationEncroachment) => 16,
            Some(DetectionType::SurfaceDeformation) => 17,
            Some(DetectionType::ThermalSignature) => 18,
            Some(DetectionType::DebrisField) => 19,
            Some(DetectionType::VehicleWreckage) => 20,
            Some(DetectionType::SignalDetected) => 21,
            None => 6, // default to Obstacle
        };

        let conf_u8 = (detection.confidence * 255.0) as u8;
        let lat_i32 = (detection.position.latitude * 1e7) as i32;
        let lon_i32 = (detection.position.longitude * 1e7) as i32;
        let alt_u16 = detection.position.altitude.max(0.0).min(65535.0) as u16;
        let detected_by = detection.detected_by.as_bytes();
        let id_len = detected_by.len().min(32) as u8;
        let ts_u32 = detection.timestamp.timestamp() as u32;

        let mut buf = Vec::with_capacity(19 + id_len as usize);
        buf.push(det_type_idx);
        buf.push(conf_u8);
        buf.push(detection.severity);
        buf.extend_from_slice(&lat_i32.to_le_bytes());
        buf.extend_from_slice(&lon_i32.to_le_bytes());
        buf.extend_from_slice(&alt_u16.to_le_bytes());
        buf.push(id_len);
        buf.extend_from_slice(&detected_by[..id_len as usize]);
        buf.extend_from_slice(&ts_u32.to_le_bytes());
        buf
    }

    async fn handle_task(&mut self, task: Task) -> Result<()> {
        info!("Task received: {} type={:?}", task.id, task.task_type);

        {
            let mut state = self.state.write().await;
            state.pending_tasks.insert(task.id.clone(), task.clone());
        }

        if self.config.role == DroneRole::Executor {
            self.consider_task_bid(&task).await?;
        }

        Ok(())
    }

    async fn consider_task_bid(&self, task: &Task) -> Result<()> {
        let state = self.state.read().await;

        let distance = state.telemetry.position.distance_to(&task.target_position);
        let battery_cost = if state.telemetry.battery.percentage < 20 { 1000.0 } else { 0.0 };

        let cost = distance + battery_cost;
        let eta = (distance / 10.0) as u32;

        let bid = TaskBid {
            task_id: task.id.clone(),
            bidder_id: self.config.drone_id.clone(),
            cost,
            eta_seconds: eta,
            battery_after: state.telemetry.battery.percentage.saturating_sub(5),
            timestamp: Utc::now(),
        };

        if let Some(ref zenoh) = self.zenoh {
            let key = keys::task_bid(&task.id);
            let payload = encode_msgpack(&bid);
            zenoh.publish(&key, payload).await?;
            info!("Submitted bid for task {}: cost={:.1}", task.id, cost);
        }

        Ok(())
    }

    async fn handle_swarm_update(&mut self, telem: DroneTelemetry) -> Result<()> {
        let member = SwarmMember::from(&telem);

        {
            let mut state = self.state.write().await;
            state.swarm_positions.insert(telem.drone_id.clone(), member);
        }

        debug!("Updated swarm member: {}", telem.drone_id);
        Ok(())
    }

    async fn handle_lora_packet(&mut self, payload: Vec<u8>, sender: u32) -> Result<()> {
        debug!("LoRa packet from node {}: {} bytes", sender, payload.len());

        // Try parsing as telemetry first (port 67 / TelemetryApp)
        if let Some(telem) = DroneTelemetry::from_compact_bytes(&payload) {
            let mut member = SwarmMember::from(&telem);
            member.via_lora = true;

            {
                let mut state = self.state.write().await;
                state.swarm_positions.insert(telem.drone_id.clone(), member);
            }

            if let Some(ref zenoh) = self.zenoh {
                let key = keys::lora_rx(sender);
                zenoh.publish(&key, payload).await?;
            }
        }

        Ok(())
    }

    /// Handle LoRa detection packets (port 0x44). Called from MeshPacket dispatcher.
    async fn handle_lora_detection(&mut self, payload: Vec<u8>, sender: u32) -> Result<()> {
        use crate::protocol::DetectionType;

        if payload.len() < 18 {
            debug!("LoRa detection packet too short: {} bytes", payload.len());
            return Ok(());
        }

        // Compact format without 0x4F magic: [det_type][conf][severity][lat:i32][lon:i32][alt:u16][id_len][id][ts:u32]
        let det_type_idx = payload[0];
        let confidence = payload[1] as f32 / 255.0;
        let severity = payload[2];
        let lat_i32 = i32::from_le_bytes(payload[3..7].try_into().unwrap_or_default());
        let lon_i32 = i32::from_le_bytes(payload[7..11].try_into().unwrap_or_default());
        let alt_u16 = u16::from_le_bytes(payload[11..13].try_into().unwrap_or_default());
        let id_len = payload[13] as usize;

        if 14 + id_len + 4 > payload.len() {
            return Ok(());
        }

        let detected_by = String::from_utf8_lossy(&payload[14..14 + id_len]).to_string();
        let ts_offset = 14 + id_len;
        let ts_u32 = u32::from_le_bytes(payload[ts_offset..ts_offset + 4].try_into().unwrap_or_default());

        let detection_type = match det_type_idx {
            0 => DetectionType::Weed, 1 => DetectionType::Pest, 2 => DetectionType::Disease,
            3 => DetectionType::NutrientDeficiency, 4 => DetectionType::IrrigationLeak,
            5 => DetectionType::CropStress, 6 => DetectionType::Obstacle,
            7 => DetectionType::HostileActivity, 8 => DetectionType::VehicleDetected,
            9 => DetectionType::PersonDetected, 10 => DetectionType::IedSuspected,
            11 => DetectionType::StructuralChange, 12 => DetectionType::StructuralCrack,
            13 => DetectionType::Corrosion, 14 => DetectionType::ThermalAnomaly,
            15 => DetectionType::LeakDetected, 16 => DetectionType::VegetationEncroachment,
            17 => DetectionType::SurfaceDeformation, 18 => DetectionType::ThermalSignature,
            19 => DetectionType::DebrisField, 20 => DetectionType::VehicleWreckage,
            21 => DetectionType::SignalDetected, _ => DetectionType::Obstacle,
        };

        let timestamp = DateTime::from_timestamp(ts_u32 as i64, 0).unwrap_or_else(|| Utc::now());

        let detection = Detection {
            id: uuid::Uuid::new_v4().to_string(),
            detection_type: Some(detection_type),
            position: GeoPosition {
                latitude: lat_i32 as f64 / 1e7,
                longitude: lon_i32 as f64 / 1e7,
                altitude: alt_u16 as f64,
                heading: 0.0,
            },
            confidence,
            severity,
            detected_by,
            timestamp,
            image_ref: None,
            metadata: None,
        };

        info!(
            "LoRa detection from node {}: {:?} conf={:.2}",
            sender, detection_type, confidence
        );

        {
            let mut state = self.state.write().await;
            state.active_detections.insert(detection.id.clone(), detection.clone());
        }

        if let Some(ref zenoh) = self.zenoh {
            let key = keys::detection(&detection.detected_by);
            let payload = encode_msgpack(&detection);
            zenoh.publish(&key, payload).await?;
        }

        Ok(())
    }

    async fn handle_mavlink_telemetry(&mut self, mav_telem: MavlinkTelemetry) -> Result<()> {
        {
            let mut state = self.state.write().await;
            state.telemetry.position = mav_telem.position;
            state.telemetry.battery = MavlinkHandler::to_battery_state(&mav_telem);
            state.telemetry.status = MavlinkHandler::to_drone_status(&mav_telem);
        }

        if let Some(ref zenoh) = self.zenoh {
            let key = format!("{}/swarm/{}/mavlink", keys::PREFIX, self.config.drone_id);
            let payload = encode_msgpack(&mav_telem);
            zenoh.publish(&key, payload).await?;
        }

        debug!("MAVLink telemetry processed: armed={} mode={}", mav_telem.armed, mav_telem.flight_mode);
        Ok(())
    }

    async fn handle_tunnel_detection(&mut self, det: TunnelDetection) -> Result<()> {
        use crate::protocol::DetectionType;

        let detection_type = match det.detection_type_idx {
            0 => DetectionType::Weed,
            1 => DetectionType::Pest,
            2 => DetectionType::Disease,
            3 => DetectionType::NutrientDeficiency,
            4 => DetectionType::IrrigationLeak,
            5 => DetectionType::CropStress,
            6 => DetectionType::Obstacle,
            7 => DetectionType::HostileActivity,
            8 => DetectionType::VehicleDetected,
            9 => DetectionType::PersonDetected,
            10 => DetectionType::IedSuspected,
            11 => DetectionType::StructuralChange,
            12 => DetectionType::StructuralCrack,
            13 => DetectionType::Corrosion,
            14 => DetectionType::ThermalAnomaly,
            15 => DetectionType::LeakDetected,
            16 => DetectionType::VegetationEncroachment,
            17 => DetectionType::SurfaceDeformation,
            18 => DetectionType::ThermalSignature,
            19 => DetectionType::DebrisField,
            20 => DetectionType::VehicleWreckage,
            21 => DetectionType::SignalDetected,
            _ => DetectionType::Obstacle,
        };

        let timestamp = DateTime::from_timestamp(det.timestamp_secs as i64, 0)
            .unwrap_or_else(|| Utc::now());

        let detection = Detection {
            id: uuid::Uuid::new_v4().to_string(),
            detection_type: Some(detection_type),
            position: GeoPosition {
                latitude: det.latitude,
                longitude: det.longitude,
                altitude: det.altitude,
                heading: 0.0,
            },
            confidence: det.confidence,
            severity: det.severity,
            detected_by: det.detected_by.clone(),
            timestamp,
            image_ref: None,
            metadata: None,
        };

        info!(
            "TUNNEL → Detection: {:?} at ({:.6}, {:.6}) conf={:.2} from {}",
            detection_type, det.latitude, det.longitude, det.confidence, det.detected_by
        );

        {
            let mut state = self.state.write().await;
            state.active_detections.insert(detection.id.clone(), detection.clone());
        }

        if let Some(ref zenoh) = self.zenoh {
            let key = keys::detection(&det.detected_by);
            let payload = encode_msgpack(&detection);
            zenoh.publish(&key, payload).await?;
        }

        Ok(())
    }

    async fn handle_swarmnet_status(&mut self, status: SwarmNetStatus) -> Result<()> {
        if let Some(ref zenoh) = self.zenoh {
            let key = keys::swarmnet_status();
            let payload = encode_msgpack(&status);
            zenoh.publish(&key, payload).await?;
        }
        debug!("Published SwarmNet status: {} active drones", status.active_drones);
        Ok(())
    }

    /// Handle intermediate features received over LoRa mesh (tactical layer, port 0x45).
    /// Publishes to Zenoh so the Python brain can pick them up for peer distillation.
    async fn handle_lora_features(&mut self, data: Vec<u8>, sender: u32) -> Result<()> {
        debug!("LoRa features from node {}: {} bytes", sender, data.len());

        if let Some(ref zenoh) = self.zenoh {
            // Use sender node ID as drone identifier for features key
            let key = keys::features(&format!("lora-{}", sender));
            zenoh.publish(&key, data).await?;
        }

        Ok(())
    }

    async fn handle_elrs_packet(&mut self, payload: Vec<u8>, frame_type: u8) -> Result<()> {
        debug!("ELRS packet: type=0x{:02X} len={}", frame_type, payload.len());

        // OLYMPUS custom telemetry frames carry compact drone telemetry
        if frame_type == 0x80 {
            if let Some(telem) = DroneTelemetry::from_compact_bytes(&payload) {
                let mut member = SwarmMember::from(&telem);
                member.via_lora = false; // via ELRS, not LoRa

                {
                    let mut state = self.state.write().await;
                    state.swarm_positions.insert(telem.drone_id.clone(), member);
                }

                if let Some(ref zenoh) = self.zenoh {
                    let key = keys::elrs_rx(&self.config.drone_id);
                    zenoh.publish(&key, payload).await?;
                }
            }
        }

        Ok(())
    }

    async fn handle_elrs_link_stats(&mut self, stats: ElrsLinkStats) -> Result<()> {
        {
            let mut state = self.state.write().await;
            state.elrs_connected = true;
            state.elrs_link_quality = stats.uplink_link_quality;
            state.elrs_rssi = stats.uplink_rssi_ant1;
        }

        if let Some(ref zenoh) = self.zenoh {
            let key = format!("{}/swarm/{}/elrs/link", keys::PREFIX, self.config.drone_id);
            let payload = serde_json::to_vec(&serde_json::json!({
                "rssi_ant1": stats.uplink_rssi_ant1,
                "rssi_ant2": stats.uplink_rssi_ant2,
                "link_quality": stats.uplink_link_quality,
                "snr": stats.uplink_snr,
                "rf_mode": stats.rf_mode,
                "tx_power": stats.uplink_tx_power,
            }))?;
            zenoh.publish(&key, payload).await?;
        }

        debug!(
            "ELRS link: RSSI={}dBm LQ={}%",
            stats.uplink_rssi_ant1, stats.uplink_link_quality
        );
        Ok(())
    }

    async fn handle_elrs_gps(&mut self, gps: ElrsGps) -> Result<()> {
        // Update position from ELRS GPS if we have satellites
        if gps.satellites >= 4 {
            let mut state = self.state.write().await;
            state.telemetry.position.latitude = gps.latitude;
            state.telemetry.position.longitude = gps.longitude;
            state.telemetry.position.altitude = gps.altitude as f64;
            state.telemetry.position.heading = gps.heading;
        }
        debug!(
            "ELRS GPS: ({:.6}, {:.6}) alt={:.1}m sats={}",
            gps.latitude, gps.longitude, gps.altitude, gps.satellites
        );
        Ok(())
    }

    async fn elrs_heartbeat_task(
        state: Arc<RwLock<BridgeState>>,
        config: BridgeConfig,
        elrs_tx: mpsc::Sender<CrsfFrame>,
    ) {
        let period = Duration::from_secs(config.elrs.heartbeat_interval_secs);
        let mut ticker = interval(period);

        loop {
            ticker.tick().await;

            let compact = {
                let s = state.read().await;
                s.telemetry.to_compact_bytes()
            };

            // Wrap compact telemetry in a CRSF frame
            let frame = CrsfFrame {
                addr: 0xC8,
                frame_type: 0x80, // OlympusTelemetry
                payload: compact,
            };

            if elrs_tx.send(frame).await.is_err() {
                break;
            }

            debug!("Sent ELRS heartbeat");
        }
    }

    fn rate_for_status(status: DroneStatus, base_rate: f32) -> f32 {
        match status {
            DroneStatus::Idle | DroneStatus::Charging => 1.0_f32.min(base_rate),
            DroneStatus::Scanning => 3.0_f32.min(base_rate),
            DroneStatus::Transiting | DroneStatus::Executing | DroneStatus::Returning => base_rate,
            DroneStatus::Emergency => (base_rate * 2.0).min(10.0),
            DroneStatus::Offline => 0.5,
        }
    }

    async fn telemetry_publisher_task(
        state: Arc<RwLock<BridgeState>>,
        config: BridgeConfig,
        tx: mpsc::Sender<BridgeMessage>,
    ) {
        let base_rate = config.telemetry.full_rate_hz;
        let mut current_rate = base_rate;
        let mut ticker = interval(Duration::from_secs_f32(1.0 / current_rate));

        loop {
            ticker.tick().await;

            let telem = {
                let s = state.read().await;
                s.telemetry.clone()
            };

            let new_rate = Self::rate_for_status(telem.status, base_rate);
            if (new_rate - current_rate).abs() > 0.01 {
                current_rate = new_rate;
                ticker = interval(Duration::from_secs_f32(1.0 / current_rate));
                debug!("Telemetry rate adjusted to {:.1}Hz for status {:?}", current_rate, telem.status);
            }

            let mut updated = telem;
            updated.timestamp = Utc::now();

            if tx.send(BridgeMessage::TelemetryUpdate(updated)).await.is_err() {
                break;
            }
        }
    }

    async fn lora_heartbeat_task(
        state: Arc<RwLock<BridgeState>>,
        config: BridgeConfig,
        lora_tx: mpsc::Sender<Vec<u8>>,
    ) {
        let period = Duration::from_secs(config.lora.heartbeat_interval_secs);
        let mut ticker = interval(period);

        loop {
            ticker.tick().await;

            let compact = {
                let s = state.read().await;
                s.telemetry.to_compact_bytes()
            };

            if lora_tx.send(compact).await.is_err() {
                break;
            }

            debug!("Sent LoRa heartbeat");
        }
    }

    async fn peer_timeout_task(
        state: Arc<RwLock<BridgeState>>,
        config: BridgeConfig,
    ) {
        let period = Duration::from_secs(5);
        let mut ticker = interval(period);
        let timeout = Duration::from_secs(config.resilience.peer_timeout_secs);

        loop {
            ticker.tick().await;

            let now = Utc::now();
            let mut state = state.write().await;

            for (id, member) in state.swarm_positions.iter_mut() {
                let age = now.signed_duration_since(member.last_seen);
                if age.num_seconds() > timeout.as_secs() as i64 {
                    if member.status != DroneStatus::Offline {
                        warn!("Peer {} timed out (last seen {}s ago)", id, age.num_seconds());
                        member.status = DroneStatus::Offline;
                    }
                }
            }
        }
    }
}
