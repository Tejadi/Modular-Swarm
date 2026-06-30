const metersToLatitude = (meters) => meters / 111320;

const metersToLongitude = (meters, latitude) => meters / (111320 * Math.cos(latitude * Math.PI / 180));

const calculatePolygon = (centerLat, centerLon, widthMeters, lengthMeters) => {
  const halfWidth = metersToLongitude(widthMeters / 2, centerLat);
  const halfLength = metersToLatitude(lengthMeters / 2);
  return [
    { lat: centerLat - halfLength, lon: centerLon - halfWidth },
    { lat: centerLat - halfLength, lon: centerLon + halfWidth },
    { lat: centerLat + halfLength, lon: centerLon + halfWidth },
    { lat: centerLat + halfLength, lon: centerLon - halfWidth },
  ];
};

const generateBoundary = (centerLat, centerLon, sizeMeters) => {
  const halfSize = sizeMeters / 2;
  const latOffset = metersToLatitude(halfSize);
  const lonOffset = metersToLongitude(halfSize, centerLat);
  return [
    { lat: centerLat - latOffset, lon: centerLon - lonOffset },
    { lat: centerLat - latOffset, lon: centerLon + lonOffset },
    { lat: centerLat + latOffset, lon: centerLon + lonOffset },
    { lat: centerLat + latOffset, lon: centerLon - lonOffset },
  ];
};

const generateFarmLayout = (profile, centerLat, centerLon, sizeMeters) => {
  const zones = profile.environment.zones;
  const fieldOffsetLat = metersToLatitude(sizeMeters / 3);
  const fieldOffsetLon = metersToLongitude(sizeMeters / 3, centerLat);

  const offsets = [
    { lat: fieldOffsetLat, lon: 0 },
    { lat: -fieldOffsetLat, lon: 0 },
    { lat: 0, lon: fieldOffsetLon },
    { lat: 0, lon: -fieldOffsetLon },
  ];

  const fields = zones.map((zone, i) => {
    const offset = offsets[i % offsets.length];
    return {
      ...zone,
      cropType: zone.metadata?.cropType || zone.name,
      polygon: calculatePolygon(
        centerLat + offset.lat,
        centerLon + offset.lon,
        zone.width || 150,
        zone.height || 120,
      ),
    };
  });

  const buildings = (profile.environment.structures || []).map((s) => ({
    ...s,
    position: {
      lat: centerLat + metersToLatitude(s.offsetLat || 0),
      lon: centerLon + metersToLongitude(s.offsetLon || 0, centerLat),
    },
  }));

  const halfSize = sizeMeters / 2;
  const latOff = metersToLatitude(halfSize);
  const lonOff = metersToLongitude(halfSize, centerLat);
  const roads = profile.environment.hasRoads ? [
    {
      id: 'main-road', name: 'Main Access Road', width: 6, type: 'dirt', color: '#CD853F',
      path: [
        { lat: centerLat - latOff, lon: centerLon - lonOff },
        { lat: centerLat + latOff, lon: centerLon + lonOff },
      ],
    },
    {
      id: 'cross-road', name: 'Cross Road', width: 5, type: 'dirt', color: '#D2691E',
      path: [
        { lat: centerLat - latOff, lon: centerLon + lonOff },
        { lat: centerLat + latOff, lon: centerLon - lonOff },
      ],
    },
  ] : [];

  return {
    center: { lat: centerLat, lon: centerLon },
    size: sizeMeters,
    name: profile.environment.siteName,
    fields,
    buildings,
    infrastructure: { roads, irrigation: { type: 'center-pivot', coverage: 'fields', status: 'active' } },
    boundary: generateBoundary(centerLat, centerLon, sizeMeters),
  };
};

