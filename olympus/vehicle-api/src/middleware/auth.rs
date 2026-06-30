use std::collections::HashSet;
use std::sync::OnceLock;

use axum::{
    body::Body,
    extract::{Request, State},
    http::{header, StatusCode},
    middleware::Next,
    response::{IntoResponse, Response},
    Json,
};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::json;
use sha2::{Digest, Sha256};
use tracing::warn;

use crate::state::AppState;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PartnerKey {
    pub id: String,
    pub org: String,
    pub key_hash: String,
    pub scopes: Vec<String>,
    #[serde(default)]
    pub expires_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone)]
pub struct AuthContext {
    pub partner_id: String,
    pub scopes: HashSet<String>,
}

impl AuthContext {
    pub fn has_scope(&self, scope: &str) -> bool {
        self.scopes.contains("*") || self.scopes.contains(scope)
    }
}

pub struct KeyRegistry {
    partners: Vec<PartnerKey>,
    legacy_hash: Option<String>,
}

fn sha256_hex(input: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(input.as_bytes());
    hex::encode(hasher.finalize())
}

pub fn hash_key(raw: &str) -> String {
    sha256_hex(raw)
}

fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for (x, y) in a.iter().zip(b.iter()) {
        diff |= x ^ y;
    }
    diff == 0
}

impl KeyRegistry {
    pub fn from_env() -> Self {
        let mut partners = Vec::new();

        if let Ok(path) = std::env::var("OLYMPUS_PARTNER_KEYS_FILE") {
            match std::fs::read_to_string(&path) {
                Ok(contents) => match serde_json::from_str::<Vec<PartnerKey>>(&contents) {
                    Ok(keys) => {
                        #[cfg(unix)]
                        {
                            use std::os::unix::fs::MetadataExt;
                            if let Ok(meta) = std::fs::metadata(&path) {
                                if meta.mode() & 0o077 != 0 {
                                    warn!(
                                        "Partner keys file {path} is world/group-readable — \
                                         restrict permissions with chmod 600"
                                    );
                                }
                            }
                        }
                        partners = keys;
                    }
                    Err(e) => warn!("Failed to parse partner keys file: {e}"),
                },
                Err(e) => warn!("Failed to read partner keys file {path}: {e}"),
            }
        }

        let legacy_hash = std::env::var("OLYMPUS_API_KEY")
            .ok()
            .filter(|k| !k.is_empty())
            .map(|k| sha256_hex(&k));

        Self {
            partners,
            legacy_hash,
        }
    }

    pub fn authenticate(&self, token: &str) -> Option<AuthContext> {
        let token_hash = sha256_hex(token);

        for partner in &self.partners {
            if !constant_time_eq(token_hash.as_bytes(), partner.key_hash.as_bytes()) {
                continue;
            }
            if let Some(exp) = partner.expires_at {
                if exp < Utc::now() {
                    continue;
                }
            }
            return Some(AuthContext {
                partner_id: partner.id.clone(),
                scopes: partner.scopes.iter().cloned().collect(),
            });
        }

        if let Some(ref legacy) = self.legacy_hash {
            if constant_time_eq(token_hash.as_bytes(), legacy.as_bytes()) {
                return Some(AuthContext {
                    partner_id: "operator".to_string(),
                    scopes: ["*".to_string()].into_iter().collect(),
                });
            }
        }

        None
    }

    pub fn is_auth_enabled(&self) -> bool {
        !self.partners.is_empty() || self.legacy_hash.is_some()
    }
}

static REGISTRY: OnceLock<KeyRegistry> = OnceLock::new();

pub fn get_registry() -> &'static KeyRegistry {
    REGISTRY.get_or_init(KeyRegistry::from_env)
}

fn extract_token(request: &Request<Body>) -> Option<String> {
    // SECURITY: Only accept tokens via Authorization header.
    // Query parameter tokens are disabled — they leak into server logs,
    // browser history, Referer headers, and proxy logs (CWE-614).
    request
        .headers()
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))
        .map(|t| t.to_string())
}

pub async fn require_auth(
    State(_state): State<AppState>,
    mut request: Request<Body>,
    next: Next,
) -> Response {
    let registry = get_registry();

    if !registry.is_auth_enabled() {
        request.extensions_mut().insert(AuthContext {
            partner_id: "dev".to_string(),
            scopes: ["*".to_string()].into_iter().collect(),
        });
        return next.run(request).await;
    }

    let token = match extract_token(&request) {
        Some(t) => t,
        None => {
            let body = json!({
                "error": "Unauthorized: missing API key",
                "status": 401,
            });
            return (StatusCode::UNAUTHORIZED, Json(body)).into_response();
        }
    };

    match registry.authenticate(&token) {
        Some(ctx) => {
            request.extensions_mut().insert(ctx);
            next.run(request).await
        }
        None => {
            let body = json!({
                "error": "Unauthorized: invalid API key",
                "status": 401,
            });
            (StatusCode::UNAUTHORIZED, Json(body)).into_response()
        }
    }
}
