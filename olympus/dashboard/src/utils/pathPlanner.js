/**
 * CERES OS - Coverage Path Planner
 * Generates boustrophedon (snake pattern) coverage paths for arbitrary
 * polygon regions (not just rectangles). Supports subdivision-aware routes.
 * Inspired by Fields2Cover library principles.
 */

// ---------------------------------------------------------------------------
// Geo helpers
// ---------------------------------------------------------------------------

const metersToLatitude = (meters) => meters / 111320;

const metersToLongitude = (meters, latitude) =>
  meters / (111320 * Math.cos((latitude * Math.PI) / 180));

/**
 * Bounding box of a polygon.
 */
const getFieldBounds = (polygon) => ({
  minLat: Math.min(...polygon.map((p) => p.lat)),
  maxLat: Math.max(...polygon.map((p) => p.lat)),
  minLon: Math.min(...polygon.map((p) => p.lon)),
  maxLon: Math.max(...polygon.map((p) => p.lon)),
});

/**
 * Centroid of a polygon.
 */
const getPolygonCenter = (polygon) => ({
  lat: polygon.reduce((s, p) => s + p.lat, 0) / polygon.length,
  lon: polygon.reduce((s, p) => s + p.lon, 0) / polygon.length,
});

/**
 * Find the longitude span where a horizontal sweep line at `lat` intersects
 * the polygon edges. Returns {minLon, maxLon} or nulls if no intersection.
 */
function getPolygonIntersectionAtLatitude(polygon, lat) {
  const intersections = [];
  for (let i = 0; i < polygon.length; i++) {
    const a = polygon[i];
    const b = polygon[(i + 1) % polygon.length];

    // Check if this edge crosses the latitude line
    if ((a.lat <= lat && b.lat > lat) || (b.lat <= lat && a.lat > lat)) {
      const t = (lat - a.lat) / (b.lat - a.lat);
      intersections.push(a.lon + t * (b.lon - a.lon));
    }
  }

  if (intersections.length < 2) return { minLon: null, maxLon: null };

  return {
    minLon: Math.min(...intersections),
    maxLon: Math.max(...intersections),
  };
}

// ---------------------------------------------------------------------------
// Core path generation
// ---------------------------------------------------------------------------

/**
 * Generate boustrophedon (snake pattern) coverage path for an arbitrary
 * polygon region (convex or simple concave).
 *
 * For each latitude sweep line, computes the actual polygon edge intersections
 * so waypoints follow the polygon boundary, not just the bounding box.
 *
 * @param {Array}  fieldPolygon  - Boundary [{lat, lon}, ...]
 * @param {number} [swathWidth=10] - Width of each pass in meters
 * @param {number} [altitude=35] - Flight altitude in meters AGL
 * @param {Object} [startPos]    - Optional drone start position {lat, lon}.
 * @returns {Array} Waypoints [{lat, lon, alt}, ...]
 */
export const generateCoveragePath = (
  fieldPolygon,
  swathWidth = 10,
  altitude = 35,
  startPos = null,
) => {
  const bounds = getFieldBounds(fieldPolygon);
  const centerLat = (bounds.minLat + bounds.maxLat) / 2;
  const centerLon = (bounds.minLon + bounds.maxLon) / 2;

  // Determine optimal starting corner (nearest to drone)
  let startFromSouth = true;
  let startFromWest = true;

  if (startPos) {
    startFromSouth = startPos.lat <= centerLat;
    startFromWest = startPos.lon <= centerLon;
  }

  const waypoints = [];
  const latStep = metersToLatitude(swathWidth);

  let currentLat = startFromSouth ? bounds.minLat : bounds.maxLat;
  const latDirection = startFromSouth ? 1 : -1;
  let lonDirection = startFromWest ? 1 : -1;

  const inRange = startFromSouth
    ? () => currentLat <= bounds.maxLat
    : () => currentLat >= bounds.minLat;

  while (inRange()) {
    // Find where the sweep line intersects the polygon edges
    const { minLon, maxLon } = getPolygonIntersectionAtLatitude(
      fieldPolygon,
      currentLat,
    );

    if (minLon !== null && maxLon !== null) {
      const startLon = lonDirection > 0 ? minLon : maxLon;
      const endLon = lonDirection > 0 ? maxLon : minLon;

      waypoints.push({ lat: currentLat, lon: startLon, alt: altitude });
      waypoints.push({ lat: currentLat, lon: endLon, alt: altitude });
    }

    currentLat += latStep * latDirection;
    lonDirection *= -1;
  }

  return waypoints;
};