const generateGridLayout = (profile, centerLat, centerLon, sizeMeters) => {
  const zones = profile.environment.zones;
  const quarterSize = sizeMeters / 4;
  const latOff = metersToLatitude(quarterSize);
  const lonOff = metersToLongitude(quarterSize, centerLat);

  const gridPositions = [
    { lat: latOff, lon: -lonOff },
    { lat: latOff, lon: lonOff },
    { lat: -latOff, lon: -lonOff },
    { lat: -latOff, lon: lonOff },
  ];

  const fields = zones.map((zone, i) => {
    const pos = gridPositions[i % gridPositions.length];
    const zoneWidth = zone.width || sizeMeters / 2.2;
    const zoneHeight = zone.height || sizeMeters / 2.2;
    return {
      ...zone,
      cropType: zone.metadata?.label || zone.name,
      polygon: calculatePolygon(
        centerLat + pos.lat,
        centerLon + pos.lon,
        zoneWidth,
        zoneHeight,
      ),
    };
  });

  const buildings = (profile.environment.structures || []).map((s) => ({
    ...s,
    position: {
      lat: centerLat + metersToLatitude(s.offsetLat || 0),
      lon: centerLon + metersToLongitude(s.offsetLon || 0, centerLat),
    },
  }));

  return {
    center: { lat: centerLat, lon: centerLon },
    size: sizeMeters,
    name: profile.environment.siteName,
    fields,
    buildings,
    infrastructure: { roads: [] },
    boundary: generateBoundary(centerLat, centerLon, sizeMeters),
  };
};

const generateFacilityLayout = (profile, centerLat, centerLon, sizeMeters) => {
  const zones = profile.environment.zones;

  const count = zones.length;
  const radius = sizeMeters / 4;

  const fields = zones.map((zone, i) => {
    const angle = (2 * Math.PI * i) / count - Math.PI / 2;
    const latOff = metersToLatitude(radius * Math.sin(angle));
    const lonOff = metersToLongitude(radius * Math.cos(angle), centerLat);
    return {
      ...zone,
      cropType: zone.metadata?.label || zone.name,
      polygon: calculatePolygon(
        centerLat + latOff,
        centerLon + lonOff,
        zone.width || 80,
        zone.height || 80,
      ),
    };
  });

  const buildings = (profile.environment.structures || []).map((s) => ({
    ...s,
    position: {
      lat: centerLat + metersToLatitude(s.offsetLat || 0),
      lon: centerLon + metersToLongitude(s.offsetLon || 0, centerLat),
    },
  }));

  const roads = profile.environment.hasRoads ? [
    {
      id: 'service-road', name: 'Service Road', width: 5, type: 'paved', color: '#4a5568',
      path: [
        { lat: centerLat - metersToLatitude(sizeMeters / 3), lon: centerLon },
        { lat: centerLat + metersToLatitude(sizeMeters / 3), lon: centerLon },
      ],
    },
  ] : [];

  return {
    center: { lat: centerLat, lon: centerLon },
    size: sizeMeters,
    name: profile.environment.siteName,
    fields,
    buildings,
    infrastructure: { roads },
    boundary: generateBoundary(centerLat, centerLon, sizeMeters),
  };
};

const generateOpenLayout = (profile, centerLat, centerLon, sizeMeters) => {
  return {
    center: { lat: centerLat, lon: centerLon },
    size: sizeMeters,
    name: profile.environment.siteName,
    fields: [],
    buildings: [],
    infrastructure: { roads: [] },
    boundary: generateBoundary(centerLat, centerLon, sizeMeters),
  };
};

export const generateEnvironment = (profile, centerLat, centerLon, sizeMeters) => {
  const type = profile.operatingArea?.generatorType || 'farm';

  switch (type) {
    case 'farm':
      return generateFarmLayout(profile, centerLat, centerLon, sizeMeters);
    case 'grid':
      return generateGridLayout(profile, centerLat, centerLon, sizeMeters);
    case 'facility':
      return generateFacilityLayout(profile, centerLat, centerLon, sizeMeters);
    case 'none':
      return generateOpenLayout(profile, centerLat, centerLon, sizeMeters);
    default:
      return generateFarmLayout(profile, centerLat, centerLon, sizeMeters);
  }
};
