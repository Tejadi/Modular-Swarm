use std::collections::HashMap;
use std::net::IpAddr;
use std::sync::Arc;

use axum::{
    extract::State,
    http::StatusCode,
    response::IntoResponse,
    Json,
};
use chrono::Utc;
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256; // used by HmacSha256
use tokio::sync::RwLock;
use tracing::{error, info, warn};

use crate::error::ApiError;
use crate::middleware::auth::AuthContext;
use crate::state::AppState;

/// Maximum webhooks per partner
const MAX_WEBHOOKS_PER_PARTNER: usize = 10;
/// Maximum total webhooks across all partners
const MAX_WEBHOOKS_TOTAL: usize = 1000;
/// Maximum URL length
const MAX_WEBHOOK_URL_LEN: usize = 2048;

type HmacSha256 = Hmac<Sha256>;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WebhookRegistration {
    pub url: String,
    pub events: Vec<String>,
    #[serde(default)]
    pub secret: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WebhookEntry {
    pub id: String,
    pub partner_id: String,
    pub url: String,
    pub events: Vec<String>,
    #[serde(skip_serializing)]
    pub secret: Option<String>,
    pub created_at: String,
}

#[derive(Debug, Serialize)]
pub struct WebhookPayload {
    pub event: String,
    pub timestamp: String,
    pub data: serde_json::Value,
}

const ALLOWED_EVENTS: &[&str] = &[
    "detection",
    "task_assigned",
    "task_completed",
    "mission_start",
    "mission_abort",
    "vehicle_online",
    "vehicle_offline",
];

pub type WebhookStore = Arc<RwLock<HashMap<String, WebhookEntry>>>;

pub fn new_store() -> WebhookStore {
    Arc::new(RwLock::new(HashMap::new()))
}

pub async fn register_webhook(
    State(state): State<AppState>,
    axum::Extension(ctx): axum::Extension<AuthContext>,
    Json(body): Json<WebhookRegistration>,
) -> Result<impl IntoResponse, ApiError> {
    // --- URL validation ---
    if body.url.is_empty() || body.url.len() > MAX_WEBHOOK_URL_LEN {
        return Err(ApiError::BadRequest(format!(
            "Webhook URL must be 1-{MAX_WEBHOOK_URL_LEN} characters"
        )));
    }
    if !body.url.starts_with("https://") {
        return Err(ApiError::BadRequest(
            "Webhook URL must use HTTPS".to_string(),
        ));
    }

    // SSRF protection: block private/loopback/link-local hosts
    validate_webhook_url(&body.url)?;

    for event in &body.events {
        if !ALLOWED_EVENTS.contains(&event.as_str()) {
            return Err(ApiError::BadRequest(format!(
                "Unknown event '{}'. Allowed: {:?}",
                event, ALLOWED_EVENTS
            )));
        }
    }

    let mut store = state.webhooks.write().await;

    // Total store cap
    if store.len() >= MAX_WEBHOOKS_TOTAL {
        return Err(ApiError::BadRequest(format!(
            "Maximum total webhooks ({MAX_WEBHOOKS_TOTAL}) reached"
        )));
    }

    // Per-partner cap
    let partner_count = store
        .values()
        .filter(|w| w.partner_id == ctx.partner_id)
        .count();
    if partner_count >= MAX_WEBHOOKS_PER_PARTNER {
        return Err(ApiError::BadRequest(format!(
            "Maximum webhooks per partner ({MAX_WEBHOOKS_PER_PARTNER}) reached"
        )));
    }

    let id = uuid::Uuid::new_v4().to_string();

    let entry = WebhookEntry {
        id: id.clone(),
        partner_id: ctx.partner_id.clone(),
        url: body.url,
        events: body.events,
        secret: body.secret,
        created_at: Utc::now().to_rfc3339(),
    };

    store.insert(id.clone(), entry);

    info!(
        partner_id = %ctx.partner_id,
        webhook_id = %id,
        "Webhook registered"
    );

    Ok((
        StatusCode::CREATED,
        Json(serde_json::json!({ "id": id, "status": "registered" })),
    ))
}

/// Block webhook URLs targeting private/loopback/link-local addresses (SSRF protection).
fn validate_webhook_url(url_str: &str) -> Result<(), ApiError> {
    // Parse host from URL
    let host = url_str
        .strip_prefix("https://")
        .and_then(|s| s.split('/').next())
        .and_then(|s| s.split(':').next())
        .unwrap_or("");

    if host.is_empty() {
        return Err(ApiError::BadRequest("Invalid webhook URL".to_string()));
    }

    // Block obvious hostnames
    let blocked_hosts = ["localhost", "127.0.0.1", "0.0.0.0", "[::1]", "::1"];
    let host_lower = host.to_lowercase();
    if blocked_hosts.contains(&host_lower.as_str()) {
        return Err(ApiError::BadRequest(
            "Webhook URL must not target localhost or loopback addresses".to_string(),
        ));
    }

    // Try to parse as IP directly
    if let Ok(ip) = host.parse::<IpAddr>() {
        if is_private_or_reserved(&ip) {
            return Err(ApiError::BadRequest(
                "Webhook URL must not target private or reserved IP addresses".to_string(),
            ));
        }
    }

    Ok(())
}

fn is_private_or_reserved(ip: &IpAddr) -> bool {
    match ip {
        IpAddr::V4(v4) => {
            v4.is_loopback()
                || v4.is_private()
                || v4.is_link_local()
                || v4.is_broadcast()
                || v4.is_unspecified()
                || (v4.octets()[0] == 169 && v4.octets()[1] == 254) // link-local
                || (v4.octets()[0] == 100 && (v4.octets()[1] & 0xC0) == 64) // CGN 100.64/10
        }
        IpAddr::V6(v6) => v6.is_loopback() || v6.is_unspecified(),
    }
}

pub async fn list_webhooks(
    State(state): State<AppState>,
    axum::Extension(ctx): axum::Extension<AuthContext>,
) -> Result<Json<Vec<WebhookEntry>>, ApiError> {
    let store = state.webhooks.read().await;
    let mine: Vec<WebhookEntry> = store
        .values()
        .filter(|w| w.partner_id == ctx.partner_id || ctx.has_scope("*"))
        .cloned()
        .collect();
    Ok(Json(mine))
}

pub async fn dispatch_event(
    webhooks: &WebhookStore,
    event: &str,
    data: serde_json::Value,
) {
    let store = webhooks.read().await;
    let matching: Vec<WebhookEntry> = store
        .values()
        .filter(|w| w.events.contains(&event.to_string()))
        .cloned()
        .collect();
    drop(store);

    if matching.is_empty() {
        return;
    }

    let payload = WebhookPayload {
        event: event.to_string(),
        timestamp: Utc::now().to_rfc3339(),
        data,
    };

    let body = match serde_json::to_string(&payload) {
        Ok(b) => b,
        Err(e) => {
            error!("Failed to serialize webhook payload: {e}");
            return;
        }
    };

    let client = reqwest::Client::new();

    let event_name = payload.event.clone();

    for hook in matching {
        let body = body.clone();
        let client = client.clone();
        let url = hook.url.clone();
        let evt = event_name.clone();

        tokio::spawn(async move {
            let mut req = client
                .post(&url)
                .header("Content-Type", "application/json")
                .header("X-Olympus-Event", &evt);

            if let Some(ref secret) = hook.secret {
                if let Ok(mut mac) = HmacSha256::new_from_slice(secret.as_bytes()) {
                    mac.update(body.as_bytes());
                    let sig = hex::encode(mac.finalize().into_bytes());
                    req = req.header("X-Olympus-Signature", sig);
                }
            }

            match req.body(body).send().await {
                Ok(resp) if resp.status().is_success() => {}
                Ok(resp) => {
                    warn!(
                        webhook_id = %hook.id,
                        status = %resp.status(),
                        "Webhook delivery failed"
                    );
                }
                Err(e) => {
                    warn!(
                        webhook_id = %hook.id,
                        error = %e,
                        "Webhook delivery error"
                    );
                }
            }
        });
    }
}