// ---------------------------------------------------------------------------
// Subdivision-aware route assignment
// ---------------------------------------------------------------------------

/**
 * Assign coverage routes to scouts based on subdivision regions.
 * Each scout's route covers all its assigned sub-regions, ordered by
 * proximity (nearest region first) to minimise transit distance.
 *
 * @param {Array}  scouts       - Scout drone objects with .id, .position
 * @param {Array}  fields       - All farm fields (unused but kept for API symmetry)
 * @param {Object} subdivisions - { [droneId]: [{ fieldId, fieldName, cropType, polygon, areaSqM }] }
 * @returns {Object} { [droneId]: { droneId, route, regions, currentWaypointIndex, routeComplete } }
 */
export const assignSubdivisionRoutes = (scouts, fields, subdivisions) => {
  const routes = {};

  for (const scout of scouts) {
    const regions = subdivisions[scout.id] || [];

    // Sort by distance from scout position (nearest first)
    const sorted = [...regions].sort((a, b) => {
      const cA = getPolygonCenter(a.polygon);
      const cB = getPolygonCenter(b.polygon);
      return calculateDistance(scout.position, cA) - calculateDistance(scout.position, cB);
    });

    const allWaypoints = [];
    const regionMeta = [];

    for (const region of sorted) {
      const wps = generateCoveragePath(
        region.polygon,
        10,  // 10 m swath width
        35,  // 35 m AGL
        scout.position,
      );

      regionMeta.push({
        fieldId: region.fieldId,
        fieldName: region.fieldName,
        cropType: region.cropType,
        startIndex: allWaypoints.length,
        endIndex: allWaypoints.length + wps.length - 1,
        areaSqM: region.areaSqM,
      });

      allWaypoints.push(...wps);
    }

    routes[scout.id] = {
      droneId: scout.id,
      route: allWaypoints,
      regions: regionMeta,
      currentWaypointIndex: 0,
      routeComplete: false,
    };
  }

  return routes;
};

// ---------------------------------------------------------------------------
// Legacy full-field route assignment
// ---------------------------------------------------------------------------

/**
 * @deprecated Use assignSubdivisionRoutes with generateFieldSubdivisions instead.
 */
export const assignScoutRoutes = (scouts, fields) => {
  return scouts.map((scout, index) => {
    const assignedField = fields[index % fields.length];
    return {
      droneId: scout.id,
      fieldId: assignedField.id,
      fieldName: assignedField.name,
      cropType: assignedField.cropType,
      route: generateCoveragePath(assignedField.polygon, 10, 35),
      currentWaypointIndex: 0,
      routeComplete: false,
    };
  });
};

// ---------------------------------------------------------------------------
// Distance helpers
// ---------------------------------------------------------------------------

/**
 * Haversine distance between two geographic coordinates.
 * @param {Object} pos1 - {lat, lon}
 * @param {Object} pos2 - {lat, lon}
 * @returns {number} Distance in meters
 */
export const calculateDistance = (pos1, pos2) => {
  const R = 6371000;
  const lat1 = (pos1.lat * Math.PI) / 180;
  const lat2 = (pos2.lat * Math.PI) / 180;
  const dLat = ((pos2.lat - pos1.lat) * Math.PI) / 180;
  const dLon = ((pos2.lon - pos1.lon) * Math.PI) / 180;

  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;

  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
};

/**
 * Total route length in meters.
 */
export const calculateRouteLength = (route) => {
  let total = 0;
  for (let i = 1; i < route.length; i++) {
    total += calculateDistance(route[i - 1], route[i]);
  }
  return total;
};
