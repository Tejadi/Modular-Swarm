use axum::{extract::State, Json};

use crate::models::HealthResponse;
use crate::state::AppState;

pub async fn health_check(State(state): State<AppState>) -> Json<HealthResponse> {
    Json(HealthResponse {
        status: "ok".to_string(),
        zenoh_connected: state.zenoh.is_connected(),
    })
}
