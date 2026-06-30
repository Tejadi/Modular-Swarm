export const detectionTypes = {
  HOSTILE_ACTIVITY: { label: 'Hostile Activity', color: '#f85149', priority: 'critical', severity: 'critical' },
  VEHICLE_DETECTED: { label: 'Vehicle', color: '#db6d28', priority: 'high', severity: 'high' },
  PERSON_DETECTED: { label: 'Personnel', color: '#d29922', priority: 'medium', severity: 'medium' },
  IED_SUSPECTED: { label: 'IED Suspected', color: '#f85149', priority: 'critical', severity: 'critical' },
  STRUCTURAL_CHANGE: { label: 'Structure Change', color: '#388bfd', priority: 'medium', severity: 'medium' },
  THERMAL_ANOMALY: { label: 'Thermal Anomaly', color: '#a371f7', priority: 'high', severity: 'high' },
};

export const detectionGeneration = {
  probability: 0.002,
  types: [
    { type: 'PERSON_DETECTED', weight: 0.30, severity: 'medium', descriptionTemplate: 'Personnel movement observed in {zone}', icon: '\u{1F464}' },
    { type: 'VEHICLE_DETECTED', weight: 0.25, severity: 'high', descriptionTemplate: 'Unidentified vehicle detected in {zone}', icon: '\u{1F697}' },
    { type: 'HOSTILE_ACTIVITY', weight: 0.15, severity: 'high', descriptionTemplate: 'Hostile activity detected in {zone}', icon: '\u26A0\uFE0F' },
    { type: 'STRUCTURAL_CHANGE', weight: 0.15, severity: 'medium', descriptionTemplate: 'Structural change detected in {zone}', icon: '\u{1F3D7}\uFE0F' },
    { type: 'THERMAL_ANOMALY', weight: 0.10, severity: 'high', descriptionTemplate: 'Thermal anomaly detected in {zone}', icon: '\u{1F525}' },
    { type: 'IED_SUSPECTED', weight: 0.05, severity: 'high', descriptionTemplate: 'Suspected IED/hazard in {zone} - exercise extreme caution', icon: '\u{1F4A3}' },
  ],
};
