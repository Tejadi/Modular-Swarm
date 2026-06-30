//! ExpressLRS (ELRS) CRSF serial driver for Happymodel EP1/EP2 receivers.
//!
//! The CRSF (Crossfire) protocol is a bidirectional telemetry/RC link used by
//! ExpressLRS 2.4GHz receivers. This driver handles:
//! - Receiving CRSF frames from the EP1 RX over UART
//! - Sending CRSF telemetry frames back through the link
//! - Extracting GPS, battery, link quality, and custom OLYMPUS payloads
//!
//! CRSF frame format:
//!   [addr] [len] [type] [payload...] [crc8]
//!   addr  = 0xC8 (flight controller) or 0xEE (radio transmitter)
//!   len   = payload length + type + crc (2..62)
//!   type  = frame type ID
//!   crc8  = CRC-8/DVB-S2 over type + payload

use anyhow::{Context, Result};
use tokio::sync::mpsc;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio_serial::SerialPortBuilderExt;
use tracing::{info, warn, error, debug};
use bytes::{BytesMut, Buf};

use crate::config::ElrsConfig;
use crate::bridge::BridgeMessage;

// CRSF protocol constants
const CRSF_SYNC_FC: u8 = 0xC8;        // Frame addressed to flight controller
const CRSF_SYNC_MODULE: u8 = 0xEE;    // Frame addressed to radio module
const CRSF_MAX_FRAME_LEN: usize = 64;

// CRSF frame type IDs
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum CrsfFrameType {
    Gps              = 0x02,
    BatterySensor    = 0x08,
    LinkStatistics   = 0x14,
    RcChannels       = 0x16,
    Attitude         = 0x1E,
    FlightMode       = 0x21,
    DeviceInfo       = 0x29,
    // Custom OLYMPUS payload types (in CRSF extended range 0x80+)
    OlympusTelemetry = 0x80,
    OlympusCommand   = 0x81,
    OlympusDetection = 0x82,
}

impl CrsfFrameType {
    fn from_u8(val: u8) -> Option<Self> {
        match val {
            0x02 => Some(Self::Gps),
            0x08 => Some(Self::BatterySensor),
            0x14 => Some(Self::LinkStatistics),
            0x16 => Some(Self::RcChannels),
            0x1E => Some(Self::Attitude),
            0x21 => Some(Self::FlightMode),
            0x29 => Some(Self::DeviceInfo),
            0x80 => Some(Self::OlympusTelemetry),
            0x81 => Some(Self::OlympusCommand),
            0x82 => Some(Self::OlympusDetection),
            _    => None,
        }
    }
}

/// Parsed CRSF frame
#[derive(Debug, Clone)]
pub struct CrsfFrame {
    pub addr: u8,
    pub frame_type: u8,
    pub payload: Vec<u8>,
}

/// CRSF link statistics from the receiver
#[derive(Debug, Clone, Default)]
pub struct ElrsLinkStats {
    pub uplink_rssi_ant1: i8,
    pub uplink_rssi_ant2: i8,
    pub uplink_link_quality: u8,
    pub uplink_snr: i8,
    pub active_antenna: u8,
    pub rf_mode: u8,
    pub uplink_tx_power: u8,
    pub downlink_rssi: i8,
    pub downlink_link_quality: u8,
    pub downlink_snr: i8,
}

/// GPS data extracted from CRSF GPS frame
#[derive(Debug, Clone, Default)]
pub struct ElrsGps {
    pub latitude: f64,   // degrees
    pub longitude: f64,  // degrees
    pub groundspeed: f32, // km/h
    pub heading: f32,    // degrees
    pub altitude: f32,   // meters
    pub satellites: u8,
}

/// CRC-8/DVB-S2 used by CRSF protocol
fn crc8_dvb_s2(data: &[u8]) -> u8 {
    let mut crc: u8 = 0;
    for &byte in data {
        crc ^= byte;
        for _ in 0..8 {
            if crc & 0x80 != 0 {
                crc = (crc << 1) ^ 0xD5;
            } else {
                crc <<= 1;
            }
        }
    }
    crc
}

pub struct ElrsBridge {
    config: ElrsConfig,
    tx_channel: mpsc::Sender<CrsfFrame>,
    #[allow(dead_code)]
    serial_task: tokio::task::JoinHandle<()>,
}

