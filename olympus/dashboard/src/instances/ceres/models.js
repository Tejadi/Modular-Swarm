// General / defense detection taxonomy (was crop-stress agriculture).
export const detectionTypes = {
  PERSON: { label: 'Person', color: '#f85149', priority: 'high', severity: 'high' },
  VEHICLE: { label: 'Vehicle', color: '#db6d28', priority: 'high', severity: 'high' },
  WEAPON: { label: 'Weapon', color: '#f85149', priority: 'critical', severity: 'critical' },
  THERMAL: { label: 'Thermal Signature', color: '#d29922', priority: 'high', severity: 'high' },
  OBJECT: { label: 'Object of Interest', color: '#388bfd', priority: 'medium', severity: 'medium' },
  UNKNOWN: { label: 'Unidentified', color: '#a371f7', priority: 'medium', severity: 'medium' },
};

// Real-only: no synthetic detections. Detections arrive live from a module's
// camera/perception when one is present (probability 0 disables the generator).
export const detectionGeneration = {
  probability: 0,
  types: [],
};
