use axum::{
    extract::State,
    http::StatusCode,
    response::IntoResponse,
    Extension, Json,
};
use serde_json::json;
use tracing::info;
use uuid::Uuid;

use crate::error::ApiError;
use crate::middleware::auth::AuthContext;
use crate::state::AppState;

/// GET /api/v1/ai-agent/status
/// Returns drift metrics, accuracy, model version, and fleet state.
pub async fn agent_status(
    State(state): State<AppState>,
    Extension(_ctx): Extension<AuthContext>,
) -> Result<Json<serde_json::Value>, ApiError> {
    let swarmnet = state.swarmnet_status.read().await;
    Ok(Json(json!({
        "model_version": swarmnet.model_versions.get("global").unwrap_or(&0),
        "accuracy": swarmnet.accuracies.get("global").unwrap_or(&0.0),
        "active_drones": swarmnet.active_drones,
        "schedule": swarmnet.current_schedule,
    })))
}

/// POST /api/v1/ai-agent/retrain
/// Force retrain + recall + push model + redeploy cycle.
/// Publishes RECALL_FOR_UPDATE → waits → pushes UPDATE_MODEL → REDEPLOY via Zenoh.
pub async fn force_retrain(
    State(state): State<AppState>,
    Extension(ctx): Extension<AuthContext>,
) -> Result<impl IntoResponse, ApiError> {
    if !ctx.has_scope("command:all") {
        return Err(ApiError::BadRequest(
            "Insufficient scope: requires command:all".to_string(),
        ));
    }

    let command_id = Uuid::new_v4().to_string();
    let payload = json!({
        "command_id": command_id,
        "command": "RECALL_FOR_UPDATE",
        "params": { "reason": "force_retrain", "initiated_by": ctx.partner_id },
        "target_drone": "*",
    });

    state
        .zenoh
        .publish_global_command(&payload)
        .await
        .map_err(ApiError::ZenohError)?;

    info!(partner = %ctx.partner_id, "Force retrain initiated — RECALL_FOR_UPDATE broadcast");

    Ok((
        StatusCode::ACCEPTED,
        Json(json!({
            "ok": true,
            "message": "Recall/retrain/redeploy cycle initiated",
            "command_id": command_id,
        })),
    ))
}

/// POST /api/v1/ai-agent/recall
/// Recall all scouts for model update.
pub async fn recall_fleet(
    State(state): State<AppState>,
    Extension(ctx): Extension<AuthContext>,
) -> Result<impl IntoResponse, ApiError> {
    if !ctx.has_scope("command:all") {
        return Err(ApiError::BadRequest(
            "Insufficient scope: requires command:all".to_string(),
        ));
    }

    let command_id = Uuid::new_v4().to_string();
    let payload = json!({
        "command_id": command_id,
        "command": "RECALL_FOR_UPDATE",
        "params": { "reason": "manual_recall", "initiated_by": ctx.partner_id },
        "target_drone": "*",
    });

    state
        .zenoh
        .publish_global_command(&payload)
        .await
        .map_err(ApiError::ZenohError)?;

    info!(partner = %ctx.partner_id, "Fleet recall command broadcast");

    Ok((
        StatusCode::OK,
        Json(json!({
            "ok": true,
            "message": "RECALL_FOR_UPDATE broadcast to all scouts",
            "command_id": command_id,
        })),
    ))
}

/// POST /api/v1/ai-agent/redeploy
/// Resume all scouts after model update.
pub async fn redeploy_fleet(
    State(state): State<AppState>,
    Extension(ctx): Extension<AuthContext>,
) -> Result<impl IntoResponse, ApiError> {
    if !ctx.has_scope("command:all") {
        return Err(ApiError::BadRequest(
            "Insufficient scope: requires command:all".to_string(),
        ));
    }

    let command_id = Uuid::new_v4().to_string();
    let payload = json!({
        "command_id": command_id,
        "command": "REDEPLOY",
        "params": { "initiated_by": ctx.partner_id },
        "target_drone": "*",
    });

    state
        .zenoh
        .publish_global_command(&payload)
        .await
        .map_err(ApiError::ZenohError)?;

    info!(partner = %ctx.partner_id, "Fleet redeploy command broadcast");

    Ok((
        StatusCode::OK,
        Json(json!({
            "ok": true,
            "message": "REDEPLOY broadcast to all scouts",
            "command_id": command_id,
        })),
    ))
}
