use std::collections::HashMap;

use axum::{
    extract::{Query, State},
    Json,
};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use crate::error::ApiError;
use crate::metrics::{DetectionSummary, TelemetryLogEntry};
use crate::state::AppState;

#[derive(Debug, Deserialize)]
pub struct TelemetryQuery {
    pub vehicle_id: Option<String>,
    pub from: Option<DateTime<Utc>>,
    pub to: Option<DateTime<Utc>>,
    #[serde(default = "default_limit")]
    pub limit: u32,
}

fn default_limit() -> u32 {
    1000
}

pub async fn query_telemetry(
    State(state): State<AppState>,
    Query(params): Query<TelemetryQuery>,
) -> Result<Json<Vec<TelemetryLogEntry>>, ApiError> {
    let db = state
        .metrics
        .as_ref()
        .ok_or_else(|| ApiError::Internal("Metrics database not available".to_string()))?;

    let limit = params.limit.min(10000);
    let entries = db
        .query_telemetry(
            params.vehicle_id.as_deref(),
            params.from,
            params.to,
            limit,
        )
        .await;

    Ok(Json(entries))
}

pub async fn detection_summary(
    State(state): State<AppState>,
) -> Result<Json<Vec<DetectionSummary>>, ApiError> {
    let db = state
        .metrics
        .as_ref()
        .ok_or_else(|| ApiError::Internal("Metrics database not available".to_string()))?;

    let summary = db.detection_summary().await;
    Ok(Json(summary))
}

#[derive(Debug, Serialize)]
pub struct SwarmNetStatusResponse {
    pub active_drones: u32,
    pub model_versions: HashMap<String, u32>,
    pub accuracies: HashMap<String, f64>,
    pub current_schedule: HashMap<String, String>,
}

pub async fn swarmnet_status(
    State(state): State<AppState>,
) -> Json<SwarmNetStatusResponse> {
    let swarmnet = state.swarmnet_status.read().await;
    Json(SwarmNetStatusResponse {
        active_drones: swarmnet.active_drones,
        model_versions: swarmnet.model_versions.clone(),
        accuracies: swarmnet.accuracies.clone(),
        current_schedule: swarmnet.current_schedule.clone(),
    })
}
