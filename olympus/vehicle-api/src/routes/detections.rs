use axum::{extract::State, Extension, Json};

use crate::error::ApiError;
use crate::middleware::auth::AuthContext;
use crate::models::DetectionRecord;
use crate::state::AppState;

pub async fn list_detections(
    State(state): State<AppState>,
    Extension(ctx): Extension<AuthContext>,
) -> Result<Json<Vec<DetectionRecord>>, ApiError> {
    if !ctx.has_scope("read:detections") && !ctx.has_scope("read:all") {
        return Err(ApiError::BadRequest(
            "Insufficient scope: requires read:detections".to_string(),
        ));
    }

    let detections = state.detections.read().await;
    let list: Vec<DetectionRecord> = detections.values().cloned().collect();
    Ok(Json(list))
}
