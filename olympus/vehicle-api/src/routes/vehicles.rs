use axum::{
    extract::{Path, State},
    http::StatusCode,
    response::IntoResponse,
    Extension, Json,
};
use chrono::Utc;
use serde::{Deserialize, Serialize};
use serde_json::json;
use tracing::info;
use uuid::Uuid;

use crate::error::ApiError;
use crate::metrics::{CommandLogEntry, TelemetryLogEntry};
use crate::middleware::auth::AuthContext;
use crate::models::{
    CommandRequest, CommandResponse, Position, VehicleRecord, ALLOWED_STATUSES,
    validate_position,
};
use crate::state::AppState;
use olympus_bridge::protocol::{
    TrustTier, RegistrationStatus, CapabilityManifest, CommandAuthority,
};

// Command allowlist is in models.rs (ALLOWED_COMMANDS) and validated via CommandRequest::validate()
use crate::models::ALLOWED_COMMANDS;

const MAX_VEHICLE_ID_LEN: usize = 64;

#[derive(Debug, Deserialize)]
pub struct RegisterRequest {
    pub vehicle_id: String,
    pub role: String,
    #[serde(default)]
    pub capabilities: Vec<String>,
    #[serde(default)]
    pub position: Option<Position>,
    #[serde(default)]
    pub trust_tier: Option<String>,
    #[serde(default)]
    pub capability_manifest: Option<CapabilityManifest>,
}

