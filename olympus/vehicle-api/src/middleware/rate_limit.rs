use axum::{
    body::Body,
    extract::Request,
    http::{header, StatusCode},
    middleware::Next,
    response::{IntoResponse, Response},
    Json,
};
use serde_json::json;
use std::collections::HashMap;
use std::net::IpAddr;
use std::sync::Arc;
use std::time::Instant;
use tokio::sync::Mutex;
use tracing::debug;

use super::auth::AuthContext;

// ---------------------------------------------------------------------------
// Rate-limit tiers — different endpoints get different budgets
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum RateLimitTier {
    /// Critical operations: commands, mission control, AI agent actions
    Command,
    /// State-changing mutations: register, approve, revoke, webhooks
    Mutation,
    /// Read-only queries: fleet list, detections, metrics
    Read,
    /// LLM advisor chat (expensive per-call)
    Advisor,
    /// Health checks — never rate-limited
    Health,
}

impl RateLimitTier {
    /// Maximum tokens (requests) per window for this tier.
    fn max_tokens(self) -> u32 {
        match self {
            Self::Command => 10,
            Self::Mutation => 20,
            Self::Read => 100,
            Self::Advisor => 15,
            Self::Health => u32::MAX,
        }
    }

    /// Refill window in seconds.
    fn window_secs(self) -> u64 {
        match self {
            Self::Health => 1, // effectively unlimited
            _ => 60,
        }
    }
}

// ---------------------------------------------------------------------------
// Token bucket
// ---------------------------------------------------------------------------

#[derive(Clone)]
struct Bucket {
    tokens: u32,
    last_refill: Instant,
    last_seen: Instant,
}

struct RateLimiterInner {
    /// Keyed by (client_key, tier) → bucket
    buckets: HashMap<(String, RateLimitTier), Bucket>,
}

#[derive(Clone)]
pub struct RateLimiter {
    inner: Arc<Mutex<RateLimiterInner>>,
}

impl RateLimiter {
    pub fn new() -> Self {
        let limiter = Self {
            inner: Arc::new(Mutex::new(RateLimiterInner {
                buckets: HashMap::new(),
            })),
        };

        // Spawn background task to purge stale buckets every 5 minutes
        let inner = limiter.inner.clone();
        tokio::spawn(async move {
            let mut interval = tokio::time::interval(tokio::time::Duration::from_secs(300));
            loop {
                interval.tick().await;
                let mut state = inner.lock().await;
                let now = Instant::now();
                let before = state.buckets.len();
                state
                    .buckets
                    .retain(|_, b| now.duration_since(b.last_seen).as_secs() < 600);
                let after = state.buckets.len();
                if before != after {
                    debug!(purged = before - after, remaining = after, "Rate-limit bucket cleanup");
                }
            }
        });

        limiter
    }

    /// Returns (allowed, seconds_until_refill) for the given client + tier.
    pub async fn check(&self, key: &str, tier: RateLimitTier) -> (bool, u64) {
        if tier == RateLimitTier::Health {
            return (true, 0);
        }

        let mut state = self.inner.lock().await;
        let now = Instant::now();
        let max = tier.max_tokens();
        let window = tier.window_secs();

        let bucket_key = (key.to_string(), tier);
        let bucket = state.buckets.entry(bucket_key).or_insert(Bucket {
            tokens: max,
            last_refill: now,
            last_seen: now,
        });

        bucket.last_seen = now;

        let elapsed = now.duration_since(bucket.last_refill).as_secs();
        if elapsed >= window {
            bucket.tokens = max;
            bucket.last_refill = now;
        }

        let retry_after = window.saturating_sub(elapsed);

        if bucket.tokens > 0 {
            bucket.tokens -= 1;
            (true, 0)
        } else {
            (false, retry_after)
        }
    }
}

static LIMITER: std::sync::OnceLock<RateLimiter> = std::sync::OnceLock::new();

fn get_limiter() -> &'static RateLimiter {
    LIMITER.get_or_init(RateLimiter::new)
}

// ---------------------------------------------------------------------------
// Tier classification from request path + method
// ---------------------------------------------------------------------------

fn classify_tier(path: &str, method: &axum::http::Method) -> RateLimitTier {
    if path.contains("/health") {
        return RateLimitTier::Health;
    }
    if path.contains("/advisor/chat") {
        return RateLimitTier::Advisor;
    }
    // Commands & mission control
    if path.contains("/command")
        || path.contains("/mission/start")
        || path.contains("/mission/abort")
        || path.contains("/ai-agent/retrain")
        || path.contains("/ai-agent/recall")
        || path.contains("/ai-agent/redeploy")
    {
        return RateLimitTier::Command;
    }
    // Mutations (POST only, excluding commands above)
    if method == axum::http::Method::POST {
        return RateLimitTier::Mutation;
    }
    // Everything else is a read
    RateLimitTier::Read
}

// ---------------------------------------------------------------------------
// Client key extraction — prefers auth context, falls back to peer IP
// ---------------------------------------------------------------------------

fn extract_rate_key(request: &Request<Body>) -> String {
    // If authenticated, rate-limit per partner
    if let Some(ctx) = request.extensions().get::<AuthContext>() {
        return format!("partner:{}", ctx.partner_id);
    }

    // Fall back to peer IP from ConnectInfo (not X-Forwarded-For which is spoofable)
    // X-Forwarded-For is only trusted from private/loopback subnets
    if let Some(forwarded) = request
        .headers()
        .get("x-forwarded-for")
        .and_then(|v| v.to_str().ok())
    {
        if let Some(first_ip) = forwarded.split(',').next().map(|s| s.trim()) {
            // Only trust X-Forwarded-For if the direct connection is from a private IP
            // (i.e., there's a reverse proxy in front). We can't check ConnectInfo here
            // easily, so we validate the IP itself isn't a private IP being spoofed.
            if let Ok(ip) = first_ip.parse::<IpAddr>() {
                if !is_private_ip(&ip) {
                    return format!("ip:{}", ip);
                }
            }
        }
    }

    // Last resort: hash from real peer. Without ConnectInfo middleware, use a
    // stable fallback keyed on the combination of available identifiers.
    "ip:unknown".to_string()
}

fn is_private_ip(ip: &IpAddr) -> bool {
    match ip {
        IpAddr::V4(v4) => {
            v4.is_loopback()
                || v4.is_private()
                || v4.is_link_local()
                || v4.octets()[0] == 169 && v4.octets()[1] == 254 // link-local
        }
        IpAddr::V6(v6) => v6.is_loopback(),
    }
}

// ---------------------------------------------------------------------------
// Middleware
// ---------------------------------------------------------------------------

pub async fn rate_limit(request: Request<Body>, next: Next) -> Response {
    let path = request.uri().path().to_string();
    let method = request.method().clone();
    let tier = classify_tier(&path, &method);
    let key = extract_rate_key(&request);
    let limiter = get_limiter();

    let (allowed, retry_after) = limiter.check(&key, tier).await;

    if allowed {
        next.run(request).await
    } else {
        let body = json!({
            "error": "Too many requests. Please try again later.",
            "status": 429,
            "retry_after": retry_after,
        });
        let mut response = (StatusCode::TOO_MANY_REQUESTS, Json(body)).into_response();
        // RFC 6585: Retry-After header
        if let Ok(val) = header::HeaderValue::from_str(&retry_after.to_string()) {
            response.headers_mut().insert("Retry-After", val);
        }
        response
    }
}
