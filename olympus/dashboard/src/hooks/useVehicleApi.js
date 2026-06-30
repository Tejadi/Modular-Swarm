/**
 * CERES OS - Vehicle API Hook
 * Connects the dashboard to the Vehicle API service via WebSocket (telemetry)
 * and REST endpoints (commands, mission control, health).
 *
 * WebSocket: real-time telemetry and detection streaming
 * REST: vehicle commands, mission lifecycle, health checks
 */

import { useEffect, useRef, useCallback, useState } from 'react';
import useFleetStore from '../store/fleetStore';

const DEFAULT_API_URL =
  process.env.REACT_APP_VEHICLE_API_URL || 'http://localhost:3001';

// API authentication token (read from env, never hardcoded)
const API_TOKEN = process.env.REACT_APP_CERES_API_KEY || '';

// Reconnection constants
const INITIAL_RECONNECT_DELAY_MS = 1000;
const MAX_RECONNECT_DELAY_MS = 30000;
const BACKOFF_MULTIPLIER = 2;

/**
 * Derives the WebSocket URL from an HTTP base URL.
 * http(s)://host -> ws(s)://host
 */
function toWsUrl(httpUrl) {
  return httpUrl.replace(/^http/, 'ws');
}

/**
 * Custom hook that bridges the React dashboard to the Vehicle API service.
 *
 * @param {string} [apiUrl] - Base HTTP URL of the Vehicle API.
 *   Falls back to REACT_APP_VEHICLE_API_URL env var, then http://localhost:3001.
 * @returns {{
 *   connected: boolean,
 *   sendCommand: (vehicleId: string, command: string, params?: object) => Promise<object>,
 *   startMission: () => Promise<object>,
 *   abortMission: () => Promise<object>,
 *   getMissionState: () => Promise<object>,
 *   getHealth: () => Promise<object>,
 * }}
 */
