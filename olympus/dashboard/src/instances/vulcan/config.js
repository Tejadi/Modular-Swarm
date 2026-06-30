export const operatingArea = {
  label: 'Facility',
  center: { lat: 29.7604, lon: -95.3698 },
  sizeMeters: 300,
  generatorType: 'facility',
};

export const vehicleDefaults = {
  scout: { altitude: 25, swathWidth: 8, speed: 3 },
  executor: { transitAltitude: 40, workAltitude: 3, speed: 5 },
  payload: { label: 'Sensor Bay', unit: '%', capacity: 100, barColor: 'bg-gotham-accent-blue', textColor: 'text-gotham-accent-blue' },
  initialFleet: {
    'scout-01': {
      id: 'scout-01', role: 'SCOUT', status: 'SCANNING',
      position: { lat: 29.7610, lon: -95.3705, alt: 25 },
      battery: 88, signalStrength: -52, currentTask: null, flightPath: [], voronoiRegion: null, lastUpdate: Date.now(),
    },
    'scout-02': {
      id: 'scout-02', role: 'SCOUT', status: 'SCANNING',
      position: { lat: 29.7615, lon: -95.3690, alt: 25 },
      battery: 91, signalStrength: -48, currentTask: null, flightPath: [], voronoiRegion: null, lastUpdate: Date.now(),
    },
    'executor-01': {
      id: 'executor-01', role: 'EXECUTOR', status: 'IDLE',
      position: { lat: 29.7598, lon: -95.3710, alt: 0 },
      battery: 96, signalStrength: -40, currentTask: null, tankLevel: 100, flightPath: [], lastUpdate: Date.now(),
    },
  },
  initialDetections: [
    { id: 'det-001', type: 'CORROSION', position: { lat: 29.7608, lon: -95.3700, alt: 0 }, confidence: 0.91, timestamp: Date.now() - 60000, status: 'PENDING', detectedBy: 'scout-01', assignedTo: null },
  ],
  initialTasks: [],
  baseStations: [
    { id: 'base-01', position: { lat: 29.7595, lon: -95.3715 }, status: 'ONLINE' },
  ],
};

export const environment = {
  siteName: 'Refinery Complex A',
  zones: [
    { id: 'tank-farm-a', name: 'Tank Farm A', metadata: { assetType: 'storage', label: 'STORAGE TANKS' }, color: '#4a5568', width: 100, height: 100 },
    { id: 'pipe-rack-1', name: 'Pipe Rack 1', metadata: { assetType: 'piping', label: 'PIPING' }, color: '#718096', width: 80, height: 120 },
    { id: 'cooling-tower', name: 'Cooling Tower', metadata: { assetType: 'cooling', label: 'COOLING' }, color: '#a0aec0', width: 60, height: 60 },
    { id: 'flare-stack', name: 'Flare Stack', metadata: { assetType: 'flare', label: 'FLARE' }, color: '#e53e3e', width: 40, height: 40 },
  ],
  structures: [
    { id: 'control-room', type: 'control-room', name: 'Control Room', offsetLat: -80, offsetLon: -100, dimensions: { width: 15, length: 20, height: 6 } },
    { id: 'pump-house', type: 'pump-house', name: 'Pump House', offsetLat: -60, offsetLon: -70, dimensions: { width: 8, length: 12, height: 4 } },
  ],
  hasRoads: true,
};
