import { getActiveInstance } from '../instances';

const weightedRandomChoice = (items) => {
  const totalWeight = items.reduce((sum, item) => sum + item.weight, 0);
  let random = Math.random() * totalWeight;

  for (const item of items) {
    random -= item.weight;
    if (random <= 0) return item;
  }
  return items[0];
};

export const attemptGenerateDetection = (drone, zoneInfo, instance) => {
  if (!instance) instance = getActiveInstance();

  const genConfig = instance.detectionGeneration;
  if (!genConfig) return null;

  const probability = genConfig.probability || 0.003;

  if (Math.random() > probability) {
    return null;
  }

  const selectedType = weightedRandomChoice(genConfig.types);

  const detectionPos = {
    lat: drone.position.lat + (Math.random() - 0.5) * 0.00003,
    lon: drone.position.lon + (Math.random() - 0.5) * 0.00003,
    alt: 0,
  };

  const detectionId = `det-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;

  const zoneName = zoneInfo.metadata?.label || zoneInfo.cropType || zoneInfo.name || 'zone';
  const description = selectedType.descriptionTemplate
    ? selectedType.descriptionTemplate.replace('{zone}', zoneName)
    : `${selectedType.type} detected in ${zoneName}`;

  return {
    id: detectionId,
    type: selectedType.type,
    severity: selectedType.severity,
    position: detectionPos,
    detectedBy: drone.id,
    detectedAt: new Date().toISOString(),
    timestamp: Date.now(),
    confidence: 0.70 + Math.random() * 0.25,
    fieldId: zoneInfo.id,
    fieldName: zoneInfo.name,
    cropType: zoneInfo.cropType,
    description,
    status: 'PENDING',
    assignedTo: null,
  };
};

export const getDetectionIcon = (type, instance) => {
  if (!instance) instance = getActiveInstance();

  const genConfig = instance.detectionGeneration;
  if (genConfig?.types) {
    const typeConfig = genConfig.types.find((t) => t.type === type);
    if (typeConfig?.icon) return typeConfig.icon;
  }
  return '\uD83D\uDCCD';
};

export const getDetectionColor = (severity) => {
  const colors = {
    low: '#10B981',
    medium: '#F59E0B',
    high: '#EF4444',
    critical: '#DC2626',
  };
  return colors[severity] || '#6B7280';
};
