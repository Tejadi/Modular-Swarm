use axum::{
    http::StatusCode,
    response::{IntoResponse, Response},
    Json,
};
use serde_json::json;
use tracing::warn;

#[derive(Debug, thiserror::Error)]
pub enum ApiError {
    #[error("Vehicle not found: {0}")]
    VehicleNotFound(String),

    #[error("Mission error: {0}")]
    MissionError(String),

    #[error("Zenoh communication error: {0}")]
    ZenohError(String),

    #[error("Invalid request: {0}")]
    BadRequest(String),

    #[error("Internal server error: {0}")]
    Internal(String),

    #[error("Serialization error: {0}")]
    SerializationError(#[from] serde_json::Error),
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        // Log the full error detail server-side, return sanitized message to client.
        // This prevents leaking entity IDs, internal paths, and serde details (CWE-209).
        let (status, client_message) = match &self {
            ApiError::VehicleNotFound(id) => {
                warn!(vehicle_id = %id, "Vehicle not found");
                (StatusCode::NOT_FOUND, "Vehicle not found".to_string())
            }
            ApiError::MissionError(detail) => {
                warn!(detail = %detail, "Mission error");
                (StatusCode::CONFLICT, format!("Mission error: {detail}"))
            }
            ApiError::ZenohError(detail) => {
                warn!(detail = %detail, "Zenoh communication error");
                (
                    StatusCode::BAD_GATEWAY,
                    "Service temporarily unavailable".to_string(),
                )
            }
            ApiError::BadRequest(msg) => {
                // Validation errors are safe to return (user-facing)
                (StatusCode::BAD_REQUEST, msg.clone())
            }
            ApiError::Internal(detail) => {
                warn!(detail = %detail, "Internal server error");
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "Internal server error".to_string(),
                )
            }
            ApiError::SerializationError(e) => {
                warn!(error = %e, "Serialization error");
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "Internal server error".to_string(),
                )
            }
        };

        let body = json!({
            "error": client_message,
            "status": status.as_u16(),
        });

        (status, Json(body)).into_response()
    }
}
