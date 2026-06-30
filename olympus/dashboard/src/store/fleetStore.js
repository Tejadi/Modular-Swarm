import { create } from 'zustand';
import { getActiveInstance } from '../instances';
import { generateEnvironment } from '../utils/environmentGenerator';

const instance = getActiveInstance();

export const DroneStatus = {
  IDLE: { label: 'IDLE', color: 'text-gotham-text-tertiary', bgColor: 'bg-gotham-bg-elevated' },
  SCANNING: { label: 'SCANNING', color: 'text-gotham-accent-teal', bgColor: 'bg-gotham-accent-teal/20', pulse: true },
  TRANSITING: { label: 'TRANSIT', color: 'text-gotham-accent-yellow', bgColor: 'bg-gotham-accent-yellow/20', pulse: true },
  EXECUTING: { label: 'EXECUTING', color: 'text-gotham-accent-green', bgColor: 'bg-gotham-accent-green/20', pulse: true },
  RETURNING: { label: 'RETURNING', color: 'text-gotham-accent-blue', bgColor: 'bg-gotham-accent-blue/20' },
  CHARGING: { label: 'CHARGING', color: 'text-gotham-accent-purple', bgColor: 'bg-gotham-accent-purple/20' },
  EMERGENCY: { label: 'EMERGENCY', color: 'text-gotham-accent-red', bgColor: 'bg-gotham-accent-red/20', pulse: true },
  OFFLINE: { label: 'OFFLINE', color: 'text-gotham-text-tertiary', bgColor: 'bg-gotham-bg-tertiary' },
};

export const DroneRole = {
  SCOUT: { label: 'SCOUT', color: 'text-gotham-accent-teal', icon: 'S', cssColor: '#39d2c0' },
  EXECUTOR: { label: 'EXECUTOR', color: 'text-gotham-accent-orange', icon: 'E', cssColor: '#db6d28' },
};

export const DetectionType = instance.detectionTypes;

export const MissionPhase = {
  IDLE: 'IDLE',
  PLANNING: 'PLANNING',
  DEPLOYING: 'DEPLOYING',
  SCANNING: 'SCANNING',
  EXECUTING: 'EXECUTING',
  PAUSED: 'PAUSED',
  EMERGENCY: 'EMERGENCY',
  COMPLETE: 'COMPLETE',
};

const OPERATING_CENTER = instance.operatingArea.center;

const initialDrones = instance.vehicleDefaults.initialFleet;
const initialDetections = instance.vehicleDefaults.initialDetections || [];
const initialTasks = instance.vehicleDefaults.initialTasks || [];
const baseStations = instance.vehicleDefaults.baseStations || [];

const syntheticFarm = generateEnvironment(
  instance,
  OPERATING_CENTER.lat,
  OPERATING_CENTER.lon,
  instance.operatingArea.sizeMeters || 500,
);
const fieldBoundary = syntheticFarm.boundary;

const defaultLayers = {
  satellite: { visible: true, opacity: 1.0 },
  fieldBoundaries: { visible: true, opacity: 0.8 },
  coverageZones: { visible: true, opacity: 0.3 },
  flightPaths: { visible: true, opacity: 0.5 },
  detections: { visible: true, opacity: 1.0 },
  baseStations: { visible: true, opacity: 1.0 },
  markers: { visible: true, opacity: 1.0 },
};