impl ElrsBridge {
    pub async fn new(
        config: &ElrsConfig,
        message_tx: mpsc::Sender<BridgeMessage>,
    ) -> Result<Self> {
        let port = tokio_serial::new(&config.serial_port, config.baud_rate)
            .open_native_async()
            .context(format!("Failed to open ELRS serial port: {}", config.serial_port))?;

        info!(
            "ELRS: Opened serial port {} at {} baud (Happymodel EP1 RX)",
            config.serial_port, config.baud_rate
        );

        let (tx_channel, tx_rx) = mpsc::channel::<CrsfFrame>(64);

        let serial_task = tokio::spawn(Self::serial_io_task(
            port,
            message_tx,
            tx_rx,
        ));

        Ok(Self {
            config: config.clone(),
            tx_channel,
            serial_task,
        })
    }

    pub fn get_tx_channel(&self) -> mpsc::Sender<CrsfFrame> {
        self.tx_channel.clone()
    }

    /// Send a CRSF frame to the ELRS receiver (downlink telemetry)
    pub async fn send_telemetry(&self, payload: Vec<u8>) -> Result<()> {
        if payload.len() > 60 {
            return Err(anyhow::anyhow!("CRSF payload too large (max 60 bytes)"));
        }

        let frame = CrsfFrame {
            addr: CRSF_SYNC_FC,
            frame_type: CrsfFrameType::OlympusTelemetry as u8,
            payload,
        };

        self.tx_channel
            .send(frame)
            .await
            .context("Failed to queue CRSF frame for transmission")?;
        Ok(())
    }

    /// Send compact drone telemetry over the ELRS link
    pub async fn send_compact_telemetry(&self, compact_bytes: Vec<u8>) -> Result<()> {
        // CRSF max payload is ~60 bytes; compact telemetry is ~40 bytes — fits
        if compact_bytes.len() > 60 {
            return Err(anyhow::anyhow!(
                "Compact telemetry too large for CRSF: {} bytes",
                compact_bytes.len()
            ));
        }
        self.send_telemetry(compact_bytes).await
    }

    /// Encode a CrsfFrame into wire bytes: [addr][len][type][payload][crc8]
    fn encode_frame(frame: &CrsfFrame) -> Vec<u8> {
        let len = (frame.payload.len() + 2) as u8; // +1 type, +1 crc
        let mut buf = Vec::with_capacity(frame.payload.len() + 4);
        buf.push(frame.addr);
        buf.push(len);
        buf.push(frame.frame_type);
        buf.extend_from_slice(&frame.payload);
        // CRC over type + payload
        let crc = crc8_dvb_s2(&buf[2..]); // from type byte onwards
        buf.push(crc);
        buf
    }

    async fn serial_io_task(
        mut port: tokio_serial::SerialStream,
        message_tx: mpsc::Sender<BridgeMessage>,
        mut tx_rx: mpsc::Receiver<CrsfFrame>,
    ) {
        let mut read_buf = BytesMut::with_capacity(512);
        let mut scratch = [0u8; 256];

        loop {
            tokio::select! {
                // Outbound: send CRSF frames to the receiver
                Some(frame) = tx_rx.recv() => {
                    let encoded = Self::encode_frame(&frame);
                    if let Err(e) = port.write_all(&encoded).await {
                        error!("ELRS: Failed to write CRSF frame: {}", e);
                    } else {
                        debug!("ELRS: Sent CRSF frame type=0x{:02X} len={}", frame.frame_type, encoded.len());
                    }
                }

                // Inbound: read CRSF frames from the receiver
                result = port.read(&mut scratch) => {
                    match result {
                        Ok(0) => {
                            warn!("ELRS: Serial port closed");
                            break;
                        }
                        Ok(n) => {
                            read_buf.extend_from_slice(&scratch[..n]);

                            // Parse all complete CRSF frames in the buffer
                            while read_buf.len() >= 4 {
                                // Find sync byte
                                if read_buf[0] != CRSF_SYNC_FC && read_buf[0] != CRSF_SYNC_MODULE {
                                    read_buf.advance(1);
                                    continue;
                                }

                                let frame_len = read_buf[1] as usize;
                                if frame_len < 2 || frame_len > CRSF_MAX_FRAME_LEN {
                                    // Invalid length, skip sync byte
                                    read_buf.advance(1);
                                    continue;
                                }

                                let total_len = frame_len + 2; // addr + len + (type + payload + crc)
                                if read_buf.len() < total_len {
                                    break; // Need more data
                                }

                                // Validate CRC
                                let crc_data = &read_buf[2..total_len - 1]; // type + payload
                                let expected_crc = read_buf[total_len - 1];
                                let computed_crc = crc8_dvb_s2(crc_data);

                                if computed_crc != expected_crc {
                                    debug!("ELRS: CRC mismatch, skipping byte");
                                    read_buf.advance(1);
                                    continue;
                                }

                                let frame = CrsfFrame {
                                    addr: read_buf[0],
                                    frame_type: read_buf[2],
                                    payload: read_buf[3..total_len - 1].to_vec(),
                                };

                                read_buf.advance(total_len);

                                // Dispatch frame
                                Self::dispatch_frame(&frame, &message_tx).await;
                            }

                            // Prevent buffer overflow
                            if read_buf.len() > 2048 {
                                warn!("ELRS: Read buffer overflow, clearing");
                                read_buf.clear();
                            }
                        }
                        Err(e) => {
                            error!("ELRS: Serial read error: {}", e);
                            tokio::time::sleep(tokio::time::Duration::from_secs(1)).await;
                        }
                    }
                }
            }
        }
    }

