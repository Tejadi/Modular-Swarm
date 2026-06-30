pub mod bridge;
pub mod config;
pub mod elrs;
pub mod lora;
pub mod mavlink_handler;
pub mod protocol;
pub mod telemetry;
pub mod zenoh_handler;

pub use bridge::{BridgeMessage, BridgeState, OlympusBridge};
pub use config::BridgeConfig;
pub use elrs::{ElrsBridge, CrsfFrame, ElrsLinkStats, ElrsGps};
pub use lora::{LoRaBridge, MeshPacket};
pub use protocol::{
    CommandMessage, Detection, DetectionType, DroneRole, DroneStatus, DroneTelemetry,
    GeoPosition, Task, TaskBid, TaskState, TaskType,
    TrustTier, CommandAuthority, RegistrationStatus, CapabilityManifest,
};
pub use mavlink_handler::{MavlinkCommand, MavlinkEvent, MavlinkHandler, MavlinkTelemetry};
pub use telemetry::{TelemetryFilter, TelemetryService, TelemetrySource};
pub use zenoh_handler::ZenohHandler;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");

pub const NAME: &str = env!("CARGO_PKG_NAME");

pub fn init_logging() {
    use tracing_subscriber::{fmt, prelude::*, EnvFilter};

    tracing_subscriber::registry()
        .with(fmt::layer().with_target(true))
        .with(EnvFilter::from_default_env())
        .try_init()
        .ok();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_version() {
        assert!(!VERSION.is_empty());
    }

    #[test]
    fn test_geo_distance() {
        let pos1 = GeoPosition {
            latitude: 42.3601,
            longitude: -71.0589,
            altitude: 0.0,
            heading: 0.0,
        };
        let pos2 = GeoPosition {
            latitude: 42.3701,
            longitude: -71.0589,
            altitude: 0.0,
            heading: 0.0,
        };

        let distance = pos1.distance_to(&pos2);
        assert!(distance > 1000.0 && distance < 1200.0);
    }

    #[test]
    fn test_telemetry_compact_roundtrip() {
        let telemetry = DroneTelemetry {
            drone_id: "scout_01".to_string(),
            role: DroneRole::Scout,
            status: DroneStatus::Scanning,
            position: GeoPosition {
                latitude: 42.3601,
                longitude: -71.0589,
                altitude: 35.0,
                heading: 90.0,
            },
            battery: protocol::BatteryState {
                percentage: 85,
                ..Default::default()
            },
            timestamp: chrono::Utc::now(),
            mesh_rssi: -65,
            wifi_connected: true,
            current_task_id: None,
        };

        let bytes = telemetry.to_compact_bytes();
        let decoded = DroneTelemetry::from_compact_bytes(&bytes).unwrap();

        assert_eq!(telemetry.drone_id, decoded.drone_id);
        assert_eq!(telemetry.role, decoded.role);
        assert_eq!(telemetry.status, decoded.status);
        assert!((telemetry.position.latitude - decoded.position.latitude).abs() < 0.0001);
    }
}
