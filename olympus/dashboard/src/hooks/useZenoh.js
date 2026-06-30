/**
 * CERES OS - Zenoh WebSocket Hook
 * Connects to Zenoh REST API for real-time telemetry streaming
 */

import { useEffect, useRef, useCallback, useState } from 'react';
import useFleetStore from '../store/fleetStore';

const DEFAULT_ZENOH_URL = 'ws://localhost:8000';

/**
 * Custom hook for connecting to Zenoh WebSocket API
 * @param {string} baseUrl - Base URL of the Zenoh REST API (default: ws://localhost:8000)
 * @returns {object} Connection state and methods
 */
export const useZenoh = (baseUrl = DEFAULT_ZENOH_URL) => {
  const wsRef = useRef(null);
  const reconnectTimeoutRef = useRef(null);
  const [isConnected, setIsConnected] = useState(false);
  const [lastError, setLastError] = useState(null);
  
  const { 
    setConnectionStatus, 
    updateDrone, 
    updateDronePosition,
    addDetection,
    addTask,
    setLoraStatus,
  } = useFleetStore();

  // Message handler
  const handleMessage = useCallback((data) => {
    try {
      // Parse the Zenoh message
      // Expected format: { key: "ceres/...", value: {...}, timestamp: ... }
      const { key, value, timestamp } = data;

      // Decode base64-encoded value if needed
      let decodedValue = value;
      if (typeof value === 'string') {
        try {
          // Try to decode as base64 first
          const decoded = atob(value);
          decodedValue = JSON.parse(decoded);
        } catch (e) {
          // If base64 decode fails, try parsing as JSON directly
          try {
            decodedValue = JSON.parse(value);
          } catch (e2) {
            console.warn('[Zenoh] Could not parse value:', value);
            decodedValue = value;
          }
        }
      }

      // Route based on key pattern
      if (key.startsWith('ceres/swarm/') && key.endsWith('/telemetry')) {
        // Drone telemetry update
        const droneId = key.split('/')[2];
        const telemetry = decodedValue;

        console.log('[Zenoh] Received telemetry for', droneId, telemetry);

        updateDrone(droneId, {
          position: {
            lat: telemetry.position?.latitude || telemetry.lat,
            lon: telemetry.position?.longitude || telemetry.lon,
            alt: telemetry.position?.altitude || telemetry.alt || 0,
          },
          battery: telemetry.battery?.percentage || telemetry.battery || 0,
          status: telemetry.status,
          signalStrength: telemetry.mesh_rssi || telemetry.rssi || -60,
        });

        if (telemetry.position?.latitude || telemetry.lat) {
          updateDronePosition(droneId, {
            lat: telemetry.position?.latitude || telemetry.lat,
            lon: telemetry.position?.longitude || telemetry.lon,
            alt: telemetry.position?.altitude || telemetry.alt || 0,
          });
        }
      }
      else if (key.startsWith('ceres/detection/')) {
        // New detection
        addDetection(decodedValue);
      }
      else if (key.startsWith('ceres/task/')) {
        // Task update
        addTask(decodedValue);
      }
      else if (key === 'ceres/lora/status') {
        // LoRa mesh status
        setLoraStatus(decodedValue.status || 'ACTIVE');
      }

    } catch (error) {
      console.error('[Zenoh] Error processing message:', error, data);
    }
  }, [updateDrone, updateDronePosition, addDetection, addTask, setLoraStatus]);

  // Connect to Zenoh
  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return;
    }

    try {
      // Connect to Zenoh REST API WebSocket
      const ws = new WebSocket(`${baseUrl}/ws`);

      // Set binary type to handle Blob messages (Zenoh may send binary data)
      ws.binaryType = 'blob';

      ws.onopen = () => {
        console.log('[Zenoh] Connected to', baseUrl);
        setIsConnected(true);
        setLastError(null);
        setConnectionStatus('CONNECTED');
        
        // Subscribe to CERES topics
        ws.send(JSON.stringify({
          action: 'subscribe',
          key: 'ceres/**',
        }));
      };

      ws.onclose = (event) => {
        console.log('[Zenoh] Disconnected:', event.code, event.reason);
        setIsConnected(false);
        setConnectionStatus('DISCONNECTED');
        
        // Attempt reconnection after 5 seconds
        reconnectTimeoutRef.current = setTimeout(() => {
          console.log('[Zenoh] Attempting reconnection...');
          connect();
        }, 5000);
      };

      ws.onerror = (error) => {
        console.error('[Zenoh] WebSocket error:', error);
        setLastError(error);
        setIsConnected(false);  // ← Ensure state reflects error condition
        setConnectionStatus('ERROR');
      };

      ws.onmessage = async (event) => {
        try {
          // Debug: Check message type
          console.log('[Zenoh] Message type:', typeof event.data, event.data.constructor.name);

          let messageText = event.data;

          // Handle Blob or ArrayBuffer
          if (event.data instanceof Blob) {
            messageText = await event.data.text();
            console.log('[Zenoh] Blob message (first 200 chars):', messageText.substring(0, 200));
          } else if (event.data instanceof ArrayBuffer) {
            messageText = new TextDecoder().decode(event.data);
            console.log('[Zenoh] ArrayBuffer message (first 200 chars):', messageText.substring(0, 200));
          } else {
            console.log('[Zenoh] String message (first 200 chars):', messageText.substring(0, 200));
          }

          const data = JSON.parse(messageText);
          handleMessage(data);
        } catch (error) {
          console.error('[Zenoh] Failed to parse message:', error);
          if (typeof event.data === 'string') {
            console.error('[Zenoh] Raw string data (first 500 chars):', event.data.substring(0, 500));
          } else {
            console.error('[Zenoh] Binary data type:', event.data.constructor.name);
          }
        }
      };

      wsRef.current = ws;
      
    } catch (error) {
      console.error('[Zenoh] Connection failed:', error);
      setLastError(error);
      setConnectionStatus('DISCONNECTED');
    }
  }, [baseUrl, handleMessage, setConnectionStatus]);

  // Disconnect
  const disconnect = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
    }
    
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    
    setIsConnected(false);
    setConnectionStatus('DISCONNECTED');
  }, [setConnectionStatus]);

  // Publish to Zenoh
  const publish = useCallback((key, value) => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) {
      console.warn('[Zenoh] Cannot publish: not connected');
      return false;
    }

    try {
      wsRef.current.send(JSON.stringify({
        action: 'put',
        key,
        value: typeof value === 'string' ? value : JSON.stringify(value),
      }));
      return true;
    } catch (error) {
      console.error('[Zenoh] Publish failed:', error);
      return false;
    }
  }, []);

  // Send command to drone
  const sendDroneCommand = useCallback((droneId, command, params = {}) => {
    return publish(`ceres/command/${droneId}`, {
      command,
      ...params,
      timestamp: Date.now(),
    });
  }, [publish]);

  // Cleanup on unmount
  useEffect(() => {
    connect();
    
    return () => {
      disconnect();
    };
  }, [connect, disconnect]);

  return {
    isConnected,
    lastError,
    connect,
    disconnect,
    publish,
    sendDroneCommand,
  };
};