    async fn dispatch_frame(frame: &CrsfFrame, message_tx: &mpsc::Sender<BridgeMessage>) {
        match CrsfFrameType::from_u8(frame.frame_type) {
            Some(CrsfFrameType::LinkStatistics) => {
                if let Some(stats) = Self::parse_link_stats(&frame.payload) {
                    debug!(
                        "ELRS link: RSSI={}dBm LQ={}% SNR={}dB RF={}",
                        stats.uplink_rssi_ant1, stats.uplink_link_quality,
                        stats.uplink_snr, stats.rf_mode
                    );
                    let _ = message_tx.try_send(BridgeMessage::ElrsLinkStats(stats));
                }
            }
            Some(CrsfFrameType::Gps) => {
                if let Some(gps) = Self::parse_gps(&frame.payload) {
                    debug!(
                        "ELRS GPS: ({:.6}, {:.6}) alt={:.1}m sats={}",
                        gps.latitude, gps.longitude, gps.altitude, gps.satellites
                    );
                    let _ = message_tx.try_send(BridgeMessage::ElrsGps(gps));
                }
            }
            Some(CrsfFrameType::OlympusTelemetry) => {
                // Custom OLYMPUS telemetry payload from another drone via ELRS backpack
                debug!("ELRS: Received OLYMPUS telemetry payload ({} bytes)", frame.payload.len());
                let _ = message_tx.try_send(
                    BridgeMessage::ElrsPacket(frame.payload.clone(), frame.frame_type)
                );
            }
            Some(CrsfFrameType::OlympusCommand) => {
                debug!("ELRS: Received OLYMPUS command payload ({} bytes)", frame.payload.len());
                let _ = message_tx.try_send(
                    BridgeMessage::ElrsPacket(frame.payload.clone(), frame.frame_type)
                );
            }
            Some(CrsfFrameType::OlympusDetection) => {
                debug!("ELRS: Received OLYMPUS detection payload ({} bytes)", frame.payload.len());
                let _ = message_tx.try_send(
                    BridgeMessage::ElrsPacket(frame.payload.clone(), frame.frame_type)
                );
            }
            _ => {
                debug!("ELRS: Ignoring CRSF frame type 0x{:02X}", frame.frame_type);
            }
        }
    }

    /// Parse CRSF Link Statistics frame (type 0x14, 10 bytes payload)
    fn parse_link_stats(payload: &[u8]) -> Option<ElrsLinkStats> {
        if payload.len() < 10 {
            return None;
        }
        Some(ElrsLinkStats {
            uplink_rssi_ant1: payload[0] as i8,
            uplink_rssi_ant2: payload[1] as i8,
            uplink_link_quality: payload[2],
            uplink_snr: payload[3] as i8,
            active_antenna: payload[4],
            rf_mode: payload[5],
            uplink_tx_power: payload[6],
            downlink_rssi: payload[7] as i8,
            downlink_link_quality: payload[8],
            downlink_snr: payload[9] as i8,
        })
    }

