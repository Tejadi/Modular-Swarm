use axum::{extract::State, http::StatusCode, response::IntoResponse, Json};
use serde::{Deserialize, Serialize};
use serde_json::json;
use tracing::{error, warn};

use crate::state::AppState;

#[derive(Deserialize)]
pub struct ChatRequest {
    message: String,
}

#[derive(Serialize, Deserialize)]
pub struct ChatResponse {
    response: String,
    #[serde(default)]
    sources: Vec<String>,
}

fn advisor_url() -> String {
    std::env::var("ADVISOR_URL").unwrap_or_else(|_| "http://localhost:8080".to_string())
}

pub async fn chat(
    State(_state): State<AppState>,
    Json(payload): Json<ChatRequest>,
) -> impl IntoResponse {
    let message = payload.message.trim().to_string();

    if message.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "Message is required", "status": 400 })),
        )
            .into_response();
    }

    if message.len() > 2000 {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "Message too long (max 2000 characters)", "status": 400 })),
        )
            .into_response();
    }

    let url = format!("{}/api/advisor/chat", advisor_url());

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .unwrap_or_default();

    match client
        .post(&url)
        .json(&json!({ "message": message }))
        .send()
        .await
    {
        Ok(resp) => {
            if resp.status().is_success() {
                match resp.json::<ChatResponse>().await {
                    Ok(body) => Json(json!({
                        "response": body.response,
                        "sources": body.sources,
                    }))
                    .into_response(),
                    Err(e) => {
                        error!("Failed to parse advisor response: {e}");
                        (
                            StatusCode::BAD_GATEWAY,
                            Json(json!({ "error": "Invalid response from advisor", "status": 502 })),
                        )
                            .into_response()
                    }
                }
            } else {
                let status = resp.status().as_u16();
                let body = resp.text().await.unwrap_or_default();
                warn!("Advisor returned error {status}: {body}");
                (
                    StatusCode::BAD_GATEWAY,
                    Json(json!({ "error": format!("Advisor error: {status}"), "status": 502 })),
                )
                    .into_response()
            }
        }
        Err(e) => {
            error!("Failed to reach advisor service: {e}");
            (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(json!({
                    "error": "Advisor service is unavailable. Ensure the Python brain is running.",
                    "status": 503
                })),
            )
                .into_response()
        }
    }
}
