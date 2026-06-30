use anyhow::{Context, Result};
use std::sync::Arc;
use tokio::sync::mpsc;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio_serial::{SerialPortBuilderExt, SerialStream};
use tracing::{info, warn, error, debug};
use bytes::{BytesMut, Buf};

use crate::config::LoRaConfig;
use crate::bridge::BridgeMessage;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum MeshtasticPortNum {
    Unknown = 0,
    TextMessage = 1,
    PositionApp = 3,
    TelemetryApp = 67,
    PrivateApp = 255,
}

#[derive(Debug, Clone)]
pub struct MeshPacket {
    pub from: u32,
    pub to: u32,
    pub port_num: u8,
    pub payload: Vec<u8>,
    pub hop_limit: u8,
    pub want_ack: bool,
}

impl MeshPacket {
    pub fn broadcast(payload: Vec<u8>, port_num: u8) -> Self {
        Self {
            from: 0,
            to: 0xFFFFFFFF,
            port_num,
            payload,
            hop_limit: 3,
            want_ack: false,
        }
    }

    pub fn encode(&self) -> Vec<u8> {
        let mut buf = Vec::with_capacity(self.payload.len() + 16);

        buf.extend_from_slice(&[0x94, 0xC3]);

        let payload_len = self.payload.len() as u16;
        buf.extend_from_slice(&payload_len.to_le_bytes());

        buf.extend_from_slice(&self.from.to_le_bytes());

        buf.extend_from_slice(&self.to.to_le_bytes());

        buf.push(self.port_num);

        buf.push(self.hop_limit);

        buf.push(if self.want_ack { 1 } else { 0 });

        buf.extend_from_slice(&self.payload);

        buf
    }

    pub fn decode(data: &[u8]) -> Option<Self> {
        if data.len() < 15 {
            return None;
        }

        if data[0] != 0x94 || data[1] != 0xC3 {
            return None;
        }

        let payload_len = u16::from_le_bytes([data[2], data[3]]) as usize;
        if data.len() < 15 + payload_len {
            return None;
        }

        let from = u32::from_le_bytes([data[4], data[5], data[6], data[7]]);
        let to = u32::from_le_bytes([data[8], data[9], data[10], data[11]]);
        let port_num = data[12];
        let hop_limit = data[13];
        let want_ack = data[14] != 0;
        let payload = data[15..15 + payload_len].to_vec();

        Some(Self {
            from,
            to,
            port_num,
            payload,
            hop_limit,
            want_ack,
        })
    }
}

pub struct LoRaBridge {
    config: LoRaConfig,
    tx_channel: mpsc::Sender<Vec<u8>>,
    #[allow(dead_code)]
    serial_task: tokio::task::JoinHandle<()>,
}

impl LoRaBridge {
    pub async fn new(
        config: &LoRaConfig,
        message_tx: mpsc::Sender<BridgeMessage>,
    ) -> Result<Self> {
        let port = tokio_serial::new(&config.serial_port, config.baud_rate)
            .open_native_async()
            .context(format!("Failed to open serial port: {}", config.serial_port))?;

        info!("Opened serial port {} at {} baud", config.serial_port, config.baud_rate);

        let (tx_channel, tx_rx) = mpsc::channel::<Vec<u8>>(64);

        let serial_task = tokio::spawn(Self::serial_io_task(
            port,
            message_tx,
            tx_rx,
            config.max_packet_size,
        ));

        Ok(Self {
            config: config.clone(),
            tx_channel,
            serial_task,
        })
    }

    pub fn get_tx_channel(&self) -> mpsc::Sender<Vec<u8>> {
        self.tx_channel.clone()
    }

    pub async fn send(&self, payload: Vec<u8>) -> Result<()> {
        if payload.len() > self.config.max_packet_size {
            return Err(anyhow::anyhow!("Payload too large for LoRa"));
        }

        self.tx_channel
            .send(payload)
            .await
            .context("Failed to queue packet for transmission")?;
        Ok(())
    }

    async fn serial_io_task(
        mut port: SerialStream,
        message_tx: mpsc::Sender<BridgeMessage>,
        mut tx_rx: mpsc::Receiver<Vec<u8>>,
        max_packet_size: usize,
    ) {
        let mut read_buf = BytesMut::with_capacity(1024);
        let mut scratch = [0u8; 256];

        loop {
            tokio::select! {
                Some(payload) = tx_rx.recv() => {
                    let packet = MeshPacket::broadcast(payload, 67);
                    let encoded = packet.encode();

                    if let Err(e) = port.write_all(&encoded).await {
                        error!("Failed to write to serial: {}", e);
                    } else {
                        debug!("Sent {} bytes over LoRa", encoded.len());
                    }
                }

                result = port.read(&mut scratch) => {
                    match result {
                        Ok(0) => {
                            warn!("Serial port closed");
                            break;
                        }
                        Ok(n) => {
                            read_buf.extend_from_slice(&scratch[..n]);

                            while read_buf.len() >= 15 {
                                if let Some(packet) = MeshPacket::decode(&read_buf) {
                                    let packet_len = 15 + packet.payload.len();
                                    read_buf.advance(packet_len);

                                    debug!(
                                        "Received LoRa packet from {:08X}: {} bytes",
                                        packet.from,
                                        packet.payload.len()
                                    );

                                    let _ = message_tx.try_send(
                                        BridgeMessage::LoRaPacket(packet.payload, packet.from)
                                    );
                                } else {
                                    if read_buf.len() > 0 && (read_buf[0] != 0x94 || (read_buf.len() > 1 && read_buf[1] != 0xC3)) {
                                        read_buf.advance(1);
                                    } else {
                                        break;
                                    }
                                }
                            }

                            if read_buf.len() > max_packet_size * 4 {
                                warn!("Read buffer overflow, clearing");
                                read_buf.clear();
                            }
                        }
                        Err(e) => {
                            error!("Serial read error: {}", e);
                            tokio::time::sleep(tokio::time::Duration::from_secs(1)).await;
                        }
                    }
                }
            }
        }
    }
}

pub struct MockLoRaBridge {
    tx_channel: mpsc::Sender<Vec<u8>>,
}

impl MockLoRaBridge {
    pub fn new() -> (Self, mpsc::Receiver<Vec<u8>>) {
        let (tx, rx) = mpsc::channel(64);
        (Self { tx_channel: tx }, rx)
    }

    pub fn get_tx_channel(&self) -> mpsc::Sender<Vec<u8>> {
        self.tx_channel.clone()
    }
}
