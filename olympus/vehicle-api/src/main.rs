mod config;
mod error;
mod metrics;
mod middleware;
mod models;
mod routes;
mod state;
mod ws;
mod zenoh_client;

use std::net::SocketAddr;

use axum::{
    http::{header, HeaderValue, Method},
    routing::{get, post},
    Router,
};
use tower_http::cors::CorsLayer;
use tower_http::trace::TraceLayer;
use tracing::{error, info, warn};

use config::AppConfig;
use metrics::MetricsDb;
use state::AppState;
use zenoh_client::ZenohClient;

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "vehicle_api=info,tower_http=info".into()),
        )
        .with_target(true)
        .init();

    info!("OLYMPUS OS Vehicle API Gateway starting up");

    let config = AppConfig::from_env();
    info!(port = config.port, "Configuration loaded");

    if let Some(args) = std::env::args().nth(1) {
        if args == "--hash-key" {
            if let Some(raw) = std::env::args().nth(2) {
                println!("{}", middleware::auth::hash_key(&raw));
                return;
            }
            eprintln!("Usage: vehicle-api --hash-key <raw_key>");
            std::process::exit(1);
        }
    }

    let registry = middleware::auth::get_registry();
    if !registry.is_auth_enabled() {
        warn!("No API keys configured — authentication is DISABLED (dev mode)");
    }

    let zenoh_client = match ZenohClient::try_connect(&config).await {
        Some(client) => {
            info!("Zenoh connection established");
            client
        }
        None => {
            error!(
                "Could not connect to Zenoh at {:?}. \
                 The service will start but vehicle data will be unavailable \
                 until Zenoh becomes reachable.",
                config.zenoh_endpoints
            );
            match ZenohClient::connect_peer_mode(&config).await {
                Ok(client) => client,
                Err(e) => {
                    error!("Peer-mode Zenoh fallback also failed: {e}. Exiting.");
                    std::process::exit(1);
                }
            }
        }
    };

    let metrics_db = match MetricsDb::open("olympus_metrics.db") {
        Ok(db) => {
            info!("Metrics database initialized");
            Some(db)
        }
        Err(e) => {
            warn!("Failed to open metrics database: {e}. Metrics will be unavailable.");
            None
        }
    };

    if let Some(ref db) = metrics_db {
        let db = db.clone();
        tokio::spawn(async move {
            let mut interval = tokio::time::interval(tokio::time::Duration::from_secs(3600));
            loop {
                interval.tick().await;
                db.prune_older_than_days(7).await;
            }
        });
    }

    let app_state = AppState::new(zenoh_client, metrics_db);

    let zenoh_arc = app_state.zenoh.clone();
    zenoh_arc
        .spawn_subscribers(
            app_state.vehicles.clone(),
            app_state.detections.clone(),
            app_state.tasks.clone(),
            app_state.mission.clone(),
        )
        .await;

    let cors = if config.cors_allow_origin == "*" {
        error!("CORS is set to allow ALL origins — set CORS_ALLOW_ORIGIN in production!");
        CorsLayer::new()
            .allow_origin(tower_http::cors::Any)
            .allow_methods([Method::GET, Method::POST, Method::OPTIONS])
            .allow_headers([header::AUTHORIZATION, header::CONTENT_TYPE, header::ACCEPT])
    } else {
        let origin: HeaderValue = config
            .cors_allow_origin
            .parse()
            .expect("Invalid CORS_ALLOW_ORIGIN value");
        CorsLayer::new()
            .allow_origin(origin)
            .allow_methods([Method::GET, Method::POST, Method::OPTIONS])
            .allow_headers([header::AUTHORIZATION, header::CONTENT_TYPE, header::ACCEPT])
    };

    let protected_routes = Router::new()
        .route("/api/v1/vehicles", get(routes::vehicles::list_vehicles))
        .route("/api/v1/vehicles/{id}", get(routes::vehicles::get_vehicle))
        .route(
            "/api/v1/vehicles/{id}/command",
            post(routes::vehicles::send_command),
        )
        .route(
            "/api/v1/vehicles/register",
            post(routes::vehicles::register_vehicle),
        )
        .route(
            "/api/v1/vehicles/{id}/telemetry",
            post(routes::vehicles::ingest_telemetry),
        )
        .route(
            "/api/v1/vehicles/{id}/approve",
            post(routes::vehicles::approve_vehicle),
        )
        .route(
            "/api/v1/vehicles/{id}/revoke",
            post(routes::vehicles::revoke_vehicle),
        )
        .route(
            "/api/v1/vehicles/pending",
            get(routes::vehicles::list_pending_vehicles),
        )
        .route("/api/v1/mission", get(routes::mission::get_mission))
        .route("/api/v1/mission/start", post(routes::mission::start_mission))
        .route("/api/v1/mission/abort", post(routes::mission::abort_mission))
        .route(
            "/api/v1/detections",
            get(routes::detections::list_detections),
        )
        .route("/api/v1/advisor/chat", post(routes::advisor::chat))
        .route(
            "/api/v1/ws/telemetry",
            get(ws::telemetry_stream::telemetry_ws_handler),
        )
        .route(
            "/api/v1/metrics/telemetry",
            get(routes::metrics::query_telemetry),
        )
        .route(
            "/api/v1/metrics/detections/summary",
            get(routes::metrics::detection_summary),
        )
        .route(
            "/api/v1/swarmnet/status",
            get(routes::metrics::swarmnet_status),
        )
        .route(
            "/api/v1/ai-agent/status",
            get(routes::ai_agent::agent_status),
        )
        .route(
            "/api/v1/ai-agent/retrain",
            post(routes::ai_agent::force_retrain),
        )
        .route(
            "/api/v1/ai-agent/recall",
            post(routes::ai_agent::recall_fleet),
        )
        .route(
            "/api/v1/ai-agent/redeploy",
            post(routes::ai_agent::redeploy_fleet),
        )
        .route(
            "/api/v1/webhooks",
            post(routes::webhooks::register_webhook).get(routes::webhooks::list_webhooks),
        )
        .layer(axum::middleware::from_fn_with_state(
            app_state.clone(),
            middleware::auth::require_auth,
        ));

    let app = Router::new()
        .route("/api/v1/health", get(routes::health::health_check))
        .merge(protected_routes)
        .layer(axum::middleware::from_fn(middleware::rate_limit::rate_limit))
        .layer(cors)
        .layer(TraceLayer::new_for_http())
        .with_state(app_state);

    let addr = SocketAddr::from(([0, 0, 0, 0], config.port));
    info!("Listening on {addr}");

    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .expect("Failed to bind TCP listener");

    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await
        .expect("Server error");

    info!("Vehicle API Gateway shut down gracefully");
}

async fn shutdown_signal() {
    let ctrl_c = async {
        tokio::signal::ctrl_c()
            .await
            .expect("Failed to install Ctrl+C handler");
    };

    #[cfg(unix)]
    let terminate = async {
        tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            .expect("Failed to install SIGTERM handler")
            .recv()
            .await;
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => info!("Received Ctrl+C, shutting down..."),
        _ = terminate => info!("Received SIGTERM, shutting down..."),
    }
}
