use anyhow::{Context, Result};
use mavlink::ardupilotmega::MavMessage;
use mavlink::{MavConnection, MavHeader};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use std::time::Instant;
use tokio::sync::{mpsc, RwLock};
use tokio::task::JoinHandle;
use tokio::time::{interval, Duration};
use tracing::{debug, error, info, warn};

use crate::config::MavlinkConfig;
use crate::protocol::{BatteryState, DroneCommand, DroneStatus, GeoPosition};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MavlinkTelemetry {
    pub armed: bool,
    pub flight_mode: u32,
    pub base_mode: u8,
    pub position: GeoPosition,
    pub battery_voltage: f32,
    pub battery_current: f32,
    pub battery_remaining: u8,
    pub battery_detail: Option<BatteryDetail>,
    pub attitude: Attitude,
    pub vfr: VfrHud,
    pub uptime_secs: f64,
}

impl Default for MavlinkTelemetry {
    fn default() -> Self {
        Self {
            armed: false,
            flight_mode: 0,
            base_mode: 0,
            position: GeoPosition::default(),
            battery_voltage: 0.0,
            battery_current: 0.0,
            battery_remaining: 0,
            battery_detail: None,
            attitude: Attitude::default(),
            vfr: VfrHud::default(),
            uptime_secs: 0.0,
        }
    }
}

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize)]
pub struct Attitude {
    pub roll: f32,
    pub pitch: f32,
    pub yaw: f32,
}

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize)]
pub struct VfrHud {
    pub airspeed: f32,
    pub groundspeed: f32,
    pub heading: i16,
    pub throttle: u16,
    pub climb_rate: f32,
    pub altitude: f32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BatteryDetail {
    pub cell_voltages: Vec<u16>,
    pub current_consumed: i32,
    pub energy_consumed: i32,
    pub temperature: i16,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MavlinkAlert {
    pub severity: u8,
    pub text: String,
}

#[derive(Debug, Clone)]
pub enum MavlinkCommand {
    EmergencyStop,
    ReturnToLaunch,
    GoTo { position: GeoPosition },
    Pause,
    Resume,
    SetAltitude { altitude: f64 },
}

impl MavlinkCommand {
    pub fn from_drone_command(cmd: &DroneCommand) -> Option<Self> {
        match cmd {
            DroneCommand::EmergencyStop => Some(Self::EmergencyStop),
            DroneCommand::ReturnToLaunch => Some(Self::ReturnToLaunch),
            DroneCommand::GoTo { position } => Some(Self::GoTo {
                position: *position,
            }),
            DroneCommand::Pause => Some(Self::Pause),
            DroneCommand::Resume => Some(Self::Resume),
            DroneCommand::SetAltitude { altitude } => Some(Self::SetAltitude {
                altitude: *altitude,
            }),
            _ => None,
        }
    }
}

#[derive(Debug, Clone)]
pub struct TunnelDetection {
    pub detection_type_idx: u8,
    pub confidence: f32,
    pub severity: u8,
    pub latitude: f64,
    pub longitude: f64,
    pub altitude: f64,
    pub detected_by: String,
    pub timestamp_secs: u32,
}

#[derive(Debug, Clone)]
pub enum MavlinkEvent {
    TelemetryUpdate(MavlinkTelemetry),
    Alert(MavlinkAlert),
    TunnelDetection(TunnelDetection),
    ConnectionState(bool),
}

pub struct MavlinkHandler {
    config: MavlinkConfig,
    state: Arc<RwLock<MavlinkTelemetry>>,
    cmd_rx: mpsc::Receiver<MavlinkCommand>,
    event_tx: mpsc::Sender<MavlinkEvent>,
    start_time: Instant,
}

impl MavlinkHandler {
    pub fn new(
        config: MavlinkConfig,
    ) -> (
        Self,
        mpsc::Sender<MavlinkCommand>,
        mpsc::Receiver<MavlinkEvent>,
    ) {
        let (cmd_tx, cmd_rx) = mpsc::channel::<MavlinkCommand>(64);
        let (event_tx, event_rx) = mpsc::channel::<MavlinkEvent>(256);

        let handler = Self {
            config,
            state: Arc::new(RwLock::new(MavlinkTelemetry::default())),
            cmd_rx,
            event_tx,
            start_time: Instant::now(),
        };

        (handler, cmd_tx, event_rx)
    }

    pub fn start(self) -> JoinHandle<()> {
        tokio::spawn(async move {
            if let Err(e) = self.run().await {
                error!("MavlinkHandler exited with error: {}", e);
            }
        })
    }

    async fn run(mut self) -> Result<()> {
        info!(
            "MavlinkHandler starting – connecting to {}",
            self.config.connection_string
        );

        let connection = mavlink::connect::<MavMessage>(&self.config.connection_string)
            .context("Failed to connect to MAVLink endpoint")?;
        let connection = Arc::new(connection);

        info!("MAVLink connection established");
        let _ = self.event_tx.send(MavlinkEvent::ConnectionState(true)).await;

        Self::request_data_streams(
            &connection,
            self.config.system_id,
            self.config.component_id,
            self.config.stream_rate_hz,
        )?;

        let hb_conn = Arc::clone(&connection);
        let hb_sys = self.config.system_id;
        let hb_comp = self.config.component_id;
        let hb_interval = self.config.heartbeat_interval_ms;
        let heartbeat_handle = tokio::spawn(async move {
            Self::heartbeat_loop(hb_conn, hb_sys, hb_comp, hb_interval).await;
        });

        let recv_conn = Arc::clone(&connection);
        let recv_state = Arc::clone(&self.state);
        let recv_event_tx = self.event_tx.clone();
        let recv_start = self.start_time;
        let receive_handle: JoinHandle<()> = tokio::task::spawn_blocking(move || {
            Self::receive_loop(recv_conn, recv_state, recv_event_tx, recv_start);
        });

        loop {
            tokio::select! {
                Some(cmd) = self.cmd_rx.recv() => {
                    if let Err(e) = Self::send_command(
                        &connection,
                        self.config.system_id,
                        self.config.component_id,
                        cmd,
                    ) {
                        error!("Failed to send MAVLink command: {}", e);
                    }
                }
                _ = tokio::signal::ctrl_c() => {
                    info!("MavlinkHandler shutting down");
                    break;
                }
            }
        }

        heartbeat_handle.abort();
        receive_handle.abort();
        let _ = self.event_tx.send(MavlinkEvent::ConnectionState(false)).await;
        Ok(())
    }

    async fn heartbeat_loop(
        conn: Arc<Box<dyn MavConnection<MavMessage> + Sync + Send>>,
        system_id: u8,
        component_id: u8,
        interval_ms: u64,
    ) {
        let mut ticker = interval(Duration::from_millis(interval_ms));

        loop {
            ticker.tick().await;

            let heartbeat = MavMessage::HEARTBEAT(mavlink::ardupilotmega::HEARTBEAT_DATA {
                custom_mode: 0,
                mavtype: mavlink::ardupilotmega::MavType::MAV_TYPE_ONBOARD_CONTROLLER,
                autopilot: mavlink::ardupilotmega::MavAutopilot::MAV_AUTOPILOT_INVALID,
                base_mode: mavlink::ardupilotmega::MavModeFlag::empty(),
                system_status: mavlink::ardupilotmega::MavState::MAV_STATE_ACTIVE,
                mavlink_version: 3,
            });

            let header = MavHeader {
                system_id,
                component_id,
                sequence: 0,
            };

            if let Err(e) = conn.send(&header, &heartbeat) {
                warn!("Failed to send heartbeat: {}", e);
            } else {
                debug!("Heartbeat sent");
            }
        }
    }

    fn request_data_streams(
        conn: &Arc<Box<dyn MavConnection<MavMessage> + Sync + Send>>,
        system_id: u8,
        component_id: u8,
        rate_hz: u8,
    ) -> Result<()> {
        let header = MavHeader {
            system_id,
            component_id,
            sequence: 0,
        };

        let stream_ids = [
            mavlink::ardupilotmega::MavDataStream::MAV_DATA_STREAM_ALL,
            mavlink::ardupilotmega::MavDataStream::MAV_DATA_STREAM_POSITION,
            mavlink::ardupilotmega::MavDataStream::MAV_DATA_STREAM_EXTENDED_STATUS,
            mavlink::ardupilotmega::MavDataStream::MAV_DATA_STREAM_EXTRA1,
            mavlink::ardupilotmega::MavDataStream::MAV_DATA_STREAM_EXTRA2,
        ];

        for stream_id in &stream_ids {
            let msg =
                MavMessage::REQUEST_DATA_STREAM(mavlink::ardupilotmega::REQUEST_DATA_STREAM_DATA {
                    target_system: 1,
                    target_component: 1,
                    req_stream_id: *stream_id as u8,
                    req_message_rate: rate_hz as u16,
                    start_stop: 1,
                });

            conn.send(&header, &msg)
                .map_err(|e| anyhow::anyhow!("Failed to request data stream: {}", e))?;
        }

        info!("Requested data streams at {} Hz", rate_hz);
        Ok(())
    }

    fn receive_loop(
        conn: Arc<Box<dyn MavConnection<MavMessage> + Sync + Send>>,
        state: Arc<RwLock<MavlinkTelemetry>>,
        event_tx: mpsc::Sender<MavlinkEvent>,
        start_time: Instant,
    ) {
        loop {
            match conn.recv() {
                Ok((_header, msg)) => {
                    Self::handle_message(&state, &event_tx, &msg, start_time);
                }
                Err(e) => {
                    debug!("MAVLink recv error: {}", e);
                    std::thread::sleep(std::time::Duration::from_millis(10));
                }
            }
        }
    }

    fn handle_message(
        state: &Arc<RwLock<MavlinkTelemetry>>,
        event_tx: &mpsc::Sender<MavlinkEvent>,
        msg: &MavMessage,
        start_time: Instant,
    ) {
        match msg {
            MavMessage::HEARTBEAT(hb) => {
                if let Ok(mut s) = state.try_write() {
                    let base_mode_bits = hb.base_mode.bits();
                    s.armed = (base_mode_bits & 128) != 0;
                    s.flight_mode = hb.custom_mode;
                    s.base_mode = base_mode_bits;
                    s.uptime_secs = start_time.elapsed().as_secs_f64();
                }
                debug!(
                    "HEARTBEAT: mode={} custom_mode={}",
                    hb.base_mode.bits(),
                    hb.custom_mode
                );
            }

            MavMessage::GLOBAL_POSITION_INT(pos) => {
                if let Ok(mut s) = state.try_write() {
                    s.position.latitude = pos.lat as f64 / 1e7;
                    s.position.longitude = pos.lon as f64 / 1e7;
                    s.position.altitude = pos.alt as f64 / 1000.0;
                    s.position.heading = pos.hdg as f32 / 100.0;
                    s.uptime_secs = start_time.elapsed().as_secs_f64();
                }
                debug!(
                    "GLOBAL_POSITION_INT: lat={:.7} lon={:.7} alt={:.1}m hdg={:.1}",
                    pos.lat as f64 / 1e7,
                    pos.lon as f64 / 1e7,
                    pos.alt as f64 / 1000.0,
                    pos.hdg as f32 / 100.0,
                );
            }

            MavMessage::SYS_STATUS(sys) => {
                if let Ok(mut s) = state.try_write() {
                    s.battery_voltage = sys.voltage_battery as f32 / 1000.0;
                    s.battery_current = sys.current_battery as f32 / 100.0;
                    s.battery_remaining = if sys.battery_remaining >= 0 {
                        sys.battery_remaining as u8
                    } else {
                        0
                    };
                    s.uptime_secs = start_time.elapsed().as_secs_f64();
                }
                debug!(
                    "SYS_STATUS: voltage={:.2}V current={:.2}A remaining={}%",
                    sys.voltage_battery as f32 / 1000.0,
                    sys.current_battery as f32 / 100.0,
                    sys.battery_remaining,
                );
            }

            MavMessage::BATTERY_STATUS(bat) => {
                if let Ok(mut s) = state.try_write() {
                    let cells: Vec<u16> = bat
                        .voltages
                        .iter()
                        .copied()
                        .filter(|&v| v != u16::MAX)
                        .collect();
                    s.battery_detail = Some(BatteryDetail {
                        cell_voltages: cells,
                        current_consumed: bat.current_consumed,
                        energy_consumed: bat.energy_consumed,
                        temperature: bat.temperature,
                    });
                    s.uptime_secs = start_time.elapsed().as_secs_f64();
                }
                debug!(
                    "BATTERY_STATUS: consumed={}mAh temp={}cdegC",
                    bat.current_consumed, bat.temperature
                );
            }

            MavMessage::ATTITUDE(att) => {
                if let Ok(mut s) = state.try_write() {
                    s.attitude = Attitude {
                        roll: att.roll,
                        pitch: att.pitch,
                        yaw: att.yaw,
                    };
                    s.uptime_secs = start_time.elapsed().as_secs_f64();
                }
                debug!(
                    "ATTITUDE: roll={:.2} pitch={:.2} yaw={:.2}",
                    att.roll, att.pitch, att.yaw
                );
            }

            MavMessage::VFR_HUD(hud) => {
                if let Ok(mut s) = state.try_write() {
                    s.vfr = VfrHud {
                        airspeed: hud.airspeed,
                        groundspeed: hud.groundspeed,
                        heading: hud.heading,
                        throttle: hud.throttle,
                        climb_rate: hud.climb,
                        altitude: hud.alt,
                    };
                    s.uptime_secs = start_time.elapsed().as_secs_f64();
                }
                debug!(
                    "VFR_HUD: gs={:.1}m/s hdg={} thr={}% alt={:.1}m",
                    hud.groundspeed, hud.heading, hud.throttle, hud.alt
                );
            }

            MavMessage::STATUSTEXT(st) => {
                let text = st
                    .text
                    .iter()
                    .take_while(|&&c| c != 0)
                    .map(|&c| c as char)
                    .collect::<String>();

                let alert = MavlinkAlert {
                    severity: st.severity as u8,
                    text: text.clone(),
                };
                info!("STATUSTEXT [sev={}]: {}", st.severity as u8, text);
                let _ = event_tx.try_send(MavlinkEvent::Alert(alert));
            }

            MavMessage::COMMAND_ACK(ack) => {
                info!(
                    "COMMAND_ACK: cmd={} result={}",
                    ack.command as u16, ack.result as u8
                );
            }

            MavMessage::TUNNEL(tunnel) => {
                // Olympus detection payload: type 0x4F
                if tunnel.payload_type as u32 == 0x4F && tunnel.payload_length >= 19 {
                    let data = &tunnel.payload[..tunnel.payload_length as usize];
                    if let Some(det) = Self::decode_tunnel_detection(data) {
                        info!(
                            "TUNNEL detection: type={} conf={:.2} at ({:.6}, {:.6})",
                            det.detection_type_idx, det.confidence,
                            det.latitude, det.longitude
                        );
                        let _ = event_tx.try_send(MavlinkEvent::TunnelDetection(det));
                    }
                } else {
                    debug!(
                        "TUNNEL: type=0x{:02X} len={}",
                        tunnel.payload_type as u32, tunnel.payload_length
                    );
                }
            }

            _ => {
            }
        }

        if let Ok(s) = state.try_read() {
            let _ = event_tx.try_send(MavlinkEvent::TelemetryUpdate(s.clone()));
        }
    }

    fn send_command(
        conn: &Arc<Box<dyn MavConnection<MavMessage> + Sync + Send>>,
        system_id: u8,
        component_id: u8,
        cmd: MavlinkCommand,
    ) -> Result<()> {
        let header = MavHeader {
            system_id,
            component_id,
            sequence: 0,
        };

        match cmd {
            MavlinkCommand::EmergencyStop => {
                let msg = MavMessage::COMMAND_LONG(mavlink::ardupilotmega::COMMAND_LONG_DATA {
                    target_system: 1,
                    target_component: 1,
                    command: mavlink::ardupilotmega::MavCmd::MAV_CMD_COMPONENT_ARM_DISARM,
                    confirmation: 0,
                    param1: 0.0,
                    param2: 21196.0,
                    param3: 0.0,
                    param4: 0.0,
                    param5: 0.0,
                    param6: 0.0,
                    param7: 0.0,
                });
                conn.send(&header, &msg)
                    .map_err(|e| anyhow::anyhow!("send EmergencyStop: {}", e))?;
                warn!("EMERGENCY STOP sent (force disarm)");
            }

            MavlinkCommand::ReturnToLaunch => {
                let msg = MavMessage::COMMAND_LONG(mavlink::ardupilotmega::COMMAND_LONG_DATA {
                    target_system: 1,
                    target_component: 1,
                    command: mavlink::ardupilotmega::MavCmd::MAV_CMD_NAV_RETURN_TO_LAUNCH,
                    confirmation: 0,
                    param1: 0.0,
                    param2: 0.0,
                    param3: 0.0,
                    param4: 0.0,
                    param5: 0.0,
                    param6: 0.0,
                    param7: 0.0,
                });
                conn.send(&header, &msg)
                    .map_err(|e| anyhow::anyhow!("send ReturnToLaunch: {}", e))?;
                info!("RTL command sent");
            }

            MavlinkCommand::GoTo { position } => {
                let msg = MavMessage::SET_POSITION_TARGET_GLOBAL_INT(
                    mavlink::ardupilotmega::SET_POSITION_TARGET_GLOBAL_INT_DATA {
                        time_boot_ms: 0,
                        target_system: 1,
                        target_component: 1,
                        coordinate_frame:
                            mavlink::ardupilotmega::MavFrame::MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                        type_mask: mavlink::ardupilotmega::PositionTargetTypemask::from_bits_truncate(0b0000_1111_1111_1000),
                        lat_int: (position.latitude * 1e7) as i32,
                        lon_int: (position.longitude * 1e7) as i32,
                        alt: position.altitude as f32,
                        vx: 0.0,
                        vy: 0.0,
                        vz: 0.0,
                        afx: 0.0,
                        afy: 0.0,
                        afz: 0.0,
                        yaw: 0.0,
                        yaw_rate: 0.0,
                    },
                );
                conn.send(&header, &msg)
                    .map_err(|e| anyhow::anyhow!("send GoTo: {}", e))?;
                info!(
                    "GoTo sent: lat={:.7} lon={:.7} alt={:.1}",
                    position.latitude, position.longitude, position.altitude
                );
            }

            MavlinkCommand::Pause => {
                let msg = MavMessage::COMMAND_LONG(mavlink::ardupilotmega::COMMAND_LONG_DATA {
                    target_system: 1,
                    target_component: 1,
                    command: mavlink::ardupilotmega::MavCmd::MAV_CMD_DO_PAUSE_CONTINUE,
                    confirmation: 0,
                    param1: 0.0,
                    param2: 0.0,
                    param3: 0.0,
                    param4: 0.0,
                    param5: 0.0,
                    param6: 0.0,
                    param7: 0.0,
                });
                conn.send(&header, &msg)
                    .map_err(|e| anyhow::anyhow!("send Pause: {}", e))?;
                info!("Pause command sent");
            }

            MavlinkCommand::Resume => {
                let msg = MavMessage::COMMAND_LONG(mavlink::ardupilotmega::COMMAND_LONG_DATA {
                    target_system: 1,
                    target_component: 1,
                    command: mavlink::ardupilotmega::MavCmd::MAV_CMD_DO_PAUSE_CONTINUE,
                    confirmation: 0,
                    param1: 1.0,
                    param2: 0.0,
                    param3: 0.0,
                    param4: 0.0,
                    param5: 0.0,
                    param6: 0.0,
                    param7: 0.0,
                });
                conn.send(&header, &msg)
                    .map_err(|e| anyhow::anyhow!("send Resume: {}", e))?;
                info!("Resume command sent");
            }

            MavlinkCommand::SetAltitude { altitude } => {
                let msg = MavMessage::SET_POSITION_TARGET_GLOBAL_INT(
                    mavlink::ardupilotmega::SET_POSITION_TARGET_GLOBAL_INT_DATA {
                        time_boot_ms: 0,
                        target_system: 1,
                        target_component: 1,
                        coordinate_frame:
                            mavlink::ardupilotmega::MavFrame::MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                        type_mask: mavlink::ardupilotmega::PositionTargetTypemask::from_bits_truncate(0b0000_1111_1111_1011),
                        lat_int: 0,
                        lon_int: 0,
                        alt: altitude as f32,
                        vx: 0.0,
                        vy: 0.0,
                        vz: 0.0,
                        afx: 0.0,
                        afy: 0.0,
                        afz: 0.0,
                        yaw: 0.0,
                        yaw_rate: 0.0,
                    },
                );
                conn.send(&header, &msg)
                    .map_err(|e| anyhow::anyhow!("send SetAltitude: {}", e))?;
                info!("SetAltitude sent: {:.1}m", altitude);
            }
        }

        Ok(())
    }

    /// Decode compact Olympus detection from TUNNEL payload.
    /// Format: [0x4F][det_type:u8][confidence:u8][severity:u8][lat:i32][lon:i32][alt:u16][id_len:u8][detected_by][ts:u32]
    fn decode_tunnel_detection(data: &[u8]) -> Option<TunnelDetection> {
        if data.len() < 19 || data[0] != 0x4F {
            return None;
        }

        let det_type_idx = data[1];
        let confidence = data[2] as f32 / 255.0;
        let severity = data[3];

        let lat_i32 = i32::from_le_bytes(data[4..8].try_into().ok()?);
        let lon_i32 = i32::from_le_bytes(data[8..12].try_into().ok()?);
        let alt_u16 = u16::from_le_bytes(data[12..14].try_into().ok()?);

        let id_len = data[14] as usize;
        if 15 + id_len + 4 > data.len() {
            return None;
        }

        let detected_by = String::from_utf8_lossy(&data[15..15 + id_len]).to_string();
        let ts_offset = 15 + id_len;
        let timestamp_secs = u32::from_le_bytes(data[ts_offset..ts_offset + 4].try_into().ok()?);

        Some(TunnelDetection {
            detection_type_idx: det_type_idx,
            confidence,
            severity,
            latitude: lat_i32 as f64 / 1e7,
            longitude: lon_i32 as f64 / 1e7,
            altitude: alt_u16 as f64,
            detected_by,
            timestamp_secs,
        })
    }

    pub fn to_battery_state(telem: &MavlinkTelemetry) -> BatteryState {
        BatteryState {
            voltage: telem.battery_voltage,
            current: telem.battery_current,
            percentage: telem.battery_remaining,
            remaining_time: 0,
            cell_count: telem
                .battery_detail
                .as_ref()
                .map(|d| d.cell_voltages.len() as u8)
                .unwrap_or(0),
            temperature: telem
                .battery_detail
                .as_ref()
                .map(|d| d.temperature as f32 / 100.0)
                .unwrap_or(0.0),
        }
    }

    pub fn to_drone_status(telem: &MavlinkTelemetry) -> DroneStatus {
        if !telem.armed {
            return DroneStatus::Idle;
        }
        if telem.vfr.groundspeed > 1.0 {
            DroneStatus::Transiting
        } else {
            DroneStatus::Scanning
        }
    }
}
