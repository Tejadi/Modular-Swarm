use axum::{extract::State, Extension, Json};
use chrono::Utc;
use serde_json::json;
use tracing::{info, warn};

use crate::error::ApiError;
use crate::middleware::auth::AuthContext;
use crate::models::MissionState;
use crate::state::AppState;

pub async fn get_mission(
    State(state): State<AppState>,
) -> Result<Json<MissionState>, ApiError> {
    let mission = state.mission.read().await;

    let mut snapshot = mission.clone();
    if let Some(start) = snapshot.start_time {
        let elapsed = Utc::now()
            .signed_duration_since(start)
            .num_seconds()
            .max(0) as u64;
        snapshot.elapsed_seconds = elapsed;
    }

    Ok(Json(snapshot))
}

pub async fn start_mission(
    State(state): State<AppState>,
    Extension(ctx): Extension<AuthContext>,
) -> Result<Json<MissionState>, ApiError> {
    if !ctx.has_scope("mission:control") && !ctx.has_scope("command:all") {
        return Err(ApiError::BadRequest(
            "Insufficient scope: requires mission:control".to_string(),
        ));
    }

    let mut mission = state.mission.write().await;

    if mission.phase != "idle" && mission.phase != "completed" && mission.phase != "aborted" {
        return Err(ApiError::MissionError(format!(
            "Cannot start mission in phase '{}'",
            mission.phase
        )));
    }

    let vehicles = state.vehicles.read().await;
    let active: Vec<String> = vehicles.keys().cloned().collect();

    if active.is_empty() {
        warn!("Starting mission with no vehicles in registry");
    }

    mission.phase = "active".to_string();
    mission.start_time = Some(Utc::now());
    mission.elapsed_seconds = 0;
    mission.active_vehicles = active;

    let start_cmd = json!({
        "command": "MISSION_START",
        "timestamp": Utc::now().to_rfc3339(),
    });
    if let Err(e) = state.zenoh.publish_global_command(&start_cmd).await {
        warn!("Failed to publish MISSION_START: {e}");
    }

    info!(phase = "active", partner = %ctx.partner_id, "Mission started");

    Ok(Json(mission.clone()))
}

pub async fn abort_mission(
    State(state): State<AppState>,
    Extension(ctx): Extension<AuthContext>,
) -> Result<Json<MissionState>, ApiError> {
    if !ctx.has_scope("mission:control") && !ctx.has_scope("command:all") {
        return Err(ApiError::BadRequest(
            "Insufficient scope: requires mission:control".to_string(),
        ));
    }

    let mut mission = state.mission.write().await;

    mission.phase = "aborted".to_string();

    let abort_cmd = json!({
        "command": "ABORT_ALL",
        "timestamp": Utc::now().to_rfc3339(),
    });
    if let Err(e) = state.zenoh.publish_global_command(&abort_cmd).await {
        warn!("Failed to publish ABORT_ALL: {e}");
        return Err(ApiError::ZenohError(format!(
            "Failed to broadcast abort: {e}"
        )));
    }

    info!(phase = "aborted", partner = %ctx.partner_id, "Mission aborted -- ABORT_ALL published");

    Ok(Json(mission.clone()))
}
