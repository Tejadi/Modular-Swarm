use std::env;

#[derive(Debug, Clone)]
pub struct AppConfig {
    pub port: u16,
    pub zenoh_endpoints: Vec<String>,
    pub cors_allow_origin: String,
    pub ws_broadcast_capacity: usize,
}

impl AppConfig {
    pub fn from_env() -> Self {
        let port = env::var("VEHICLE_API_PORT")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(3001);

        let zenoh_endpoints = env::var("ZENOH_ENDPOINTS")
            .unwrap_or_else(|_| "tcp/localhost:7447".to_string())
            .split(',')
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .collect();

        // Default to dashboard origin; wildcard "*" is insecure in production.
        // Set CORS_ALLOW_ORIGIN explicitly for non-local deployments.
        let cors_allow_origin =
            env::var("CORS_ALLOW_ORIGIN").unwrap_or_else(|_| "http://localhost:3000".to_string());

        let ws_broadcast_capacity = env::var("WS_BROADCAST_CAPACITY")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(256);

        Self {
            port,
            zenoh_endpoints,
            cors_allow_origin,
            ws_broadcast_capacity,
        }
    }
}
