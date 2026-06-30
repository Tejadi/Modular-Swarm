use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::path::Path;

use crate::protocol::DroneRole;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BridgeConfig {
    pub drone_id: String,
    pub role: DroneRole,
    pub zenoh: ZenohConfig,
    pub lora: LoRaConfig,
    pub elrs: ElrsConfig,
    pub mavlink: MavlinkConfig,
    pub telemetry: TelemetryConfig,
    pub altitude: AltitudeConfig,
    pub resilience: ResilienceConfig,
}

impl Default for BridgeConfig {
    fn default() -> Self {
        Self {
            drone_id: "drone_00".to_string(),
            role: DroneRole::Scout,
            zenoh: ZenohConfig::default(),
            lora: LoRaConfig::default(),
            elrs: ElrsConfig::default(),
            mavlink: MavlinkConfig::default(),
            telemetry: TelemetryConfig::default(),
            altitude: AltitudeConfig::default(),
            resilience: ResilienceConfig::default(),
        }
    }
}

impl BridgeConfig {
    pub fn load() -> Result<Self> {
        let config_path = std::env::var("OLYMPUS_CONFIG")
            .unwrap_or_else(|_| "/etc/olympus/bridge.toml".to_string());

        if Path::new(&config_path).exists() {
            Self::from_file(&config_path)
        } else {
            Self::from_env()
        }
    }

    pub fn from_file(path: &str) -> Result<Self> {
        let content = std::fs::read_to_string(path)
            .with_context(|| format!("Failed to read config file: {}", path))?;

        toml::from_str(&content)
            .with_context(|| "Failed to parse config file")
    }

    pub fn from_env() -> Result<Self> {
        Ok(Self {
            drone_id: std::env::var("OLYMPUS_DRONE_ID")
                .unwrap_or_else(|_| "drone_00".to_string()),
            role: match std::env::var("OLYMPUS_ROLE").as_deref() {
                Ok("executor") => DroneRole::Executor,
                _ => DroneRole::Scout,
            },
            zenoh: ZenohConfig::from_env(),
            lora: LoRaConfig::from_env(),
            elrs: ElrsConfig::from_env(),
            mavlink: MavlinkConfig::from_env(),
            telemetry: TelemetryConfig::default(),
            altitude: AltitudeConfig::default(),
            resilience: ResilienceConfig::default(),
        })
    }

    pub fn default_config_toml() -> String {
        let config = Self::default();
        toml::to_string_pretty(&config).unwrap()
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ZenohConfig {
    pub mode: String,
    pub connect_endpoints: Vec<String>,
    pub listen_endpoints: Vec<String>,
    pub rest_enabled: bool,
    pub rest_port: u16,
    pub allowed_publish: Vec<String>,
    pub allowed_subscribe: Vec<String>,
    pub max_publish_frequency: f32,
}

impl Default for ZenohConfig {
    fn default() -> Self {
        Self {
            mode: "peer".to_string(),
            connect_endpoints: vec![],
            listen_endpoints: vec!["tcp/0.0.0.0:7447".to_string()],
            rest_enabled: true,
            rest_port: 8000,
            allowed_publish: vec![
                "olympus/swarm/*/telemetry".to_string(),
                "olympus/swarm/*/heartbeat".to_string(),
                "olympus/detection/**".to_string(),
                "olympus/task/**".to_string(),
            ],
            allowed_subscribe: vec![
                "olympus/command/**".to_string(),
                "olympus/zone/**".to_string(),
                "olympus/task/**".to_string(),
            ],
            max_publish_frequency: 10.0,
        }
    }
}

impl ZenohConfig {
    pub fn from_env() -> Self {
        let mut config = Self::default();

        if let Ok(mode) = std::env::var("ZENOH_MODE") {
            config.mode = mode;
        }

        if let Ok(endpoints) = std::env::var("ZENOH_CONNECT") {
            config.connect_endpoints = endpoints.split(',').map(|s| s.to_string()).collect();
        }

        if let Ok(port) = std::env::var("ZENOH_REST_PORT") {
            if let Ok(p) = port.parse() {
                config.rest_port = p;
            }
        }

        config
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LoRaConfig {
    pub enabled: bool,
    pub serial_port: String,
    pub baud_rate: u32,
    pub channel: u8,
    pub hop_limit: u8,
    pub tx_power: i8,
    pub modem_preset: String,
    pub max_packet_size: usize,
    pub heartbeat_interval_secs: u64,
}

impl Default for LoRaConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            serial_port: "/dev/ttyUSB0".to_string(),
            baud_rate: 115200,
            channel: 0,
            hop_limit: 3,
            tx_power: 22,
            modem_preset: "MEDIUM_FAST".to_string(),
            max_packet_size: 200,
            heartbeat_interval_secs: 5,
        }
    }
}

impl LoRaConfig {
    pub fn from_env() -> Self {
        let mut config = Self::default();

        if let Ok(port) = std::env::var("LORA_SERIAL_PORT") {
            config.serial_port = port;
        }

        if let Ok(enabled) = std::env::var("LORA_ENABLED") {
            config.enabled = enabled == "true" || enabled == "1";
        }

        config
    }
}

/// ExpressLRS (ELRS) 2.4GHz radio configuration.
/// Happymodel EP1/EP2 RX uses CRSF protocol over UART at 420000 baud.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ElrsConfig {
    pub enabled: bool,
    pub serial_port: String,
    pub baud_rate: u32,
    pub heartbeat_interval_secs: u64,
    /// Maximum CRSF payload size (protocol max is 60 bytes)
    pub max_payload_size: usize,
}