export const useFleetStore = create((set, get) => ({
  drones: initialDrones,
  detections: initialDetections,
  tasks: initialTasks,
  baseStations: baseStations,
  fieldBoundary: fieldBoundary,
  syntheticFarm: syntheticFarm,
  droneRoutes: {},
  selectedDrone: null,
  selectedDetection: null,
  connectionStatus: 'CONNECTED',
  loraStatus: 'ACTIVE',
  viewMode: 'ALL',
  showVoronoi: true,
  showFlightPaths: true,
  showDetections: true,
  missionActive: true,
  alerts: [],

  missionPhase: MissionPhase.SCANNING,
  missionStartTime: Date.now() - 5000,
  missionElapsedSeconds: 0,

  drawings: [],
  assignedZones: [],
  drawingBoundaries: [],
  markers: [],

  metrics: {
    coveragePercent: 0,
    totalDistanceTraveled: {},
    energyEfficiency: {},
    taskCompletionRate: 0,
    averageBattery: 0,
    coverageHistory: [],
  },

  layers: defaultLayers,

  setLayerVisibility: (layerKey, visible) => set((state) => ({
    layers: {
      ...state.layers,
      [layerKey]: { ...state.layers[layerKey], visible },
    },
    ...(layerKey === 'coverageZones' ? { showVoronoi: visible } : {}),
    ...(layerKey === 'flightPaths' ? { showFlightPaths: visible } : {}),
    ...(layerKey === 'detections' ? { showDetections: visible } : {}),
  })),

  setLayerOpacity: (layerKey, opacity) => set((state) => ({
    layers: {
      ...state.layers,
      [layerKey]: { ...state.layers[layerKey], opacity },
    },
  })),

  addDrawing: (drawing) => set((state) => ({
    drawings: [...state.drawings, { ...drawing, id: drawing.id || `drawing-${Date.now()}` }],
  })),
  removeDrawing: (id) => set((state) => ({
    drawings: state.drawings.filter((d) => d.id !== id),
  })),
  clearDrawings: () => set({ drawings: [] }),

  addAssignedZone: (zone) => set((state) => ({
    assignedZones: [...state.assignedZones, { ...zone, id: zone.id || `zone-${Date.now()}` }],
  })),
  removeAssignedZone: (id) => set((state) => ({
    assignedZones: state.assignedZones.filter((z) => z.id !== id),
  })),
  reassignZone: (zoneId, newDroneId) => set((state) => ({
    assignedZones: state.assignedZones.map((z) =>
      z.id === zoneId ? { ...z, droneId: newDroneId } : z
    ),
  })),

  addBoundary: (boundary) => set((state) => ({
    drawingBoundaries: [...state.drawingBoundaries, { ...boundary, id: boundary.id || `boundary-${Date.now()}` }],
  })),
  removeBoundary: (id) => set((state) => ({
    drawingBoundaries: state.drawingBoundaries.filter((b) => b.id !== id),
  })),
  clearBoundaries: () => set({ drawingBoundaries: [] }),

  addMarker: (marker) => set((state) => ({
    markers: [...state.markers, { ...marker, id: marker.id || `marker-${Date.now()}` }],
  })),
  removeMarker: (id) => set((state) => ({
    markers: state.markers.filter((m) => m.id !== id),
  })),
  clearMarkers: () => set({ markers: [] }),

  updateMetrics: () => set((state) => {
    const droneList = Object.values(state.drones);
    const activeDrones = droneList.filter((d) => d.status !== 'OFFLINE');

    const averageBattery = activeDrones.length > 0
      ? activeDrones.reduce((s, d) => s + d.battery, 0) / activeDrones.length
      : 0;

    let totalWaypoints = 0;
    let visitedWaypoints = 0;
    for (const route of Object.values(state.droneRoutes)) {
      totalWaypoints += route.route?.length || 0;
      visitedWaypoints += route.currentWaypointIndex || 0;
    }
    const coveragePercent = totalWaypoints > 0
      ? Math.min(100, Math.round((visitedWaypoints / totalWaypoints) * 100))
      : 0;

    const totalTasks = state.tasks.length;
    const completedTasks = state.tasks.filter((t) => t.status === 'COMPLETED').length;
    const taskCompletionRate = totalTasks > 0
      ? Math.round((completedTasks / totalTasks) * 100)
      : 0;

    const history = [...(state.metrics.coverageHistory || [])];
    history.push({ time: Date.now(), percent: coveragePercent });
    if (history.length > 60) history.shift();

    return {
      metrics: {
        ...state.metrics,
        coveragePercent,
        averageBattery: Math.round(averageBattery),
        taskCompletionRate,
        coverageHistory: history,
      },
    };
  }),

  addDistanceTraveled: (droneId, meters) => set((state) => ({
    metrics: {
      ...state.metrics,
      totalDistanceTraveled: {
        ...state.metrics.totalDistanceTraveled,
        [droneId]: (state.metrics.totalDistanceTraveled[droneId] || 0) + meters,
      },
    },
  })),

  setMissionPhase: (phase) => set({ missionPhase: phase }),
  startMission: () => set({
    missionPhase: MissionPhase.PLANNING,
    missionStartTime: Date.now(),
    missionActive: true,
  }),
  pauseMission: () => set((state) => ({
    missionPhase: state.missionPhase === MissionPhase.PAUSED ? MissionPhase.SCANNING : MissionPhase.PAUSED,
  })),

  updateDrone: (id, updates) => set((state) => ({
    drones: {
      ...state.drones,
      [id]: { ...state.drones[id], ...updates, lastUpdate: Date.now() },
    },
  })),

  updateDronePosition: (id, position) => set((state) => {
    const drone = state.drones[id];
    if (!drone) return state;
    const newPath = [...(drone.flightPath || []), position].slice(-100);
    return {
      drones: {
        ...state.drones,
        [id]: { ...drone, position, flightPath: newPath, lastUpdate: Date.now() },
      },
    };
  }),

  // Liveness: zenoh retains the last telemetry, so a node's lastUpdate (poll
  // time) always looks fresh. Staleness is judged on lastSeenMs — the zenoh
  // STORE time, which stops advancing when the node actually stops publishing.
  // Stale -> gray (OFFLINE); very stale -> dropped from the map entirely.
  pruneStaleDrones: (staleMs, dropMs) => set((state) => {
    const now = Date.now();
    const next = {};
    let changed = false;
    for (const [id, d] of Object.entries(state.drones)) {
      const seen = d.lastSeenMs || d.lastUpdate || now;
      const age = now - seen;
      if (age > dropMs) { changed = true; continue; }      // drop off the map
      if (age > staleMs && d.status !== 'OFFLINE') {
        next[id] = { ...d, status: 'OFFLINE' };             // gray out
        changed = true;
      } else {
        next[id] = d;
      }
    }
    return changed ? { drones: next } : state;
  }),

  // Drop detections whose camera stopped re-publishing (target left frame). Uses
  // the zenoh store time (lastSeenMs), which freezes when the detector goes quiet.
  pruneStaleDetections: (ttlMs) => set((state) => {
    const now = Date.now();
    const kept = state.detections.filter((d) => !d.lastSeenMs || now - d.lastSeenMs <= ttlMs);
    return kept.length === state.detections.length ? state : { detections: kept };
  }),

  addDetection: (detection) => set((state) => {
    // The camera re-publishes the same detection key (person-0, person-1, ...)
    // continuously; update-in-place so the map shows one live marker per target
    // instead of accumulating thousands. Only a genuinely new key raises an alert.
    const key = detection.key;
    if (key) {
      const idx = state.detections.findIndex((d) => d.key === key);
      if (idx >= 0) {
        const next = state.detections.slice();
        next[idx] = { ...next[idx], ...detection };
        return { detections: next };
      }
    }
    const id = key ? `det-${key}` : `det-${Date.now()}`;
    return {
      detections: [...state.detections, { ...detection, id }],
      alerts: [...state.alerts, {
        id: `alert-${Date.now()}`,
        type: 'detection',
        message: `New ${detection.type || 'contact'} detected by ${detection.detectedBy || 'sensor'}`,
        timestamp: Date.now(),
      }],
    };
  }),

  updateDetection: (id, updates) => set((state) => ({
    detections: state.detections.map((d) =>
      d.id === id ? { ...d, ...updates } : d
    ),
  })),

  addTask: (task) => set((state) => ({
    tasks: [...state.tasks, { ...task, id: `task-${Date.now()}` }],
  })),

  updateTask: (id, updates) => set((state) => ({
    tasks: state.tasks.map((t) =>
      t.id === id ? { ...t, ...updates } : t
    ),
  })),

  setSelectedDrone: (id) => set({ selectedDrone: id }),
  setSelectedDetection: (id) => set({ selectedDetection: id }),
  setViewMode: (mode) => set({ viewMode: mode }),
  toggleVoronoi: () => set((state) => ({ showVoronoi: !state.showVoronoi })),
  toggleFlightPaths: () => set((state) => ({ showFlightPaths: !state.showFlightPaths })),
  toggleDetections: () => set((state) => ({ showDetections: !state.showDetections })),

  setDroneRoutes: (routes) => set({ droneRoutes: routes }),
  updateWaypointIndex: (droneId, index) => set((state) => ({
    droneRoutes: {
      ...state.droneRoutes,
      [droneId]: {
        ...state.droneRoutes[droneId],
        currentWaypointIndex: index,
      },
    },
  })),

  sendCommand: (droneId, command) => {
    console.log(`[CERES] Command -> ${droneId}: ${command}`);
    switch (command) {
      case 'ABORT':
        set((s) => ({
          drones: { ...s.drones, [droneId]: { ...s.drones[droneId], status: 'EMERGENCY' } },
        }));
        break;
      case 'RTL':
        set((s) => ({
          drones: { ...s.drones, [droneId]: { ...s.drones[droneId], status: 'RETURNING' } },
        }));
        break;
      case 'PAUSE':
        set((s) => ({
          drones: { ...s.drones, [droneId]: { ...s.drones[droneId], status: 'IDLE' } },
        }));
        break;
      default:
        break;
    }
  },

  sendGlobalCommand: (command) => {
    console.log(`[CERES] Global command: ${command}`);
    const state = get();
    if (command === 'ABORT_ALL') {
      const updatedDrones = {};
      Object.keys(state.drones).forEach((id) => {
        updatedDrones[id] = { ...state.drones[id], status: 'EMERGENCY' };
      });
      set({
        drones: updatedDrones,
        missionActive: false,
        missionPhase: MissionPhase.EMERGENCY,
        alerts: [...state.alerts, {
          id: `alert-${Date.now()}`,
          type: 'emergency',
          message: 'EMERGENCY ABORT - All vehicles commanded to land',
          timestamp: Date.now(),
        }],
      });
    } else if (command === 'RTL_ALL') {
      const updatedDrones = {};
      Object.keys(state.drones).forEach((id) => {
        updatedDrones[id] = { ...state.drones[id], status: 'RETURNING' };
      });
      set({ drones: updatedDrones });
    }
  },

  dismissAlert: (id) => set((state) => ({
    alerts: state.alerts.filter((a) => a.id !== id),
  })),

  // Relocate the operating center and regenerate the environment
  setOperatingCenter: (lat, lon) => {
    const newEnv = generateEnvironment(
      instance,
      lat,
      lon,
      instance.operatingArea.sizeMeters || 500,
    );
    const origDrones = instance.vehicleDefaults.initialFleet;
    const origCenter = instance.operatingArea.center;
    const relocatedDrones = {};
    Object.entries(origDrones).forEach(([id, drone]) => {
      const offsetLat = drone.position.lat - origCenter.lat;
      const offsetLon = drone.position.lon - origCenter.lon;
      relocatedDrones[id] = {
        ...drone,
        position: { lat: lat + offsetLat, lon: lon + offsetLon, alt: drone.position.alt },
        flightPath: [],
        lastUpdate: Date.now(),
      };
    });
    const relocatedBases = (instance.vehicleDefaults.baseStations || []).map((bs) => ({
      ...bs,
      position: {
        lat: lat + (bs.position.lat - origCenter.lat),
        lon: lon + (bs.position.lon - origCenter.lon),
      },
    }));
    set({
      drones: relocatedDrones,
      baseStations: relocatedBases,
      syntheticFarm: newEnv,
      fieldBoundary: newEnv.boundary,
      droneRoutes: {},
      detections: [],
    });
  },

  setConnectionStatus: (status) => set({ connectionStatus: status }),
  setLoraStatus: (status) => set({ loraStatus: status }),

  getScouts: () => Object.values(get().drones).filter((d) => d.role === 'SCOUT'),
  getExecutors: () => Object.values(get().drones).filter((d) => d.role === 'EXECUTOR'),
  getActiveDrones: () => Object.values(get().drones).filter((d) => d.status !== 'OFFLINE'),
  getPendingDetections: () => get().detections.filter((d) => d.status === 'PENDING'),
}));

export default useFleetStore;
