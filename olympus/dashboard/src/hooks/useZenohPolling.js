/**
 * CERES OS - Zenoh HTTP Polling Hook
 * Uses REST API polling for telemetry updates (fallback for WebSocket issues)
 */

import { useEffect, useRef, useCallback, useState } from 'react';
import useFleetStore from '../store/fleetStore';

const DEFAULT_ZENOH_URL = 'http://localhost:8000';
const POLL_INTERVAL = 1000; // Poll every 1 second
const STALE_MS = 10000;     // no fresh telemetry this long -> gray (OFFLINE)
const DROP_MS = 30000;      // ... this long -> remove from the map entirely
const DETECTION_TTL = 8000; // a detection clears this long after its target leaves frame

// The leader is the "Command Station"; every other module is "Member N",
// numbered by first-seen order and kept stable for the session.
const memberNumbers = new Map();
let nextMemberNum = 0;
const isLeaderNode = (telemetry) =>
  telemetry?.swarm?.name === 'leader' ||
  telemetry?.swarm?.is_leader === true ||
  telemetry?.role === 'leader';
const displayNameFor = (droneId, telemetry) => {
  if (isLeaderNode(telemetry)) return 'Command Station';
  if (!memberNumbers.has(droneId)) memberNumbers.set(droneId, ++nextMemberNum);
  return `Member ${memberNumbers.get(droneId)}`;
};

/**
 * Custom hook for polling Zenoh REST API
 * @param {string} baseUrl - Base URL of the Zenoh REST API
 * @returns {object} Connection state and methods
 */
export const useZenohPolling = (baseUrl = DEFAULT_ZENOH_URL) => {
  const pollIntervalRef = useRef(null);
  const isConnectedRef = useRef(false); // Use ref to avoid recreating poll callback
  const [isConnected, setIsConnected] = useState(false);
  const [lastError, setLastError] = useState(null);

  const {
    setConnectionStatus,
    updateDrone,
    updateDronePosition,
    addDetection,
    addTask,
    pruneStaleDrones,
    pruneStaleDetections,
  } = useFleetStore();

  // Message handler
  const handleMessage = useCallback((data) => {
    try {
      const { key, value, time } = data;
      // zenoh STORE time -> ms; this stops advancing when a node stops
      // publishing (unlike the poll time), so it's the real freshness signal.
      const lastSeenMs = time ? (Date.parse(String(time).split('/')[0]) || Date.now()) : Date.now();

      // Decode base64-encoded value
      let decodedValue = value;
      if (typeof value === 'string') {
        try {
          const decoded = atob(value);
          decodedValue = JSON.parse(decoded);
        } catch (e) {
          console.warn('[Zenoh] Could not decode value:', value);
          return;
        }
      }

      // Route based on key pattern
      if (key.startsWith('ceres/swarm/') && key.endsWith('/telemetry')) {
        const droneId = key.split('/')[2];
        const telemetry = decodedValue;
        const leader = isLeaderNode(telemetry);

        updateDrone(droneId, {
          id: droneId, // Ensure drone has its ID
          name: displayNameFor(droneId, telemetry),   // "Command Station" / "Member N"
          isLeader: leader,
          role: telemetry.role?.toUpperCase() || 'MODULE',
          position: {
            lat: telemetry.position?.latitude ?? telemetry.lat,
            lon: telemetry.position?.longitude ?? telemetry.lon,
            alt: telemetry.position?.altitude ?? telemetry.alt ?? 0,
          },
          heading: telemetry.heading ?? 0,           // full pose: orientation
          battery: telemetry.battery?.percentage ?? telemetry.battery ?? 0,
          // Real status; default to a valid active state (never UNKNOWN/pending).
          status: telemetry.status?.toUpperCase() || 'SCANNING',
          signalStrength: telemetry.mesh_rssi ?? telemetry.rssi ?? -60,
          // Full swarm metadata for click popups (sensors, ekf_flags, pose, caps).
          swarm: telemetry.swarm || {},
          positionSource: telemetry.swarm?.position_source || telemetry.position_source,
          lastSeenMs,                  // for the liveness TTL (gray/drop stale nodes)
          lastUpdate: Date.now(),
          flightPath: [], // Initialize empty flight path if new drone
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
        // Normalize position to {lat,lon} (camera publishes latitude/longitude)
        // so the map + detection panel read one shape; stable key dedupes repeats.
        const dv = decodedValue || {};
        const p = dv.position || {};
        const lat = p.lat ?? p.latitude;
        const lon = p.lon ?? p.longitude;
        addDetection({
          ...dv,
          position: { ...p, lat, lon, alt: p.alt ?? p.altitude ?? 0 },
          key: key.split('/').pop(),
          lastSeenMs,
        });
      }
      else if (key.startsWith('ceres/task/')) {
        addTask(decodedValue);
      }

    } catch (error) {
      console.error('[Zenoh] Error processing message:', error, data);
    }
  }, [updateDrone, updateDronePosition, addDetection, addTask]);

  // Poll function
  const poll = useCallback(async () => {
    try {
      // Poll all CERES topics
      const response = await fetch(`${baseUrl}/ceres/**`);

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const messages = await response.json();

      // Mark as connected on first successful poll
      if (!isConnectedRef.current) {
        console.log('[Zenoh] Connected to REST API at', baseUrl);
        isConnectedRef.current = true;
        setIsConnected(true);
        setLastError(null);
        setConnectionStatus('CONNECTED');
      }

      // Process each message
      if (Array.isArray(messages)) {
        messages.forEach(handleMessage);
      }

      // Liveness: gray out / drop nodes whose zenoh store time has gone stale.
      pruneStaleDrones(STALE_MS, DROP_MS);
      pruneStaleDetections(DETECTION_TTL);

    } catch (error) {
      console.error('[Zenoh] Poll error:', error);
      setLastError(error);
      isConnectedRef.current = false;
      setIsConnected(false);
      setConnectionStatus('ERROR');
    }
  }, [baseUrl, handleMessage, setConnectionStatus, pruneStaleDrones, pruneStaleDetections]);

  // Start polling
  const connect = useCallback(() => {
    if (pollIntervalRef.current) {
      return; // Already polling
    }

    console.log('[Zenoh] Starting polling at', baseUrl);
    setConnectionStatus('CONNECTING');

    // Initial poll
    poll();

    // Set up interval
    pollIntervalRef.current = setInterval(poll, POLL_INTERVAL);

  }, [baseUrl, poll, setConnectionStatus]);

  // Stop polling
  const disconnect = useCallback(() => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
    setIsConnected(false);
    setConnectionStatus('DISCONNECTED');
    console.log('[Zenoh] Polling stopped');
  }, [setConnectionStatus]);

  // Auto-connect on mount
  useEffect(() => {
    connect();

    return () => {
      disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Run only on mount/unmount to avoid infinite reconnection loop

  return {
    isConnected,
    lastError,
    connect,
    disconnect,
  };
};

export default useZenohPolling;
