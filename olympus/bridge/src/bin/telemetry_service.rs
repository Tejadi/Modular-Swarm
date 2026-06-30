use anyhow::{Context, Result};
use std::sync::Arc;
use tracing::{info, Level};
use tracing_subscriber::{fmt, prelude::*, EnvFilter};

use olympus_bridge::config::BridgeConfig;
use olympus_bridge::protocol::keys;
use olympus_bridge::telemetry::{TelemetryService, MockTelemetrySource, TelemetryFilter};

use zenoh::prelude::r#async::*;

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::registry()
        .with(fmt::layer())
        .with(EnvFilter::from_default_env()
            .add_directive(Level::INFO.into()))
        .init();

    info!("OLYMPUS Telemetry Service starting...");

    let config = BridgeConfig::load()?;

    let source = Arc::new(MockTelemetrySource::new(45.5231, -122.6765));

    let service = TelemetryService::new(
        config.drone_id.clone(),
        config.role,
        source,
    );

    let zenoh_config = zenoh::prelude::Config::default();
    let session = zenoh::open(zenoh_config)
        .res()
        .await
        .map_err(|e| anyhow::anyhow!(e.to_string()))
        .context("Failed to open Zenoh session")?;

    let mut filter = TelemetryFilter::new(
        config.telemetry.position_delta_threshold,
        config.telemetry.battery_delta_threshold,
    );

    let key = keys::telemetry(&config.drone_id);
    let publisher = session
        .declare_publisher(&key)
        .res()
        .await
        .map_err(|e| anyhow::anyhow!(e.to_string()))
        .context("Failed to declare Zenoh publisher")?;

    info!("Publishing telemetry to {}", key);

    let period = tokio::time::Duration::from_secs_f32(1.0 / config.telemetry.full_rate_hz);
    let mut ticker = tokio::time::interval(period);

    loop {
        ticker.tick().await;

        let telem = service.update().await?;

        if filter.should_transmit(&telem) {
            let payload = serde_json::to_vec(&telem)?;

            publisher
                .put(payload)
                .res()
                .await
                .map_err(|e| anyhow::anyhow!(e.to_string()))?;

            info!(
                "Telemetry: pos=({:.5}, {:.5}, {:.1}m) bat={}%",
                telem.position.latitude,
                telem.position.longitude,
                telem.position.altitude,
                telem.battery.percentage
            );
        }
    }
}
