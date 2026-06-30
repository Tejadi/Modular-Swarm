use std::collections::HashMap;
use serde::{Deserialize, Serialize};
use chrono::{DateTime, Utc};
use uuid::Uuid;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum DroneRole {
    #[default]
    Scout,
    Executor,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum DroneStatus {
    #[default]
    Idle,
    Scanning,
    Transiting,
    Executing,
    Returning,
    Charging,
    Emergency,
    Offline,
}

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize)]
pub struct GeoPosition {
    pub latitude: f64,
    pub longitude: f64,
    pub altitude: f64,
    pub heading: f32,
}

impl GeoPosition {
    pub fn new(lat: f64, lon: f64, alt: f64) -> Self {
        Self {
            latitude: lat,
            longitude: lon,
            altitude: alt,
            heading: 0.0,
        }
    }

    pub fn distance_to(&self, other: &GeoPosition) -> f64 {
        const EARTH_RADIUS: f64 = 6_371_000.0;
        let lat1 = self.latitude.to_radians();
        let lat2 = other.latitude.to_radians();
        let delta_lat = (other.latitude - self.latitude).to_radians();
        let delta_lon = (other.longitude - self.longitude).to_radians();
        let a = (delta_lat / 2.0).sin().powi(2)
            + lat1.cos() * lat2.cos() * (delta_lon / 2.0).sin().powi(2);
        let c = 2.0 * a.sqrt().asin();
        EARTH_RADIUS * c
    }
}

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize)]
pub struct BatteryState {
    pub voltage: f32,
    pub current: f32,
    pub percentage: u8,
    pub remaining_time: u32,
    pub cell_count: u8,
    pub temperature: f32,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct DroneTelemetry {
    pub drone_id: String,
    pub role: DroneRole,
    pub status: DroneStatus,
    pub position: GeoPosition,
    pub battery: BatteryState,
    pub timestamp: DateTime<Utc>,
    pub mesh_rssi: i16,
    pub wifi_connected: bool,
    pub current_task_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub intermediate_features: Option<Vec<u8>>,
}

impl DroneTelemetry {
    pub fn new(drone_id: String, role: DroneRole) -> Self {
        Self {
            drone_id,
            role,
            status: DroneStatus::Idle,
            position: GeoPosition::default(),
            battery: BatteryState::default(),
            timestamp: Utc::now(),
            mesh_rssi: 0,
            wifi_connected: false,
            current_task_id: None,
            intermediate_features: None,
        }
    }

    pub fn to_compact_bytes(&self) -> Vec<u8> {
        let mut buf = Vec::with_capacity(64);
        let id_bytes = self.drone_id.as_bytes();
        buf.push(id_bytes.len() as u8);
        buf.extend_from_slice(id_bytes);
        buf.push(self.role as u8);
        buf.push(self.status as u8);
        buf.extend_from_slice(&self.position.latitude.to_le_bytes());
        buf.extend_from_slice(&self.position.longitude.to_le_bytes());
        buf.extend_from_slice(&(self.position.altitude as f32).to_le_bytes());
        buf.push(self.battery.percentage);
        buf.extend_from_slice(&self.mesh_rssi.to_le_bytes());
        buf.push(self.wifi_connected as u8);
        buf.extend_from_slice(&self.timestamp.timestamp().to_le_bytes());
        buf
    }