export const useVehicleApi = (apiUrl) => {
  const baseUrl = apiUrl || DEFAULT_API_URL;

  // ---------------------------------------------------------------------------
  // Refs (survive re-renders, no extra renders when mutated)
  // ---------------------------------------------------------------------------
  const wsRef = useRef(null);
  const reconnectTimerRef = useRef(null);
  const reconnectDelayRef = useRef(INITIAL_RECONNECT_DELAY_MS);
  const mountedRef = useRef(true);

  // ---------------------------------------------------------------------------
  // Local state
  // ---------------------------------------------------------------------------
  const [connected, setConnected] = useState(false);

  // ---------------------------------------------------------------------------
  // Zustand store selectors (destructured once, stable references)
  // ---------------------------------------------------------------------------
  const updateDrone = useFleetStore((state) => state.updateDrone);
  const addDetection = useFleetStore((state) => state.addDetection);
  const setConnectionStatus = useFleetStore((state) => state.setConnectionStatus);

  // ---------------------------------------------------------------------------
  // WebSocket message handler
  // ---------------------------------------------------------------------------
  const handleWsMessage = useCallback(
    (event) => {
      try {
        const message = JSON.parse(event.data);
        const { type } = message;

        if (type === 'telemetry') {
          // Expected shape: { type: "telemetry", vehicleId, ...fields }
          const { vehicleId, ...updates } = message;
          if (vehicleId) {
            updateDrone(vehicleId, updates);
          }
        } else if (type === 'detection') {
          // Expected shape: { type: "detection", detection: {...} } or flat
          const detection = message.detection || message;
          addDetection(detection);
        }
        // Unknown message types are silently ignored so the hook stays forward-compatible.
      } catch (err) {
        console.error('[VehicleApi] Failed to parse WebSocket message:', err);
      }
    },
    [updateDrone, addDetection],
  );

  // ---------------------------------------------------------------------------
  // WebSocket lifecycle helpers
  // ---------------------------------------------------------------------------

  /**
   * Schedule a reconnection attempt with exponential backoff.
   */
  const scheduleReconnect = useCallback(() => {
    if (!mountedRef.current) return;

    const delay = reconnectDelayRef.current;
    console.log(`[VehicleApi] Reconnecting in ${delay}ms...`);

    reconnectTimerRef.current = setTimeout(() => {
      // Increase delay for next attempt (capped at MAX)
      reconnectDelayRef.current = Math.min(
        reconnectDelayRef.current * BACKOFF_MULTIPLIER,
        MAX_RECONNECT_DELAY_MS,
      );
      connectWs(); // eslint-disable-line no-use-before-define
    }, delay);
  }, []); // intentionally empty - connectWs referenced via closure

  /**
   * Open a WebSocket connection to the Vehicle API telemetry endpoint.
   */
  const connectWs = useCallback(() => {
    // Tear down any existing connection first
    if (wsRef.current) {
      wsRef.current.onclose = null; // prevent triggering reconnect on intentional close
      wsRef.current.close();
      wsRef.current = null;
    }

    const tokenParam = API_TOKEN ? `?token=${encodeURIComponent(API_TOKEN)}` : '';
    const wsUrl = `${toWsUrl(baseUrl)}/api/v1/ws/telemetry${tokenParam}`;
    console.log('[VehicleApi] Connecting WebSocket to', wsUrl);

    try {
      const ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        console.log('[VehicleApi] WebSocket connected');
        // Reset backoff on successful connection
        reconnectDelayRef.current = INITIAL_RECONNECT_DELAY_MS;

        if (mountedRef.current) {
          setConnected(true);
          setConnectionStatus('CONNECTED');
        }
      };

      ws.onmessage = handleWsMessage;

      ws.onerror = (err) => {
        console.error('[VehicleApi] WebSocket error:', err);
      };

      ws.onclose = (event) => {
        console.log(
          '[VehicleApi] WebSocket closed:',
          event.code,
          event.reason,
        );

        if (mountedRef.current) {
          setConnected(false);
          setConnectionStatus('DISCONNECTED');
          scheduleReconnect();
        }
      };

      wsRef.current = ws;
    } catch (err) {
      console.error('[VehicleApi] WebSocket creation failed:', err);
      if (mountedRef.current) {
        setConnected(false);
        setConnectionStatus('DISCONNECTED');
        scheduleReconnect();
      }
    }
  }, [baseUrl, handleWsMessage, setConnectionStatus, scheduleReconnect]);

  // ---------------------------------------------------------------------------
  // REST helpers
  // ---------------------------------------------------------------------------

  /**
   * Generic fetch wrapper that prefixes the base URL and handles JSON.
   */
  const request = useCallback(
    async (method, path, body) => {
      const url = `${baseUrl}${path}`;
      const headers = { 'Content-Type': 'application/json' };
      if (API_TOKEN) {
        headers['Authorization'] = `Bearer ${API_TOKEN}`;
      }
      const options = {
        method,
        headers,
      };

      if (body !== undefined) {
        options.body = JSON.stringify(body);
      }

      const response = await fetch(url, options);

      // Attempt to parse JSON; return raw response if not JSON
      const contentType = response.headers.get('content-type') || '';
      const data = contentType.includes('application/json')
        ? await response.json()
        : await response.text();

      if (!response.ok) {
        const error = new Error(
          `Vehicle API ${method} ${path} failed: ${response.status}`,
        );
        error.status = response.status;
        error.data = data;
        throw error;
      }

      return data;
    },
    [baseUrl],
  );

  // ---------------------------------------------------------------------------
  // Public REST methods
  // ---------------------------------------------------------------------------

  /**
   * Send a command to a specific vehicle.
   * POST /api/v1/vehicles/{id}/command
   */
  const sendCommand = useCallback(
    (vehicleId, command, params = {}) =>
      request('POST', `/api/v1/vehicles/${vehicleId}/command`, {
        command,
        ...params,
      }),
    [request],
  );

  /**
   * Start the current mission.
   * POST /api/v1/mission/start
   */
  const startMission = useCallback(
    () => request('POST', '/api/v1/mission/start'),
    [request],
  );

  /**
   * Abort the current mission.
   * POST /api/v1/mission/abort
   */
  const abortMission = useCallback(
    () => request('POST', '/api/v1/mission/abort'),
    [request],
  );

  /**
   * Retrieve the current mission state.
   * GET /api/v1/mission
   */
  const getMissionState = useCallback(
    () => request('GET', '/api/v1/mission'),
    [request],
  );

  /**
   * Retrieve the API health status.
   * GET /api/v1/health
   */
  const getHealth = useCallback(
    () => request('GET', '/api/v1/health'),
    [request],
  );

  // ---------------------------------------------------------------------------
  // Effect: manage WebSocket connection lifecycle
  // ---------------------------------------------------------------------------
  useEffect(() => {
    mountedRef.current = true;
    connectWs();

    return () => {
      mountedRef.current = false;

      // Clear any pending reconnect timer
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }

      // Close the WebSocket cleanly
      if (wsRef.current) {
        wsRef.current.onclose = null; // avoid triggering reconnect during teardown
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [connectWs]);

  // ---------------------------------------------------------------------------
  // Public interface
  // ---------------------------------------------------------------------------
  return {
    connected,
    sendCommand,
    startMission,
    abortMission,
    getMissionState,
    getHealth,
  };
};
