export const operatingArea = {
  label: 'Search Area',
  center: { lat: 37.7749, lon: -122.4194 },
  sizeMeters: 2000,
  generatorType: 'grid',
};

export const vehicleDefaults = {
  scout: { altitude: 60, swathWidth: 30, speed: 15 },
  executor: { transitAltitude: 80, workAltitude: 10, speed: 20 },
  payload: { label: 'Supply Payload', unit: 'kg', capacity: 5, barColor: 'bg-gotham-accent-green', textColor: 'text-gotham-accent-green' },
  initialFleet: {
    'scout-01': {
      id: 'scout-01', role: 'SCOUT', status: 'SCANNING',
      position: { lat: 37.7760, lon: -122.4210, alt: 60 },
      battery: 80, signalStrength: -58, currentTask: null, flightPath: [], voronoiRegion: null, lastUpdate: Date.now(),
    },
    'scout-02': {
      id: 'scout-02', role: 'SCOUT', status: 'SCANNING',
      position: { lat: 37.7770, lon: -122.4180, alt: 60 },
      battery: 87, signalStrength: -54, currentTask: null, flightPath: [], voronoiRegion: null, lastUpdate: Date.now(),
    },
    'scout-03': {
      id: 'scout-03', role: 'SCOUT', status: 'SCANNING',
      position: { lat: 37.7740, lon: -122.4200, alt: 60 },
      battery: 73, signalStrength: -65, currentTask: null, flightPath: [], voronoiRegion: null, lastUpdate: Date.now(),
    },
    'executor-01': {
      id: 'executor-01', role: 'EXECUTOR', status: 'IDLE',
      position: { lat: 37.7730, lon: -122.4220, alt: 0 },
      battery: 94, signalStrength: -42, currentTask: null, tankLevel: 5, flightPath: [], lastUpdate: Date.now(),
    },
  },
  initialDetections: [
    { id: 'det-001', type: 'THERMAL_SIGNATURE', position: { lat: 37.7755, lon: -122.4195, alt: 0 }, confidence: 0.76, timestamp: Date.now() - 60000, status: 'PENDING', detectedBy: 'scout-01', assignedTo: null },
  ],
  initialTasks: [],
  baseStations: [
    { id: 'base-01', position: { lat: 37.7725, lon: -122.4230 }, status: 'ONLINE' },
  ],
};

export const environment = {
  siteName: 'Search Zone Alpha',
  zones: [
    { id: 'grid-a1', name: 'Grid A1', metadata: { terrainType: 'FOREST', label: 'FOREST' }, color: '#2d3748', width: 800, height: 800 },
    { id: 'grid-a2', name: 'Grid A2', metadata: { terrainType: 'COASTAL', label: 'COASTAL' }, color: '#2c5282', width: 800, height: 800 },
    { id: 'grid-b1', name: 'Grid B1', metadata: { terrainType: 'URBAN', label: 'URBAN' }, color: '#4a5568', width: 800, height: 800 },
    { id: 'grid-b2', name: 'Grid B2', metadata: { terrainType: 'OPEN', label: 'OPEN' }, color: '#553c9a', width: 800, height: 800 },
  ],
  structures: [],
  hasRoads: false,
};