    pub fn from_compact_bytes(data: &[u8]) -> Option<Self> {
        if data.is_empty() { return None; }
        let mut pos = 0;
        let id_len = data[pos] as usize;
        pos += 1;
        if pos + id_len > data.len() { return None; }
        let drone_id = String::from_utf8_lossy(&data[pos..pos + id_len]).to_string();
        pos += id_len;
        if pos + 28 > data.len() { return None; }
        let role = match data[pos] { 0 => DroneRole::Scout, _ => DroneRole::Executor };
        pos += 1;
        let status = match data[pos] {
            0 => DroneStatus::Idle, 1 => DroneStatus::Scanning, 2 => DroneStatus::Transiting,
            3 => DroneStatus::Executing, 4 => DroneStatus::Returning, 5 => DroneStatus::Charging,
            6 => DroneStatus::Emergency, _ => DroneStatus::Offline,
        };
        pos += 1;
        let latitude = f64::from_le_bytes(data[pos..pos+8].try_into().ok()?); pos += 8;
        let longitude = f64::from_le_bytes(data[pos..pos+8].try_into().ok()?); pos += 8;
        let altitude = f32::from_le_bytes(data[pos..pos+4].try_into().ok()?) as f64; pos += 4;
        let battery_percentage = data[pos]; pos += 1;
        let mesh_rssi = i16::from_le_bytes(data[pos..pos+2].try_into().ok()?); pos += 2;
        let wifi_connected = data[pos] != 0; pos += 1;
        let ts_secs = i64::from_le_bytes(data[pos..pos+8].try_into().ok()?);
        let timestamp = DateTime::from_timestamp(ts_secs, 0)?;
        Some(Self {
            drone_id, role, status,
            position: GeoPosition { latitude, longitude, altitude, heading: 0.0 },
            battery: BatteryState { percentage: battery_percentage, ..Default::default() },
            timestamp, mesh_rssi, wifi_connected, current_task_id: None,
            intermediate_features: None,
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DetectionType {
    // Agriculture (CERES)
    Weed, Pest, Disease, NutrientDeficiency, IrrigationLeak, CropStress, Obstacle,
    // Defense (ATHENA)
    HostileActivity, VehicleDetected, PersonDetected, IedSuspected, StructuralChange,
    // Industrial (VULCAN)
    StructuralCrack, Corrosion, ThermalAnomaly, LeakDetected, VegetationEncroachment, SurfaceDeformation,
    // SAR (HERMES)
    ThermalSignature, DebrisField, VehicleWreckage, SignalDetected,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct Detection {
    pub id: String,
    pub detection_type: Option<DetectionType>,
    pub position: GeoPosition,
    pub confidence: f32,
    pub severity: u8,
    pub detected_by: String,
    pub timestamp: DateTime<Utc>,
    pub image_ref: Option<String>,
    pub metadata: Option<serde_json::Value>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TaskType {
    // Agriculture (CERES)
    Spray, Seed, Fertilize, Inspect, Sample,
    // Defense (ATHENA) / SAR (HERMES) / shared
    Investigate, Mark, Photograph, Relay,
    // Industrial (VULCAN)
    ThermalScan, Measure,
    // SAR (HERMES)
    DropSupplies, MarkLocation, RelayComms,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum EscalationLevel {
    #[default]
    Auto,
    Notify,
    ApproveRequired,
    Emergency,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum TaskState {
    #[default]
    Pending, Auctioned, Assigned, AwaitingApproval, InProgress, Completed, Failed, Cancelled,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Task {
    pub id: String,
    pub task_type: TaskType,
    pub target_position: GeoPosition,
    pub priority: u8,
    pub state: TaskState,
    pub created_by: String,
    pub assigned_to: Option<String>,
    pub detection_id: String,
    pub created_at: DateTime<Utc>,
    pub deadline: Option<DateTime<Utc>>,
    pub payload_required: Option<String>,
    #[serde(default)]
    pub escalation_level: EscalationLevel,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TaskBid {
    pub task_id: String,
    pub bidder_id: String,
    pub cost: f64,
    pub eta_seconds: u32,
    pub battery_after: u8,
    pub timestamp: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", content = "payload")]
pub enum DroneCommand {
    EmergencyStop, ReturnToLaunch,
    GoTo { position: GeoPosition },
    StartScan { zone_id: String },
    Pause, Resume,
    ExecuteTask { task_id: String },
    SetAltitude { altitude: f64 },
    Ack { command_id: String },
    // Strategic layer — AI Agent commands
    RecallForUpdate,
    Redeploy,
    UpdateModel { version: u32 },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CommandMessage {
    pub id: String,
    pub target_drone: String,
    pub command: DroneCommand,
    pub issued_by: String,
    pub issued_at: DateTime<Utc>,
    pub priority: u8,
}

impl CommandMessage {
    pub fn new(target: String, command: DroneCommand, issued_by: String) -> Self {
        Self {
            id: Uuid::new_v4().to_string(),
            target_drone: target, command, issued_by,
            issued_at: Utc::now(), priority: 5,
        }
    }
}

pub mod keys {
    pub const PREFIX: &str = "olympus";
    pub fn telemetry(drone_id: &str) -> String { format!("{}/swarm/{}/telemetry", PREFIX, drone_id) }
    pub fn telemetry_wildcard() -> String { format!("{}/swarm/*/telemetry", PREFIX) }
    pub fn command(drone_id: &str) -> String { format!("{}/command/{}", PREFIX, drone_id) }
    pub fn command_broadcast() -> String { format!("{}/command/*", PREFIX) }
    pub fn detection(drone_id: &str) -> String { format!("{}/detection/{}", PREFIX, drone_id) }
    pub fn detection_all() -> String { format!("{}/detection/**", PREFIX) }
    pub fn task_auction() -> String { format!("{}/task/auction", PREFIX) }
    pub fn task_bid(task_id: &str) -> String { format!("{}/task/{}/bid", PREFIX, task_id) }
    pub fn lora_rx(node_id: u32) -> String { format!("{}/lora/{}/rx", PREFIX, node_id) }
    pub fn features(drone_id: &str) -> String { format!("{}/swarm/{}/features", PREFIX, drone_id) }
    pub fn features_wildcard() -> String { format!("{}/swarm/*/features", PREFIX) }
    pub fn cbba_bundle(executor_id: &str) -> String { format!("{}/cbba/{}/bundle", PREFIX, executor_id) }
    pub fn escalation(drone_id: &str) -> String { format!("{}/escalation/{}", PREFIX, drone_id) }
    pub fn escalation_response() -> String { format!("{}/escalation/response", PREFIX) }
    pub fn model_metadata(drone_id: &str) -> String { format!("{}/swarm/{}/model/metadata", PREFIX, drone_id) }
    pub fn model_metadata_wildcard() -> String { format!("{}/swarm/*/model/metadata", PREFIX) }
    pub fn swarmnet_status() -> String { format!("{}/swarmnet/status", PREFIX) }
    pub fn swarmnet_global_model() -> String { format!("{}/swarmnet/model/global", PREFIX) }
    pub fn elrs_rx(drone_id: &str) -> String { format!("{}/elrs/{}/rx", PREFIX, drone_id) }
    pub fn elrs_link(drone_id: &str) -> String { format!("{}/swarm/{}/elrs/link", PREFIX, drone_id) }
    pub fn registry(vehicle_id: &str) -> String { format!("{}/registry/{}", PREFIX, vehicle_id) }
    pub fn registry_wildcard() -> String { format!("{}/registry/*", PREFIX) }
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct SwarmNetStatus {
    pub active_drones: u32,
    pub model_versions: HashMap<String, u32>,
    pub accuracies: HashMap<String, f64>,
    pub contributions: HashMap<String, u32>,
    pub timestamp: DateTime<Utc>,
}

// --- Trust-Tiered Registration ---

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum TrustTier {
    #[default]
    Trusted,
    Partner,
    Observer,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum CommandAuthority {
    Binding,
    Advisory,
    #[default]
    None,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum RegistrationStatus {
    Pending,
    #[default]
    Approved,
    Rejected,
    Revoked,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CapabilityManifest {
    #[serde(default = "default_true")]
    pub provides_telemetry: bool,
    #[serde(default)]
    pub provides_detections: bool,
    #[serde(default)]
    pub provides_features: bool,
    #[serde(default)]
    pub accepted_commands: Vec<String>,
    #[serde(default)]
    pub command_authority: CommandAuthority,
    #[serde(default)]
    pub participates_in_cbba: bool,
    #[serde(default = "default_ttl")]
    pub ttl_seconds: u64,
    #[serde(default)]
    pub data_encryption_required: bool,
}

fn default_true() -> bool { true }
fn default_ttl() -> u64 { 3600 }

impl Default for CapabilityManifest {
    fn default() -> Self {
        Self {
            provides_telemetry: true,
            provides_detections: false,
            provides_features: false,
            accepted_commands: Vec::new(),
            command_authority: CommandAuthority::None,
            participates_in_cbba: false,
            ttl_seconds: 3600,
            data_encryption_required: false,
        }
    }
}