/**
 * EventSource-based hook for Zenoh (alternative to WebSocket)
 * Uses Server-Sent Events for one-way streaming
 */
export const useZenohEventSource = (baseUrl = DEFAULT_ZENOH_URL) => {
  const eventSourceRef = useRef(null);
  const [isConnected, setIsConnected] = useState(false);
  
  const { setConnectionStatus, updateDrone, addDetection } = useFleetStore();

  useEffect(() => {
    // Connect to Zenoh REST API EventSource endpoint
    const eventSource = new EventSource(`${baseUrl.replace('ws', 'http')}/ceres/**`);
    
    eventSource.onopen = () => {
      console.log('[Zenoh ES] Connected');
      setIsConnected(true);
      setConnectionStatus('CONNECTED');
    };

    eventSource.onerror = (error) => {
      console.error('[Zenoh ES] Error:', error);
      setIsConnected(false);
      setConnectionStatus('DISCONNECTED');
    };

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        // Process data similarly to WebSocket handler
        if (data.key && data.value) {
          // Handle telemetry, detections, etc.
        }
      } catch (error) {
        console.error('[Zenoh ES] Parse error:', error);
      }
    };

    eventSourceRef.current = eventSource;

    return () => {
      eventSource.close();
    };
  }, [baseUrl, setConnectionStatus]);

  return { isConnected };
};

export default useZenoh;
