export const detectionTypes = {
  STRUCTURAL_CRACK: { label: 'Structural Crack', color: '#f85149', priority: 'critical', severity: 'critical' },
  CORROSION: { label: 'Corrosion', color: '#db6d28', priority: 'high', severity: 'high' },
  THERMAL_ANOMALY: { label: 'Thermal Anomaly', color: '#a371f7', priority: 'high', severity: 'high' },
  LEAK_DETECTED: { label: 'Leak', color: '#388bfd', priority: 'critical', severity: 'critical' },
  VEGETATION_ENCROACHMENT: { label: 'Vegetation', color: '#3fb950', priority: 'low', severity: 'low' },
  SURFACE_DEFORMATION: { label: 'Deformation', color: '#d29922', priority: 'medium', severity: 'medium' },
};

export const detectionGeneration = {
  probability: 0.004,
  types: [
    { type: 'CORROSION', weight: 0.25, severity: 'high', descriptionTemplate: 'Corrosion pattern found on {zone}', icon: '🪨' },
    { type: 'STRUCTURAL_CRACK', weight: 0.20, severity: 'high', descriptionTemplate: 'Structural crack detected on {zone}', icon: '🚨' },
    { type: 'THERMAL_ANOMALY', weight: 0.15, severity: 'high', descriptionTemplate: 'Thermal anomaly at {zone} - possible insulation failure', icon: '🔥' },
    { type: 'LEAK_DETECTED', weight: 0.15, severity: 'high', descriptionTemplate: 'Fluid leak detected at {zone}', icon: '💧' },
    { type: 'VEGETATION_ENCROACHMENT', weight: 0.15, severity: 'low', descriptionTemplate: 'Vegetation encroachment on {zone}', icon: '🌿' },
    { type: 'SURFACE_DEFORMATION', weight: 0.10, severity: 'medium', descriptionTemplate: 'Surface deformation detected on {zone}', icon: '⚠️' },
  ],
};
