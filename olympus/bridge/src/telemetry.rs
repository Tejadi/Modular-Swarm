use anyhow::{Context, Result};
use chrono::Utc;
use std::sync::Arc;
use tokio::sync::RwLock;
use tokio::time::{interval, Duration};
use tracing::{info, warn, error, debug};

use crate::protocol::{
    DroneTelemetry, DroneStatus, DroneRole, GeoPosition, BatteryState,
};

#[async_trait::async_trait]
pub trait TelemetrySource: Send + Sync {
    async fn get_position(&self) -> Result<GeoPosition>;
    async fn get_battery(&self) -> Result<BatteryState>;
    async fn get_status(&self) -> Result<DroneStatus>;
}

pub struct Px4TelemetrySource {
    position: Arc<RwLock<GeoPosition>>,
    battery: Arc<RwLock<BatteryState>>,
    status: Arc<RwLock<DroneStatus>>,
}

impl Px4TelemetrySource {
    pub fn new() -> Self {
        Self {
            position: Arc::new(RwLock::new(GeoPosition::default())),
            battery: Arc::new(RwLock::new(BatteryState::default())),
            status: Arc::new(RwLock::new(DroneStatus::Idle)),
        }
    }

    pub async fn update_position(&self, pos: GeoPosition) {
        let mut p = self.position.write().await;
        *p = pos;
    }

    pub async fn update_battery(&self, bat: BatteryState) {
        let mut b = self.battery.write().await;
        *b = bat;
    }

    pub async fn update_status(&self, status: DroneStatus) {
        let mut s = self.status.write().await;
        *s = status;
    }
}

#[async_trait::async_trait]
impl TelemetrySource for Px4TelemetrySource {
    async fn get_position(&self) -> Result<GeoPosition> {
        let p = self.position.read().await;
        Ok(*p)
    }

    async fn get_battery(&self) -> Result<BatteryState> {
        let b = self.battery.read().await;
        Ok(*b)
    }

    async fn get_status(&self) -> Result<DroneStatus> {
        let s = self.status.read().await;
        Ok(*s)
    }
}

pub struct MockTelemetrySource {
    base_position: GeoPosition,
}

impl MockTelemetrySource {
    pub fn new(base_lat: f64, base_lon: f64) -> Self {
        Self {
            base_position: GeoPosition::new(base_lat, base_lon, 35.0),
        }
    }
}

#[async_trait::async_trait]
impl TelemetrySource for MockTelemetrySource {
    async fn get_position(&self) -> Result<GeoPosition> {
        let now = Utc::now().timestamp_millis() as f64 / 1000.0;
        let drift_lat = (now * 0.01).sin() * 0.0001;
        let drift_lon = (now * 0.01).cos() * 0.0001;

        Ok(GeoPosition {
            latitude: self.base_position.latitude + drift_lat,
            longitude: self.base_position.longitude + drift_lon,
            altitude: self.base_position.altitude + (now * 0.1).sin() * 2.0,
            heading: ((now * 10.0) % 360.0) as f32,
        })
    }

    async fn get_battery(&self) -> Result<BatteryState> {
        Ok(BatteryState {
            voltage: 22.2,
            current: 5.5,
            percentage: 85,
            remaining_time: 1200,
            cell_count: 6,
            temperature: 28.5,
        })
    }

    async fn get_status(&self) -> Result<DroneStatus> {
        Ok(DroneStatus::Scanning)
    }
}

pub struct TelemetryService {
    drone_id: String,
    role: DroneRole,
    source: Arc<dyn TelemetrySource>,
    current: Arc<RwLock<DroneTelemetry>>,
}

impl TelemetryService {
    pub fn new(
        drone_id: String,
        role: DroneRole,
        source: Arc<dyn TelemetrySource>,
    ) -> Self {
        let current = Arc::new(RwLock::new(DroneTelemetry::new(drone_id.clone(), role)));
        Self {
            drone_id,
            role,
            source,
            current,
        }
    }

    pub async fn get_current(&self) -> DroneTelemetry {
        let t = self.current.read().await;
        t.clone()
    }

    pub async fn update(&self) -> Result<DroneTelemetry> {
        let position = self.source.get_position().await?;
        let battery = self.source.get_battery().await?;
        let status = self.source.get_status().await?;

        let mut t = self.current.write().await;
        t.position = position;
        t.battery = battery;
        t.status = status;
        t.timestamp = Utc::now();

        Ok(t.clone())
    }

    pub async fn run_update_loop(&self, rate_hz: f32) {
        let period = Duration::from_secs_f32(1.0 / rate_hz);
        let mut ticker = interval(period);

        loop {
            ticker.tick().await;

            if let Err(e) = self.update().await {
                warn!("Telemetry update failed: {}", e);
            }
        }
    }
}

pub struct TelemetryFilter {
    last_position: Option<GeoPosition>,
    last_battery: Option<u8>,
    position_threshold: f64,
    battery_threshold: u8,
}

impl TelemetryFilter {
    pub fn new(position_threshold: f64, battery_threshold: u8) -> Self {
        Self {
            last_position: None,
            last_battery: None,
            position_threshold,
            battery_threshold,
        }
    }

    pub fn should_transmit(&mut self, telem: &DroneTelemetry) -> bool {
        let position_changed = match &self.last_position {
            None => true,
            Some(last) => last.distance_to(&telem.position) > self.position_threshold,
        };

        let battery_changed = match self.last_battery {
            None => true,
            Some(last) => (telem.battery.percentage as i16 - last as i16).abs() >= self.battery_threshold as i16,
        };

        if position_changed || battery_changed {
            self.last_position = Some(telem.position);
            self.last_battery = Some(telem.battery.percentage);
            true
        } else {
            false
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_mock_telemetry() {
        let source = MockTelemetrySource::new(45.5, -122.5);
        let pos = source.get_position().await.unwrap();

        assert!((pos.latitude - 45.5).abs() < 0.001);
        assert!((pos.longitude + 122.5).abs() < 0.001);
    }

    #[test]
    fn test_telemetry_filter() {
        let mut filter = TelemetryFilter::new(1.0, 2);

        let mut telem = DroneTelemetry::new("test".to_string(), DroneRole::Scout);
        telem.position = GeoPosition::new(45.5, -122.5, 30.0);
        telem.battery.percentage = 90;

        assert!(filter.should_transmit(&telem));

        assert!(!filter.should_transmit(&telem));

        telem.position.latitude += 0.000001;
        assert!(!filter.should_transmit(&telem));

        telem.position.latitude += 0.0001;
        assert!(filter.should_transmit(&telem));

        telem.battery.percentage = 85;
        assert!(filter.should_transmit(&telem));
    }
}
