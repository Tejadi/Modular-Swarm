use anyhow::{Context, Result};
use std::sync::Arc;
use tokio::sync::mpsc;
use tracing::{info, warn, error, debug};
use zenoh::prelude::r#async::*;
use zenoh::config::{Config, WhatAmI};

use crate::config::ZenohConfig;
use crate::bridge::BridgeMessage;
use crate::protocol::{DroneTelemetry, CommandMessage, Detection, Task, keys};

const CONTENT_JSON: u8 = 0x01;
const CONTENT_MSGPACK: u8 = 0x02;

fn decode_payload<T: serde::de::DeserializeOwned>(data: &[u8]) -> Option<T> {
    if data.is_empty() {
        return None;
    }
    match data[0] {
        CONTENT_MSGPACK => rmp_serde::from_slice(&data[1..]).ok(),
        CONTENT_JSON => serde_json::from_slice(&data[1..]).ok(),
        _ => serde_json::from_slice(data).ok(),
    }
}

pub fn encode_msgpack<T: serde::Serialize>(value: &T) -> Vec<u8> {
    let mut buf = vec![CONTENT_MSGPACK];
    if let Ok(packed) = rmp_serde::to_vec(value) {
        buf.extend_from_slice(&packed);
    }
    buf
}

pub fn encode_json<T: serde::Serialize>(value: &T) -> Vec<u8> {
    let mut buf = vec![CONTENT_JSON];
    if let Ok(json) = serde_json::to_vec(value) {
        buf.extend_from_slice(&json);
    }
    buf
}

pub struct ZenohHandler {
    session: Arc<Session>,
    #[allow(dead_code)]
    subscribers: Vec<zenoh::subscriber::Subscriber<'static, ()>>,
}

impl ZenohHandler {
    pub async fn new(
        config: &ZenohConfig,
        message_tx: mpsc::Sender<BridgeMessage>,
    ) -> Result<Self> {
        let mut zenoh_config = Config::default();

        match config.mode.as_str() {
            "peer" => zenoh_config.set_mode(Some(WhatAmI::Peer)).unwrap(),
            "client" => zenoh_config.set_mode(Some(WhatAmI::Client)).unwrap(),
            "router" => zenoh_config.set_mode(Some(WhatAmI::Router)).unwrap(),
            _ => zenoh_config.set_mode(Some(WhatAmI::Peer)).unwrap(),
        };

        if !config.connect_endpoints.is_empty() {
            zenoh_config.connect.endpoints = config.connect_endpoints
                .iter()
                .map(|s| s.parse().expect("Invalid connect endpoint"))
                .collect();
        }

        if !config.listen_endpoints.is_empty() {
            zenoh_config.listen.endpoints = config.listen_endpoints
                .iter()
                .map(|s| s.parse().expect("Invalid listen endpoint"))
                .collect();
        }

        let session = zenoh::open(zenoh_config)
            .res()
            .await
            .map_err(|e| anyhow::anyhow!(e.to_string()))
            .context("Failed to open Zenoh session")?;
        let session = Arc::new(session);

        info!("Zenoh session opened in {} mode", config.mode);

        let mut subscribers = Vec::new();

        let tx = message_tx.clone();
        let sub = session
            .declare_subscriber(keys::telemetry_wildcard())
            .callback(move |sample| {
                let data: Vec<u8> = (&sample.value).try_into().unwrap_or_default();
                if let Some(telem) = decode_payload::<DroneTelemetry>(&data) {
                    let _ = tx.try_send(BridgeMessage::SwarmUpdate(telem));
                }
            })
            .res()
            .await
            .map_err(|e| anyhow::anyhow!(e.to_string()))
            .context("Failed to create telemetry subscriber")?;
        subscribers.push(sub);
        debug!("Subscribed to {}", keys::telemetry_wildcard());

        let tx = message_tx.clone();
        let sub = session
            .declare_subscriber(keys::command_broadcast())
            .callback(move |sample| {
                let data: Vec<u8> = (&sample.value).try_into().unwrap_or_default();
                if let Some(cmd) = decode_payload::<CommandMessage>(&data) {
                    let _ = tx.try_send(BridgeMessage::CommandReceived(cmd));
                }
            })
            .res()
            .await
            .map_err(|e| anyhow::anyhow!(e.to_string()))
            .context("Failed to create command subscriber")?;
        subscribers.push(sub);
        debug!("Subscribed to {}", keys::command_broadcast());

        let tx = message_tx.clone();
        let sub = session
            .declare_subscriber(keys::task_auction())
            .callback(move |sample| {
                let data: Vec<u8> = (&sample.value).try_into().unwrap_or_default();
                if let Some(task) = decode_payload::<Task>(&data) {
                    let _ = tx.try_send(BridgeMessage::TaskReceived(task));
                }
            })
            .res()
            .await
            .map_err(|e| anyhow::anyhow!(e.to_string()))
            .context("Failed to create task subscriber")?;
        subscribers.push(sub);
        debug!("Subscribed to {}", keys::task_auction());

        let tx = message_tx.clone();
        let sub = session
            .declare_subscriber(keys::detection_all())
            .callback(move |sample| {
                let data: Vec<u8> = (&sample.value).try_into().unwrap_or_default();
                if let Some(det) = decode_payload::<Detection>(&data) {
                    let _ = tx.try_send(BridgeMessage::DetectionEvent(det));
                }
            })
            .res()
            .await
            .map_err(|e| anyhow::anyhow!(e.to_string()))
            .context("Failed to create detection subscriber")?;
        subscribers.push(sub);
        debug!("Subscribed to {}", keys::detection_all());

        Ok(Self { session, subscribers })
    }

