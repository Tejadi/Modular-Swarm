export const taskTypes = {
  INVESTIGATE: { label: 'Investigate', color: '#388bfd' },
  MARK: { label: 'Mark Target', color: '#f85149' },
  PHOTOGRAPH: { label: 'Photograph', color: '#39d2c0' },
  RELAY: { label: 'Relay Comms', color: '#d29922' },
};

export const detectionToTask = {
  HOSTILE_ACTIVITY: 'INVESTIGATE',
  VEHICLE_DETECTED: 'PHOTOGRAPH',
  PERSON_DETECTED: 'PHOTOGRAPH',
  IED_SUSPECTED: 'MARK',
  STRUCTURAL_CHANGE: 'PHOTOGRAPH',
  THERMAL_ANOMALY: 'INVESTIGATE',
};
