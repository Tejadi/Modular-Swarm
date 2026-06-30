use chrono::{DateTime, Utc};
use olympus_bridge::protocol::{TrustTier, RegistrationStatus, CapabilityManifest};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize)]
pub struct Position {
    pub latitude: f64,
    pub longitude: f64,
    pub altitude: f64,
    pub heading: f32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VehicleRecord {
    pub id: String,
    pub role: String,
    pub status: String,
    pub position: Position,
    pub battery_pct: u8,
    pub signal_rssi: i16,
    pub current_task: Option<String>,
    pub last_seen: DateTime<Utc>,
    pub tank_level: Option<f32>,
    #[serde(default)]
    pub capabilities: Vec<String>,
    #[serde(default)]
    pub trust_tier: TrustTier,
    #[serde(default)]
    pub registration_status: RegistrationStatus,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub capability_manifest: Option<CapabilityManifest>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub approved_by: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub approved_at: Option<DateTime<Utc>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expires_at: Option<DateTime<Utc>>,
}

impl VehicleRecord {
    pub fn new(id: String) -> Self {
        Self {
            id,
            role: "scout".to_string(),
            status: "idle".to_string(),
            position: Position::default(),
            battery_pct: 0,
            signal_rssi: 0,
            current_task: None,
            last_seen: Utc::now(),
            tank_level: None,
            capabilities: Vec::new(),
            trust_tier: TrustTier::Trusted,
            registration_status: RegistrationStatus::Approved,
            capability_manifest: None,
            approved_by: None,
            approved_at: None,
            expires_at: None,
        }
    }
}

// ---------------------------------------------------------------------------
// Command validation — typed allowlist prevents arbitrary JSON injection
// ---------------------------------------------------------------------------

pub const ALLOWED_COMMANDS: &[&str] = &[
    "EMERGENCY_STOP",
    "RETURN_TO_LAUNCH",
    "PAUSE",
    "RESUME",
    "GO_TO",
    "START_SCAN",
    "EXECUTE_TASK",
    "SET_ALTITUDE",
    "RECALL_FOR_UPDATE",
    "REDEPLOY",
    "UPDATE_MODEL",
    "ARM",
    "DISARM",
    "MISSION_START",
    "ABORT_ALL",
];

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CommandRequest {
    pub command: String,
    #[serde(default)]
    pub params: serde_json::Value,
}

impl CommandRequest {
    /// Validate command type is in the allowlist and params are well-formed.
    pub fn validate(&self) -> Result<(), String> {
        let cmd = self.command.to_uppercase();

        if !ALLOWED_COMMANDS.contains(&cmd.as_str()) {
            return Err(format!("Unknown command '{}'", self.command));
        }

        // Validate coordinate params when present
        if cmd == "GO_TO" {
            if let Some(lat) = self.params.get("latitude").and_then(|v| v.as_f64()) {
                validate_latitude(lat)?;
            }
            if let Some(lon) = self.params.get("longitude").and_then(|v| v.as_f64()) {
                validate_longitude(lon)?;
            }
            if let Some(alt) = self.params.get("altitude").and_then(|v| v.as_f64()) {
                validate_altitude(alt)?;
            }
        }

        if cmd == "SET_ALTITUDE" {
            if let Some(alt) = self.params.get("altitude").and_then(|v| v.as_f64()) {
                validate_altitude(alt)?;
            }
        }

        // Reject excessively large param payloads (max 4KB serialized)
        if let Ok(serialized) = serde_json::to_string(&self.params) {
            if serialized.len() > 4096 {
                return Err("Command params exceed 4KB limit".to_string());
            }
        }

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Position / coordinate validators
// ---------------------------------------------------------------------------

pub fn validate_latitude(lat: f64) -> Result<(), String> {
    if lat.is_nan() || lat.is_infinite() || !(-90.0..=90.0).contains(&lat) {
        return Err(format!("Invalid latitude: must be -90..90"));
    }
    Ok(())
}

pub fn validate_longitude(lon: f64) -> Result<(), String> {
    if lon.is_nan() || lon.is_infinite() || !(-180.0..=180.0).contains(&lon) {
        return Err(format!("Invalid longitude: must be -180..180"));
    }
    Ok(())
}

pub fn validate_altitude(alt: f64) -> Result<(), String> {
    if alt.is_nan() || alt.is_infinite() || !(-100.0..=50000.0).contains(&alt) {
        return Err(format!("Invalid altitude: must be -100..50000"));
    }
    Ok(())
}

pub fn validate_position(pos: &Position) -> Result<(), String> {
    validate_latitude(pos.latitude)?;
    validate_longitude(pos.longitude)?;
    validate_altitude(pos.altitude)?;
    Ok(())
}

/// Allowed vehicle status strings
pub const ALLOWED_STATUSES: &[&str] = &[
    "idle", "scanning", "transiting", "executing", "returning",
    "emergency", "offline", "charging", "armed", "disarmed", "recalled",
];

#[derive(Debug, Serialize)]
pub struct CommandResponse {
    pub ok: bool,
    pub message: String,
    pub command_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MissionState {
    pub phase: String,
    pub start_time: Option<DateTime<Utc>>,
    pub elapsed_seconds: u64,
    pub active_vehicles: Vec<String>,
}

impl Default for MissionState {
    fn default() -> Self {
        Self {
            phase: "idle".to_string(),
            start_time: None,
            elapsed_seconds: 0,
            active_vehicles: Vec::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DetectionRecord {
    pub id: String,
    #[serde(rename = "type")]
    pub detection_type: String,
    pub position: Position,
    pub confidence: f32,
    pub timestamp: DateTime<Utc>,
    pub status: String,
    pub detected_by: String,
    pub assigned_to: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TaskRecord {
    pub id: String,
    #[serde(rename = "type")]
    pub task_type: String,
    pub target_position: Position,
    pub status: String,
    pub assigned_to: Option<String>,
    pub priority: u8,
}

#[derive(Debug, Serialize)]
pub struct HealthResponse {
    pub status: String,
    pub zenoh_connected: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TelemetryEvent {
    pub event: String,
    pub vehicle_id: String,
    pub data: serde_json::Value,
    pub timestamp: DateTime<Utc>,
}
