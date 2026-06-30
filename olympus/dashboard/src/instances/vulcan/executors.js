export const taskTypes = {
  PHOTOGRAPH: { label: 'Photograph', color: '#39d2c0' },
  THERMAL_SCAN: { label: 'Thermal Scan', color: '#a371f7' },
  MEASURE: { label: 'Measure', color: '#d29922' },
  SAMPLE: { label: 'Sample', color: '#388bfd' },
};

export const detectionToTask = {
  STRUCTURAL_CRACK: 'PHOTOGRAPH',
  CORROSION: 'PHOTOGRAPH',
  THERMAL_ANOMALY: 'THERMAL_SCAN',
  LEAK_DETECTED: 'SAMPLE',
  VEGETATION_ENCROACHMENT: 'PHOTOGRAPH',
  SURFACE_DEFORMATION: 'MEASURE',
};