    pub async fn publish(&self, key: &str, payload: Vec<u8>) -> Result<()> {
        self.session
            .put(key, payload)
            .res()
            .await
            .map_err(|e| anyhow::anyhow!(e.to_string()))
            .context("Failed to publish to Zenoh")?;
        Ok(())
    }

    pub async fn get(&self, key: &str) -> Result<Option<Vec<u8>>> {
        let replies = self.session
            .get(key)
            .res()
            .await
            .map_err(|e| anyhow::anyhow!(e.to_string()))
            .context("Failed to query Zenoh")?;

        while let Ok(reply) = replies.recv_async().await {
            match reply.sample {
                Ok(sample) => {
                    let data: Vec<u8> = (&sample.value).try_into().unwrap_or_default();
                    return Ok(Some(data));
                }
                Err(e) => {
                    warn!("Query error: {:?}", e);
                }
            }
        }

        Ok(None)
    }

    pub async fn delete(&self, key: &str) -> Result<()> {
        self.session
            .delete(key)
            .res()
            .await
            .map_err(|e| anyhow::anyhow!(e.to_string()))
            .context("Failed to delete from Zenoh")?;
        Ok(())
    }
}

pub struct Ros2BridgeConfig {
    pub domain_id: u32,
    pub allow_pub: Vec<String>,
    pub allow_sub: Vec<String>,
    pub max_frequencies: Vec<(String, f32)>,
}

impl Default for Ros2BridgeConfig {
    fn default() -> Self {
        Self {
            domain_id: 0,
            allow_pub: vec![
                "*/battery_state".to_string(),
                "*/vehicle_status".to_string(),
                "*/global_position".to_string(),
                "*/mission_result".to_string(),
            ],
            allow_sub: vec![
                "*/cmd_vel".to_string(),
                "*/mission_upload".to_string(),
                "*/emergency_stop".to_string(),
            ],
            max_frequencies: vec![
                ("*/global_position".to_string(), 5.0),
                ("*/battery_state".to_string(), 1.0),
            ],
        }
    }
}

impl Ros2BridgeConfig {
    pub fn to_json(&self) -> String {
        serde_json::json!({
            "plugins": {
                "ros2dds": {
                    "domain": self.domain_id,
                    "allow": {
                        "publishers": self.allow_pub,
                        "subscribers": self.allow_sub
                    },
                    "pub_max_frequencies": self.max_frequencies.iter()
                        .map(|(k, v)| format!("{}={}", k, v))
                        .collect::<Vec<_>>()
                }
            }
        }).to_string()
    }
}
