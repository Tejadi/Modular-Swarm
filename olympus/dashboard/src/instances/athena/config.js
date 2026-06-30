export const operatingArea = {
  label: 'Sector',
  center: { lat: 34.0522, lon: -118.2437 },
  sizeMeters: 1000,
  generatorType: 'grid',
};

export const vehicleDefaults = {
  scout: { altitude: 80, swathWidth: 20, speed: 12 },
  executor: { transitAltitude: 100, workAltitude: 30, speed: 20 },
  payload: { label: 'Payload Bay', unit: '%', capacity: 100, barColor: 'bg-gotham-accent-red', textColor: 'text-gotham-accent-red' },
  initialFleet: {
    'scout-01': {
      id: 'scout-01', role: 'SCOUT', status: 'SCANNING',
      position: { lat: 34.0535, lon: -118.2450, alt: 80 },
      battery: 82, signalStrength: -60, currentTask: null, flightPath: [], voronoiRegion: null, lastUpdate: Date.now(),
    },
    'scout-02': {
      id: 'scout-02', role: 'SCOUT', status: 'SCANNING',
      position: { lat: 34.0540, lon: -118.2425, alt: 80 },
      battery: 90, signalStrength: -55, currentTask: null, flightPath: [], voronoiRegion: null, lastUpdate: Date.now(),
    },
    'scout-03': {
      id: 'scout-03', role: 'SCOUT', status: 'SCANNING',
      position: { lat: 34.0510, lon: -118.2430, alt: 80 },
      battery: 75, signalStrength: -62, currentTask: null, flightPath: [], voronoiRegion: null, lastUpdate: Date.now(),
    },
    'executor-01': {
      id: 'executor-01', role: 'EXECUTOR', status: 'IDLE',
      position: { lat: 34.0500, lon: -118.2460, alt: 0 },
      battery: 95, signalStrength: -45, currentTask: null, tankLevel: 100, flightPath: [], lastUpdate: Date.now(),
    },
    'executor-02': {
      id: 'executor-02', role: 'EXECUTOR', status: 'TRANSITING',
      position: { lat: 34.0528, lon: -118.2415, alt: 100 },
      battery: 68, signalStrength: -70, currentTask: 'task-001', tankLevel: 100, flightPath: [], lastUpdate: Date.now(),
    },
  },
  initialDetections: [
    { id: 'det-001', type: 'VEHICLE_DETECTED', position: { lat: 34.0530, lon: -118.2440, alt: 0 }, confidence: 0.88, timestamp: Date.now() - 60000, status: 'PENDING', detectedBy: 'scout-01', assignedTo: null },
    { id: 'det-002', type: 'PERSON_DETECTED', position: { lat: 34.0545, lon: -118.2420, alt: 0 }, confidence: 0.79, timestamp: Date.now() - 30000, status: 'ASSIGNED', detectedBy: 'scout-02', assignedTo: 'executor-02' },
  ],
  initialTasks: [
    { id: 'task-001', type: 'PHOTOGRAPH', targetPosition: { lat: 34.0545, lon: -118.2420 }, status: 'IN_PROGRESS', assignedTo: 'executor-02', createdAt: Date.now() - 120000, priority: 'high' },
  ],
  baseStations: [
    { id: 'base-01', position: { lat: 34.0495, lon: -118.2470 }, status: 'ONLINE' },
    { id: 'base-02', position: { lat: 34.0555, lon: -118.2410 }, status: 'ONLINE' },
  ],
};

export const environment = {
  siteName: 'Sector Alpha',
  zones: [
    { id: 'sector-a1', name: 'Sector A1', metadata: { threatLevel: 'LOW', label: 'LOW THREAT' }, color: '#1c6e3a', width: 400, height: 400 },
    { id: 'sector-a2', name: 'Sector A2', metadata: { threatLevel: 'MEDIUM', label: 'MED THREAT' }, color: '#6e4a1c', width: 400, height: 400 },
    { id: 'sector-b1', name: 'Sector B1', metadata: { threatLevel: 'HIGH', label: 'HIGH THREAT' }, color: '#6e1c1c', width: 400, height: 400 },
    { id: 'sector-b2', name: 'Sector B2', metadata: { threatLevel: 'LOW', label: 'LOW THREAT' }, color: '#1c4a6e', width: 400, height: 400 },
  ],
  structures: [],
  hasRoads: false,
};