impl Default for ElrsConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            serial_port: "/dev/ttyUSB1".to_string(),
            baud_rate: 420000, // CRSF standard baud for ELRS
            heartbeat_interval_secs: 2,
            max_payload_size: 60,
        }
    }
}

impl ElrsConfig {
    pub fn from_env() -> Self {
        let mut config = Self::default();

        if let Ok(enabled) = std::env::var("ELRS_ENABLED") {
            config.enabled = enabled == "true" || enabled == "1";
        }

        if let Ok(port) = std::env::var("ELRS_SERIAL_PORT") {
            config.serial_port = port;
        }

        if let Ok(baud) = std::env::var("ELRS_BAUD_RATE") {
            if let Ok(b) = baud.parse() {
                config.baud_rate = b;
            }
        }

        config
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TelemetryConfig {
    pub full_rate_hz: f32,
    pub heartbeat_rate_hz: f32,
    pub battery_delta_threshold: u8,
    pub position_delta_threshold: f64,
    pub include_velocity: bool,
}

impl Default for TelemetryConfig {
    fn default() -> Self {
        Self {
            full_rate_hz: 5.0,
            heartbeat_rate_hz: 0.5,
            battery_delta_threshold: 1,
            position_delta_threshold: 0.5,
            include_velocity: true,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AltitudeConfig {
    pub scout_altitude: f64,
    pub executor_work_altitude: f64,
    pub executor_transit_altitude: f64,
    pub transit_ceiling: f64,
    pub min_separation: f64,
}

impl Default for AltitudeConfig {
    fn default() -> Self {
        Self {
            scout_altitude: 35.0,
            executor_work_altitude: 5.0,
            executor_transit_altitude: 50.0,
            transit_ceiling: 60.0,
            min_separation: 10.0,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MavlinkConfig {
    pub enabled: bool,
    pub connection_string: String,
    pub system_id: u8,
    pub component_id: u8,
    pub heartbeat_interval_ms: u64,
    pub stream_rate_hz: u8,
}

impl Default for MavlinkConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            connection_string: "serial:/dev/ttyTHS1:57600".to_string(),
            system_id: 1,
            component_id: 191,
            heartbeat_interval_ms: 1000,
            stream_rate_hz: 4,
        }
    }
}

impl MavlinkConfig {
    pub fn from_env() -> Self {
        let mut config = Self::default();

        if let Ok(enabled) = std::env::var("MAVLINK_ENABLED") {
            config.enabled = enabled == "true" || enabled == "1";
        }

        if let Ok(conn) = std::env::var("MAVLINK_CONNECTION") {
            config.connection_string = conn;
        }

        if let Ok(sys_id) = std::env::var("MAVLINK_SYSTEM_ID") {
            if let Ok(id) = sys_id.parse() {
                config.system_id = id;
            }
        }

        if let Ok(comp_id) = std::env::var("MAVLINK_COMPONENT_ID") {
            if let Ok(id) = comp_id.parse() {
                config.component_id = id;
            }
        }

        if let Ok(hb) = std::env::var("MAVLINK_HEARTBEAT_MS") {
            if let Ok(ms) = hb.parse() {
                config.heartbeat_interval_ms = ms;
            }
        }

        if let Ok(rate) = std::env::var("MAVLINK_STREAM_RATE") {
            if let Ok(r) = rate.parse() {
                config.stream_rate_hz = r;
            }
        }

        config
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ResilienceConfig {
    pub wifi_timeout_secs: u64,
    pub peer_timeout_secs: u64,
    pub command_ack_timeout_secs: u64,
    pub command_retries: u8,
    pub auto_rtl_on_disconnect: bool,
    pub rtl_timeout_secs: u64,
}

impl Default for ResilienceConfig {
    fn default() -> Self {
        Self {
            wifi_timeout_secs: 10,
            peer_timeout_secs: 30,
            command_ack_timeout_secs: 5,
            command_retries: 3,
            auto_rtl_on_disconnect: true,
            rtl_timeout_secs: 60,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_config() {
        let config = BridgeConfig::default();
        assert_eq!(config.drone_id, "drone_00");
        assert_eq!(config.role, DroneRole::Scout);
    }

    #[test]
    fn test_config_toml_generation() {
        let toml = BridgeConfig::default_config_toml();
        assert!(toml.contains("drone_id"));
        println!("{}", toml);
    }
}
