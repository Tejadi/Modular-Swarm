// Real-only operating picture: NO seeded/demo systems. Every drone, detection,
// and task on the map comes live from the swarm via zenoh (useZenohPolling).
export const operatingArea = {
  label: 'Operating Area',
  center: { lat: 39.9526, lon: -75.1652 }, // Philadelphia (leader placeholder until GPS lock)
  sizeMeters: 500,
  generatorType: 'farm',
};

export const vehicleDefaults = {
  scout: { altitude: 35, swathWidth: 10, speed: 5 },
  executor: { transitAltitude: 50, workAltitude: 5, speed: 15 },
  payload: { label: 'Payload', unit: '', capacity: 0, barColor: 'bg-gotham-accent-orange', textColor: 'text-gotham-accent-orange' },
  // Real systems only — these start empty and fill from live telemetry.
  initialFleet: {},
  initialDetections: [],
  initialTasks: [],
  baseStations: [],
};

export const environment = {
  siteName: 'Operating Area',
  zones: [],
  structures: [],
  hasRoads: false,
};
