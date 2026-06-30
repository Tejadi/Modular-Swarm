export const detectionTypes = {
  PERSON_DETECTED: { label: 'Person Detected', color: '#f85149', priority: 'critical', severity: 'critical' },
  THERMAL_SIGNATURE: { label: 'Thermal Signature', color: '#db6d28', priority: 'critical', severity: 'critical' },
  DEBRIS_FIELD: { label: 'Debris Field', color: '#d29922', priority: 'high', severity: 'high' },
  VEHICLE_WRECKAGE: { label: 'Vehicle Wreckage', color: '#388bfd', priority: 'high', severity: 'high' },
  SIGNAL_DETECTED: { label: 'Signal Detected', color: '#3fb950', priority: 'critical', severity: 'critical' },
};

export const detectionGeneration = {
  probability: 0.002,
  types: [
    { type: 'PERSON_DETECTED', weight: 0.30, severity: 'high', descriptionTemplate: 'Person detected in {zone} - possible survivor', icon: '👤' },
    { type: 'THERMAL_SIGNATURE', weight: 0.25, severity: 'high', descriptionTemplate: 'Thermal signature in {zone} - requires investigation', icon: '🔥' },
    { type: 'DEBRIS_FIELD', weight: 0.20, severity: 'medium', descriptionTemplate: 'Debris field identified in {zone}', icon: '🪨' },
    { type: 'VEHICLE_WRECKAGE', weight: 0.15, severity: 'high', descriptionTemplate: 'Vehicle wreckage spotted in {zone}', icon: '🚗' },
    { type: 'SIGNAL_DETECTED', weight: 0.10, severity: 'high', descriptionTemplate: 'Electronic signal detected from {zone}', icon: '📡' },
  ],
};