#[derive(Debug, Serialize)]
pub struct RegisterResponse {
    pub ok: bool,
    pub vehicle_id: String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub registration_status: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct ApprovalRequest {
    pub approved: bool,
    #[serde(default)]
    pub restricted_commands: Option<Vec<String>>,
}

#[derive(Debug, Deserialize)]
pub struct TelemetryIngest {
    #[serde(default)]
    pub position: Option<Position>,
    #[serde(default)]
    pub battery_pct: Option<u8>,
    #[serde(default)]
    pub status: Option<String>,
    #[serde(default)]
    pub signal_rssi: Option<i16>,
    #[serde(default)]
    pub tank_level: Option<f32>,
}

pub async fn list_vehicles(
    State(state): State<AppState>,
) -> Result<Json<Vec<VehicleRecord>>, ApiError> {
    let vehicles = state.vehicles.read().await;
    let list: Vec<VehicleRecord> = vehicles.values().cloned().collect();
    Ok(Json(list))
}

pub async fn get_vehicle(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<VehicleRecord>, ApiError> {
    validate_vehicle_id(&id)?;
    let vehicles = state.vehicles.read().await;
    let record = vehicles
        .get(&id)
        .cloned()
        .ok_or_else(|| ApiError::VehicleNotFound(id))?;
    Ok(Json(record))
}

pub async fn send_command(
    State(state): State<AppState>,
    Extension(ctx): Extension<AuthContext>,
    Path(id): Path<String>,
    Json(body): Json<CommandRequest>,
) -> Result<Json<CommandResponse>, ApiError> {
    validate_vehicle_id(&id)?;

    if !ctx.has_scope("command:all") && !ctx.has_scope("command:own") {
        return Err(ApiError::BadRequest("Insufficient scope for commands".to_string()));
    }

    // Validate command type + params (allowlist, coordinate bounds, payload size)
    body.validate().map_err(ApiError::BadRequest)?;

    let cmd_upper = body.command.to_uppercase();

    {
        let vehicles = state.vehicles.read().await;
        if !vehicles.contains_key(&id) {
            return Err(ApiError::VehicleNotFound(id));
        }
    }

    let command_id = Uuid::new_v4().to_string();

    let payload = json!({
        "command_id": command_id,
        "command": cmd_upper,
        "params": body.params,
        "target_drone": id,
    });

    state
        .zenoh
        .publish_command(&id, &payload)
        .await
        .map_err(ApiError::ZenohError)?;

    if let Some(ref db) = state.metrics {
        db.log_command(&CommandLogEntry {
            command_id: command_id.clone(),
            vehicle_id: id.clone(),
            command: cmd_upper.clone(),
            partner_id: ctx.partner_id.clone(),
            timestamp: Utc::now(),
        })
        .await;
    }

    info!(vehicle_id = %id, command = %cmd_upper, partner = %ctx.partner_id, "Command dispatched");

    Ok(Json(CommandResponse {
        ok: true,
        message: format!("Command '{}' sent to vehicle '{}'", cmd_upper, id),
        command_id,
    }))
}

pub async fn register_vehicle(
    State(state): State<AppState>,
    Extension(ctx): Extension<AuthContext>,
    Json(body): Json<RegisterRequest>,
) -> Result<impl IntoResponse, ApiError> {
    if !ctx.has_scope("write:telemetry") {
        return Err(ApiError::BadRequest(
            "Insufficient scope: requires write:telemetry".to_string(),
        ));
    }

    validate_vehicle_id(&body.vehicle_id)?;

    // Validate position coordinates if provided
    if let Some(ref pos) = body.position {
        validate_position(pos).map_err(ApiError::BadRequest)?;
    }

    let valid_roles = ["scout", "executor", "partner", "ground_vehicle", "aircraft", "observer"];
    let role = body.role.to_lowercase();
    if !valid_roles.contains(&role.as_str()) {
        return Err(ApiError::BadRequest(format!(
            "Invalid role '{}'. Allowed: {:?}",
            body.role, valid_roles
        )));
    }

    // Determine trust tier from request (default: trusted for backward compat)
    let tier_str = body.trust_tier.as_deref().unwrap_or("trusted");
    let (trust_tier, reg_status, http_status) = match tier_str {
        "trusted" => {
            // Own agents auto-approve
            (TrustTier::Trusted, RegistrationStatus::Approved, StatusCode::CREATED)
        }
        "partner" => {
            // Peer vehicles require a capability manifest and operator approval
            if body.capability_manifest.is_none() {
                return Err(ApiError::BadRequest(
                    "Partner registration requires a capability_manifest".to_string(),
                ));
            }
            (TrustTier::Partner, RegistrationStatus::Pending, StatusCode::ACCEPTED)
        }
        "observer" => {
            // Observers auto-approve with no command authority
            (TrustTier::Observer, RegistrationStatus::Approved, StatusCode::CREATED)
        }
        _ => {
            return Err(ApiError::BadRequest(format!(
                "Invalid trust_tier '{}'. Allowed: trusted, partner, observer",
                tier_str
            )));
        }
    };

    // For observers, force command authority to NONE
    let manifest = match trust_tier {
        TrustTier::Observer => {
            Some(CapabilityManifest {
                provides_telemetry: true,
                command_authority: CommandAuthority::None,
                accepted_commands: Vec::new(),
                participates_in_cbba: false,
                ..body.capability_manifest.unwrap_or_default()
            })
        }
        TrustTier::Trusted => {
            // Trusted agents get full capabilities by default
            Some(body.capability_manifest.unwrap_or(CapabilityManifest {
                provides_telemetry: true,
                provides_detections: true,
                provides_features: true,
                accepted_commands: ALLOWED_COMMANDS.iter().map(|s| s.to_string()).collect(),
                command_authority: CommandAuthority::Binding,
                participates_in_cbba: true,
                ttl_seconds: 0, // no expiry
                data_encryption_required: false,
            }))
        }
        TrustTier::Partner => body.capability_manifest.clone(),
    };

    // Compute expiry from TTL
    let expires_at = manifest.as_ref().and_then(|m| {
        if m.ttl_seconds > 0 {
            Some(Utc::now() + chrono::Duration::seconds(m.ttl_seconds as i64))
        } else {
            None
        }
    });

    let record = VehicleRecord {
        id: body.vehicle_id.clone(),
        role,
        status: "idle".to_string(),
        position: body.position.unwrap_or_default(),
        battery_pct: 100,
        signal_rssi: 0,
        current_task: None,
        last_seen: Utc::now(),
        tank_level: None,
        capabilities: body.capabilities.clone(),
        trust_tier,
        registration_status: reg_status,
        capability_manifest: manifest,
        approved_by: if reg_status == RegistrationStatus::Approved {
            Some("auto".to_string())
        } else {
            None
        },
        approved_at: if reg_status == RegistrationStatus::Approved {
            Some(Utc::now())
        } else {
            None
        },
        expires_at,
    };

    {
        let mut vehicles = state.vehicles.write().await;
        vehicles.insert(body.vehicle_id.clone(), record);
    }

    // Publish registration event to Zenoh
    let registry_payload = json!({
        "action": "registered",
        "vehicle_id": body.vehicle_id,
        "trust_tier": tier_str,
        "registration_status": format!("{:?}", reg_status).to_lowercase(),
        "timestamp": Utc::now().to_rfc3339(),
    });
    let registry_key = format!("olympus/registry/{}", body.vehicle_id);
    let _ = state.zenoh.publish_raw(&registry_key, &registry_payload).await;

    let status_str = format!("{:?}", reg_status).to_lowercase();
    let msg = match trust_tier {
        TrustTier::Partner => format!("Vehicle registered (pending approval)"),
        _ => format!("Vehicle registered and approved"),
    };

    info!(
        vehicle_id = %body.vehicle_id,
        partner = %ctx.partner_id,
        trust_tier = %tier_str,
        status = %status_str,
        "Vehicle registered"
    );

    Ok((
        http_status,
        Json(RegisterResponse {
            ok: true,
            vehicle_id: body.vehicle_id,
            message: msg,
            registration_status: Some(status_str),
        }),
    ))
}

pub async fn approve_vehicle(
    State(state): State<AppState>,
    Extension(ctx): Extension<AuthContext>,
    Path(id): Path<String>,
    Json(body): Json<ApprovalRequest>,
) -> Result<Json<serde_json::Value>, ApiError> {
    if !ctx.has_scope("admin:approve") && !ctx.has_scope("command:all") {
        return Err(ApiError::BadRequest(
            "Insufficient scope: requires admin:approve".to_string(),
        ));
    }

    validate_vehicle_id(&id)?;

    let mut vehicles = state.vehicles.write().await;
    let record = vehicles
        .get_mut(&id)
        .ok_or_else(|| ApiError::VehicleNotFound(id.clone()))?;

    if record.trust_tier != TrustTier::Partner {
        return Err(ApiError::BadRequest(format!(
            "Vehicle '{}' is not a partner (tier={:?}), approval not applicable",
            id, record.trust_tier
        )));
    }

    let now = Utc::now();

    if body.approved {
        record.registration_status = RegistrationStatus::Approved;
        record.approved_by = Some(ctx.partner_id.clone());
        record.approved_at = Some(now);

        // Apply command restrictions if provided
        if let Some(ref restricted) = body.restricted_commands {
            if let Some(ref mut manifest) = record.capability_manifest {
                manifest.accepted_commands = restricted.clone();
            }
        }

        info!(vehicle_id = %id, approver = %ctx.partner_id, "Partner vehicle APPROVED");
    } else {
        record.registration_status = RegistrationStatus::Rejected;
        info!(vehicle_id = %id, approver = %ctx.partner_id, "Partner vehicle REJECTED");
    }

    // Publish approval event to Zenoh
    let action = if body.approved { "approved" } else { "rejected" };
    let registry_payload = json!({
        "action": action,
        "vehicle_id": id,
        "approved_by": ctx.partner_id,
        "timestamp": now.to_rfc3339(),
    });
    let registry_key = format!("olympus/registry/{}", id);
    drop(vehicles); // release write lock before async publish
    let _ = state.zenoh.publish_raw(&registry_key, &registry_payload).await;

    Ok(Json(json!({
        "ok": true,
        "vehicle_id": id,
        "action": action,
    })))
}

pub async fn revoke_vehicle(
    State(state): State<AppState>,
    Extension(ctx): Extension<AuthContext>,
    Path(id): Path<String>,
) -> Result<Json<serde_json::Value>, ApiError> {
    if !ctx.has_scope("admin:approve") && !ctx.has_scope("command:all") {
        return Err(ApiError::BadRequest(
            "Insufficient scope: requires admin:approve".to_string(),
        ));
    }

    validate_vehicle_id(&id)?;

    {
        let mut vehicles = state.vehicles.write().await;
        let record = vehicles
            .get_mut(&id)
            .ok_or_else(|| ApiError::VehicleNotFound(id.clone()))?;

        record.registration_status = RegistrationStatus::Revoked;
    }

    info!(vehicle_id = %id, revoker = %ctx.partner_id, "Vehicle REVOKED");

    // Publish revocation event to Zenoh
    let registry_payload = json!({
        "action": "revoked",
        "vehicle_id": id,
        "revoked_by": ctx.partner_id,
        "timestamp": Utc::now().to_rfc3339(),
    });
    let registry_key = format!("olympus/registry/{}", id);
    let _ = state.zenoh.publish_raw(&registry_key, &registry_payload).await;

    Ok(Json(json!({
        "ok": true,
        "vehicle_id": id,
        "action": "revoked",
    })))
}

pub async fn list_pending_vehicles(
    State(state): State<AppState>,
) -> Result<Json<Vec<VehicleRecord>>, ApiError> {
    let vehicles = state.vehicles.read().await;
    let pending: Vec<VehicleRecord> = vehicles
        .values()
        .filter(|v| v.registration_status == RegistrationStatus::Pending)
        .cloned()
        .collect();
    Ok(Json(pending))
}

pub async fn ingest_telemetry(
    State(state): State<AppState>,
    Extension(ctx): Extension<AuthContext>,
    Path(id): Path<String>,
    Json(body): Json<TelemetryIngest>,
) -> Result<impl IntoResponse, ApiError> {
    if !ctx.has_scope("write:telemetry") {
        return Err(ApiError::BadRequest(
            "Insufficient scope: requires write:telemetry".to_string(),
        ));
    }

    validate_vehicle_id(&id)?;

    // Validate position coordinates if provided
    if let Some(ref pos) = body.position {
        validate_position(pos).map_err(ApiError::BadRequest)?;
    }

    // Validate status string against allowlist
    if let Some(ref status) = body.status {
        if !ALLOWED_STATUSES.contains(&status.as_str()) {
            return Err(ApiError::BadRequest(format!(
                "Invalid status '{}'. Allowed: {:?}",
                status, ALLOWED_STATUSES
            )));
        }
    }

    let now = Utc::now();

    {
        let mut vehicles = state.vehicles.write().await;
        let record = vehicles
            .entry(id.clone())
            .or_insert_with(|| VehicleRecord::new(id.clone()));

        if let Some(pos) = body.position {
            record.position = pos;
        }
        if let Some(batt) = body.battery_pct {
            record.battery_pct = batt;
        }
        if let Some(ref status) = body.status {
            record.status = status.clone();
        }
        if let Some(rssi) = body.signal_rssi {
            record.signal_rssi = rssi;
        }
        if let Some(tank) = body.tank_level {
            record.tank_level = Some(tank);
        }
        record.last_seen = now;
    }

    let telemetry_json = json!({
        "drone_id": id,
        "position": body.position,
        "battery": { "percentage": body.battery_pct.unwrap_or(0) },
        "status": body.status.as_deref().unwrap_or("idle"),
        "timestamp": now.to_rfc3339(),
        "source": "partner",
        "partner_id": ctx.partner_id,
    });

    let _ = state
        .zenoh
        .publish_telemetry(&id, &telemetry_json)
        .await;

    if let Some(ref db) = state.metrics {
        let pos = body.position.unwrap_or_default();
        db.log_telemetry(&TelemetryLogEntry {
            vehicle_id: id.clone(),
            timestamp: now,
            latitude: pos.latitude,
            longitude: pos.longitude,
            altitude: pos.altitude,
            battery_pct: body.battery_pct.unwrap_or(0),
            status: body.status.unwrap_or_else(|| "idle".to_string()),
            partner_id: Some(ctx.partner_id.clone()),
        })
        .await;
    }

    Ok((StatusCode::OK, Json(json!({ "ok": true }))))
}

fn validate_vehicle_id(id: &str) -> Result<(), ApiError> {
    if id.is_empty() || id.len() > MAX_VEHICLE_ID_LEN {
        return Err(ApiError::BadRequest(format!(
            "Vehicle ID must be 1-{MAX_VEHICLE_ID_LEN} characters"
        )));
    }
    if !id
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
    {
        return Err(ApiError::BadRequest(
            "Vehicle ID may only contain alphanumeric characters, hyphens, and underscores"
                .to_string(),
        ));
    }
    Ok(())
}
