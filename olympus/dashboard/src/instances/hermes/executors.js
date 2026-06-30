export const taskTypes = {
  INVESTIGATE: { label: 'Investigate', color: '#388bfd' },
  DROP_SUPPLIES: { label: 'Drop Supplies', color: '#3fb950' },
  MARK_LOCATION: { label: 'Mark Location', color: '#f85149' },
  RELAY_COMMS: { label: 'Relay Comms', color: '#d29922' },
};

export const detectionToTask = {
  PERSON_DETECTED: 'DROP_SUPPLIES',
  THERMAL_SIGNATURE: 'INVESTIGATE',
  DEBRIS_FIELD: 'MARK_LOCATION',
  VEHICLE_WRECKAGE: 'INVESTIGATE',
  SIGNAL_DETECTED: 'INVESTIGATE',
};
