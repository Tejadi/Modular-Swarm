mod bridge;
mod config;
mod elrs;
mod lora;
mod protocol;
mod telemetry;
mod zenoh_handler;

use anyhow::Result;
use tracing::{info, warn, error, Level};
use tracing_subscriber::{fmt, prelude::*, EnvFilter};
use std::sync::Arc;
use tokio::sync::RwLock;

use crate::bridge::OlympusBridge;
use crate::config::BridgeConfig;

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::registry()
        .with(fmt::layer().with_target(true))
        .with(EnvFilter::from_default_env()
            .add_directive(Level::INFO.into()))
        .init();

    info!("╔══════════════════════════════════════════════════════════════╗");
    info!("║           OLYMPUS OS BRIDGE v0.1.0                           ║");
    info!("║   Omniscient Logistics Yielding Modular Platform for UAS     ║");
    info!("╚══════════════════════════════════════════════════════════════╝");

    let config = BridgeConfig::load()?;
    info!("Configuration loaded for drone: {}", config.drone_id);
    info!("Role: {:?}", config.role);

    let shared_state = Arc::new(RwLock::new(bridge::BridgeState::default()));

    let bridge = OlympusBridge::new(config, shared_state).await?;

    info!("OLYMPUS BRIDGE: ONLINE");
    info!("Bridging Zenoh <-> LoRa <-> ELRS <-> MAVLink");

    bridge.run().await?;

    info!("OLYMPUS BRIDGE: SHUTDOWN COMPLETE");
    Ok(())
}
