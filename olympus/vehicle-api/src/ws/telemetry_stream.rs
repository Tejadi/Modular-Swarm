use axum::{
    extract::{
        ws::{Message, WebSocket, WebSocketUpgrade},
        State,
    },
    response::IntoResponse,
};
use futures::{SinkExt, StreamExt};
use tracing::{debug, info, warn};

use crate::state::AppState;

pub async fn telemetry_ws_handler(
    ws: WebSocketUpgrade,
    State(state): State<AppState>,
) -> impl IntoResponse {
    ws.on_upgrade(move |socket| handle_socket(socket, state))
}

async fn handle_socket(socket: WebSocket, state: AppState) {
    let (mut sender, mut receiver) = socket.split();
    let mut rx = state.zenoh.subscribe_telemetry();

    info!("WebSocket client connected for telemetry stream");

    let send_task = tokio::spawn(async move {
        loop {
            match rx.recv().await {
                Ok(event) => {
                    let json = match serde_json::to_string(&event) {
                        Ok(j) => j,
                        Err(e) => {
                            warn!("Failed to serialize telemetry event: {e}");
                            continue;
                        }
                    };

                    if sender.send(Message::Text(json)).await.is_err() {
                        debug!("WebSocket send failed; client likely disconnected");
                        break;
                    }
                }
                Err(tokio::sync::broadcast::error::RecvError::Lagged(n)) => {
                    warn!("WebSocket client lagged, skipped {n} messages");
                }
                Err(tokio::sync::broadcast::error::RecvError::Closed) => {
                    debug!("Telemetry broadcast channel closed");
                    break;
                }
            }
        }
    });

    while let Some(msg) = receiver.next().await {
        match msg {
            Ok(Message::Close(_)) => {
                info!("WebSocket client sent close frame");
                break;
            }
            Ok(Message::Ping(data)) => {
                debug!("WebSocket ping received ({} bytes)", data.len());
            }
            Ok(_) => {}
            Err(e) => {
                warn!("WebSocket receive error: {e}");
                break;
            }
        }
    }

    send_task.abort();
    info!("WebSocket client disconnected");
}