    /// Parse CRSF GPS frame (type 0x02, 15 bytes payload)
    fn parse_gps(payload: &[u8]) -> Option<ElrsGps> {
        if payload.len() < 15 {
            return None;
        }
        // CRSF GPS: lat/lon as i32 (1e-7 degrees), groundspeed u16 (km/h * 10),
        // heading u16 (degrees * 100), altitude u16 (meters + 1000), sats u8
        let lat_raw = i32::from_be_bytes([payload[0], payload[1], payload[2], payload[3]]);
        let lon_raw = i32::from_be_bytes([payload[4], payload[5], payload[6], payload[7]]);
        let groundspeed = u16::from_be_bytes([payload[8], payload[9]]);
        let heading = u16::from_be_bytes([payload[10], payload[11]]);
        let altitude = u16::from_be_bytes([payload[12], payload[13]]);
        let satellites = payload[14];

        Some(ElrsGps {
            latitude: lat_raw as f64 / 1e7,
            longitude: lon_raw as f64 / 1e7,
            groundspeed: groundspeed as f32 / 10.0,
            heading: heading as f32 / 100.0,
            altitude: altitude as f32 - 1000.0,
            satellites,
        })
    }
}

pub struct MockElrsBridge {
    tx_channel: mpsc::Sender<CrsfFrame>,
}

impl MockElrsBridge {
    pub fn new() -> (Self, mpsc::Receiver<CrsfFrame>) {
        let (tx, rx) = mpsc::channel(64);
        (Self { tx_channel: tx }, rx)
    }

    pub fn get_tx_channel(&self) -> mpsc::Sender<CrsfFrame> {
        self.tx_channel.clone()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_crc8_dvb_s2() {
        // Known test vector: CRC of empty is 0
        assert_eq!(crc8_dvb_s2(&[]), 0);
        // CRC of [0x14] (link stats type) should be deterministic
        let crc = crc8_dvb_s2(&[0x14]);
        assert_ne!(crc, 0);
    }

    #[test]
    fn test_encode_decode_frame() {
        let frame = CrsfFrame {
            addr: CRSF_SYNC_FC,
            frame_type: CrsfFrameType::OlympusTelemetry as u8,
            payload: vec![0x01, 0x02, 0x03],
        };

        let encoded = ElrsBridge::encode_frame(&frame);
        // addr + len + type + 3 payload + crc = 7 bytes
        assert_eq!(encoded.len(), 7);
        assert_eq!(encoded[0], CRSF_SYNC_FC);
        assert_eq!(encoded[1], 5); // payload(3) + type(1) + crc(1)
        assert_eq!(encoded[2], CrsfFrameType::OlympusTelemetry as u8);

        // Verify CRC
        let crc_data = &encoded[2..encoded.len() - 1];
        assert_eq!(crc8_dvb_s2(crc_data), encoded[encoded.len() - 1]);
    }

    #[test]
    fn test_parse_link_stats() {
        let payload = vec![
            0xBC_u8, // rssi_ant1 = -68 (as i8)
            0xBE,    // rssi_ant2 = -66
            100,     // link quality 100%
            0x0A,    // snr = 10
            0,       // antenna 0
            2,       // rf mode 2 (150Hz)
            3,       // tx power index 3
            0xC0_u8, // downlink rssi = -64
            99,      // downlink lq
            0x08,    // downlink snr = 8
        ];

        let stats = ElrsBridge::parse_link_stats(&payload).unwrap();
        assert_eq!(stats.uplink_link_quality, 100);
        assert_eq!(stats.rf_mode, 2);
    }

    #[test]
    fn test_parse_gps() {
        // Encode a known position: 42.3601, -71.0589
        let lat = (42.3601 * 1e7) as i32;
        let lon = (-71.0589 * 1e7) as i32;
        let speed: u16 = 150; // 15.0 km/h
        let hdg: u16 = 9000;  // 90.00 degrees
        let alt: u16 = 1035;  // 35m (offset by 1000)
        let sats: u8 = 12;

        let mut payload = Vec::new();
        payload.extend_from_slice(&lat.to_be_bytes());
        payload.extend_from_slice(&lon.to_be_bytes());
        payload.extend_from_slice(&speed.to_be_bytes());
        payload.extend_from_slice(&hdg.to_be_bytes());
        payload.extend_from_slice(&alt.to_be_bytes());
        payload.push(sats);

        let gps = ElrsBridge::parse_gps(&payload).unwrap();
        assert!((gps.latitude - 42.3601).abs() < 0.001);
        assert!((gps.longitude - (-71.0589)).abs() < 0.001);
        assert_eq!(gps.satellites, 12);
        assert!((gps.altitude - 35.0).abs() < 1.0);
    }
}
