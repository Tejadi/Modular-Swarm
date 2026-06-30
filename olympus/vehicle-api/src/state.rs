use std::collections::HashMap;
use std::sync::Arc;
use std::time::Instant;

use tokio::sync::RwLock;

use crate::metrics::MetricsDb;
use crate::models::{DetectionRecord, MissionState, TaskRecord, VehicleRecord};
use crate::routes::webhooks::WebhookStore;
use crate::zenoh_client::ZenohClient;

#[derive(Clone, Default)]
pub struct SwarmNetState {
    pub active_drones: u32,
    pub model_versions: HashMap<String, u32>,
    pub accuracies: HashMap<String, f64>,
    pub current_schedule: HashMap<String, String>,
}

#[derive(Clone)]
pub struct AppState {
    pub zenoh: Arc<ZenohClient>,
    pub vehicles: Arc<RwLock<HashMap<String, VehicleRecord>>>,
    pub detections: Arc<RwLock<HashMap<String, DetectionRecord>>>,
    pub tasks: Arc<RwLock<HashMap<String, TaskRecord>>>,
    pub mission: Arc<RwLock<MissionState>>,
    pub metrics: Option<MetricsDb>,
    pub webhooks: WebhookStore,
    pub start_time: Instant,
    pub swarmnet_status: Arc<RwLock<SwarmNetState>>,
}

impl AppState {
    pub fn new(zenoh: ZenohClient, metrics: Option<MetricsDb>) -> Self {
        Self {
            zenoh: Arc::new(zenoh),
            vehicles: Arc::new(RwLock::new(HashMap::new())),
            detections: Arc::new(RwLock::new(HashMap::new())),
            tasks: Arc::new(RwLock::new(HashMap::new())),
            mission: Arc::new(RwLock::new(MissionState::default())),
            metrics,
            webhooks: crate::routes::webhooks::new_store(),
            start_time: Instant::now(),
            swarmnet_status: Arc::new(RwLock::new(SwarmNetState::default())),
        }
    }
}
