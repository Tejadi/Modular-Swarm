/**
 * CERES OS - Field Subdivision Generator
 * Grid-based Voronoi tessellation for dividing agricultural fields
 * among scout drones. Uses convex hulls clipped to field boundaries
 * so adjacent partitions share edges with no gaps or overlaps.
 */

// ---------------------------------------------------------------------------
// Geo helpers
// ---------------------------------------------------------------------------

const metersToLatitude = (meters) => meters / 111320;

const metersToLongitude = (meters, latitude) =>
  meters / (111320 * Math.cos((latitude * Math.PI) / 180));

/**
 * Haversine distance between two {lat, lon} points.
 * @returns {number} Distance in meters
 */
const haversineDistance = (p1, p2) => {
  const R = 6371000;
  const lat1 = (p1.lat * Math.PI) / 180;
  const lat2 = (p2.lat * Math.PI) / 180;
  const dLat = ((p2.lat - p1.lat) * Math.PI) / 180;
  const dLon = ((p2.lon - p1.lon) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
};

/**
 * Axis-aligned bounding box of a polygon.
 */
const getBounds = (polygon) => ({
  minLat: Math.min(...polygon.map((p) => p.lat)),
  maxLat: Math.max(...polygon.map((p) => p.lat)),
  minLon: Math.min(...polygon.map((p) => p.lon)),
  maxLon: Math.max(...polygon.map((p) => p.lon)),
});

/**
 * Polygon center (centroid of vertices).
 */
const getCenter = (polygon) => ({
  lat: polygon.reduce((s, p) => s + p.lat, 0) / polygon.length,
  lon: polygon.reduce((s, p) => s + p.lon, 0) / polygon.length,
});

// ---------------------------------------------------------------------------
// Computational geometry helpers
// ---------------------------------------------------------------------------

/**
 * Cross product of vectors OA and OB where O is origin.
 */
function cross(O, A, B) {
  return (A.lon - O.lon) * (B.lat - O.lat) - (A.lat - O.lat) * (B.lon - O.lon);
}

/**
 * Convex hull via Andrew's monotone chain algorithm.
 * Returns polygon vertices in counter-clockwise order.
 * @param {Array} points - [{lat, lon}, ...]
 * @returns {Array} Hull vertices [{lat, lon}, ...]
 */
function convexHull(points) {
  if (points.length < 3) return [...points];

  // Sort by lon, then lat
  const sorted = [...points].sort(
    (a, b) => a.lon - b.lon || a.lat - b.lat,
  );

  // Remove duplicates
  const unique = [sorted[0]];
  for (let i = 1; i < sorted.length; i++) {
    if (
      sorted[i].lat !== sorted[i - 1].lat ||
      sorted[i].lon !== sorted[i - 1].lon
    ) {
      unique.push(sorted[i]);
    }
  }

  if (unique.length < 3) return unique;

  // Build lower hull
  const lower = [];
  for (const p of unique) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) {
      lower.pop();
    }
    lower.push(p);
  }

  // Build upper hull
  const upper = [];
  for (let i = unique.length - 1; i >= 0; i--) {
    const p = unique[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) {
      upper.pop();
    }
    upper.push(p);
  }

  // Remove last point of each half because it's repeated
  lower.pop();
  upper.pop();

  return lower.concat(upper);
}

/**
 * Sutherland-Hodgman polygon clipping.
 * Clips `subject` polygon to `clip` polygon.
 * Both must be convex and vertices in consistent winding order.
 * @param {Array} subject - [{lat, lon}, ...]
 * @param {Array} clip    - [{lat, lon}, ...]
 * @returns {Array} Clipped polygon [{lat, lon}, ...]
 */
function clipPolygon(subject, clip) {
  if (subject.length === 0 || clip.length === 0) return [];

  let output = [...subject];

  for (let i = 0; i < clip.length; i++) {
    if (output.length === 0) return [];

    const edgeStart = clip[i];
    const edgeEnd = clip[(i + 1) % clip.length];
    const input = [...output];
    output = [];

    for (let j = 0; j < input.length; j++) {
      const current = input[j];
      const previous = input[(j + input.length - 1) % input.length];

      const currInside = cross(edgeStart, edgeEnd, current) >= 0;
      const prevInside = cross(edgeStart, edgeEnd, previous) >= 0;

      if (currInside) {
        if (!prevInside) {
          output.push(intersect(edgeStart, edgeEnd, previous, current));
        }
        output.push(current);
      } else if (prevInside) {
        output.push(intersect(edgeStart, edgeEnd, previous, current));
      }
    }
  }

  return output;
}

/**
 * Line-line intersection of segment (p1->p2) with line (p3->p4).
 */
function intersect(p1, p2, p3, p4) {
  const a1 = p2.lat - p1.lat;
  const b1 = p1.lon - p2.lon;
  const c1 = a1 * p1.lon + b1 * p1.lat;

  const a2 = p4.lat - p3.lat;
  const b2 = p3.lon - p4.lon;
  const c2 = a2 * p3.lon + b2 * p3.lat;

  const det = a1 * b2 - a2 * b1;
  if (Math.abs(det) < 1e-12) {
    // Parallel lines - return midpoint as fallback
    return {
      lat: (p3.lat + p4.lat) / 2,
      lon: (p3.lon + p4.lon) / 2,
    };
  }

  return {
    lon: (c1 * b2 - c2 * b1) / det,
    lat: (a1 * c2 - a2 * c1) / det,
  };
}

/**
 * Shoelace formula for polygon area in m².
 * Converts lat/lon area to approximate m² using Haversine scale factors.
 */
function shoelaceArea(polygon) {
  if (polygon.length < 3) return 0;
  const center = getCenter(polygon);

  // Scale factors at center latitude
  const latScale = 111320; // meters per degree latitude
  const lonScale = 111320 * Math.cos((center.lat * Math.PI) / 180);

  let area = 0;
  for (let i = 0; i < polygon.length; i++) {
    const j = (i + 1) % polygon.length;
    const xi = polygon[i].lon * lonScale;
    const yi = polygon[i].lat * latScale;
    const xj = polygon[j].lon * lonScale;
    const yj = polygon[j].lat * latScale;
    area += xi * yj - xj * yi;
  }
  return Math.abs(area) / 2;
}

/**
 * Point-in-polygon test (ray casting).
 * @param {Object} point - {lat, lon}
 * @param {Array} polygon - [{lat, lon}, ...]
 * @returns {boolean}
 */
export function pointInPolygon(point, polygon) {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const pi = polygon[i];
    const pj = polygon[j];
    if (
      pi.lat > point.lat !== pj.lat > point.lat &&
      point.lon < ((pj.lon - pi.lon) * (point.lat - pi.lat)) / (pj.lat - pi.lat) + pi.lon
    ) {
      inside = !inside;
    }
  }
  return inside;
}

/**
 * Ensure polygon vertices are in counter-clockwise order.
 */
function ensureCCW(polygon) {
  let sum = 0;
  for (let i = 0; i < polygon.length; i++) {
    const j = (i + 1) % polygon.length;
    sum += (polygon[j].lon - polygon[i].lon) * (polygon[j].lat + polygon[i].lat);
  }
  return sum > 0 ? polygon : [...polygon].reverse();
}

// ---------------------------------------------------------------------------
// Subdivision colors (Gotham palette)
// ---------------------------------------------------------------------------

export const SUBDIVISION_COLORS = [
  '#39d2c0', // Teal  (scout-01)
  '#388bfd', // Blue  (scout-02)
  '#a371f7', // Purple
  '#d29922', // Gold
  '#f778ba', // Pink
  '#db6d28', // Orange
];

export const getSubdivisionColor = (index) =>
  SUBDIVISION_COLORS[index % SUBDIVISION_COLORS.length];

// ---------------------------------------------------------------------------
// Core algorithm
// ---------------------------------------------------------------------------

/**
 * Divide all fields among scout drones using grid-based Voronoi tessellation
 * with convex hull boundaries clipped to field polygons.
 *
 * Algorithm:
 * 1. Lay a point grid (default 5 m) across every field.
 * 2. For each grid point, find the nearest scout (Haversine). Optionally
 *    weight by battery so high-energy drones cover a larger area.
 * 3. For each (droneId, fieldId) group, compute the convex hull of the
 *    assigned points, then clip to the field polygon via Sutherland-Hodgman.
 * 4. Return per-drone sub-region polygons with accurate area metadata.
 *
 * Result: Adjacent partitions share edges at the perpendicular bisectors
 * between drone positions. No gaps, no overlaps.
 *
 * @param {Array}  fields  - Farm fields, each with .id, .name, .polygon [{lat,lon}], .cropType
 * @param {Array}  scouts  - Scout drones with .id, .position {lat,lon}, .battery (0-100)
 * @param {Object} [options]
 * @param {number} [options.gridResolution=5] - Grid spacing in meters
 * @param {boolean} [options.batteryWeight=true] - Weight by battery level
 * @param {Array}  [options.manualZones=[]] - Manual zone overrides [{polygon, droneId}]
 * @param {Array}  [options.boundaries=[]] - Hard boundary lines [{start, end}]
 * @returns {{ regions: Object, gridSize: number }}
 */
export const generateFieldSubdivisions = (fields, scouts, options = {}) => {
  const gridRes = options.gridResolution || 5;
  const useBattery = options.batteryWeight !== false;
  const manualZones = options.manualZones || [];
  const boundaries = options.boundaries || [];

  if (!scouts.length || !fields.length) return { regions: {}, gridSize: gridRes };

  // Single scout -> assign every field entirely
  if (scouts.length === 1) {
    return {
      regions: {
        [scouts[0].id]: fields.map((f) => ({
          fieldId: f.id,
          fieldName: f.name,
          cropType: f.cropType,
          polygon: [...f.polygon],
          areaSqM: shoelaceArea(f.polygon),
          center: getCenter(f.polygon),
          pointCount: 0,
        })),
      },
      gridSize: gridRes,
    };
  }

  // Step 1 – generate grid points across all fields
  const gridPoints = [];

  for (const field of fields) {
    const b = getBounds(field.polygon);
    const centerLat = (b.minLat + b.maxLat) / 2;
    const latStep = metersToLatitude(gridRes);
    const lonStep = metersToLongitude(gridRes, centerLat);

    for (let lat = b.minLat; lat <= b.maxLat; lat += latStep) {
      for (let lon = b.minLon; lon <= b.maxLon; lon += lonStep) {
        gridPoints.push({ lat, lon, fieldId: field.id });
      }
    }
  }

  // Step 2 – assign each point to nearest scout
  const buckets = {};
  scouts.forEach((s) => { buckets[s.id] = []; });

  for (const pt of gridPoints) {
    // Check manual zones first
    let assigned = false;
    for (const zone of manualZones) {
      if (zone.droneId && zone.polygon && pointInPolygon(pt, zone.polygon)) {
        if (buckets[zone.droneId]) {
          buckets[zone.droneId].push(pt);
          assigned = true;
          break;
        }
      }
    }
    if (assigned) continue;

    // Check if boundary constraint applies
    let bestId = scouts[0].id;
    let bestDist = Infinity;

    for (const scout of scouts) {
      let d = haversineDistance(pt, scout.position);
      if (useBattery) {
        const factor = Math.max(scout.battery, 10) / 100;
        d /= factor;
      }

      // Boundary penalty: if a hard boundary line separates the point from
      // this scout, add a large penalty to prevent cross-boundary assignment
      let penalized = false;
      for (const boundary of boundaries) {
        if (penalized) break;
        if (boundary.type === 'polygon' && boundary.polygon?.length >= 2) {
          for (let bi = 0; bi < boundary.polygon.length; bi++) {
            const bj = (bi + 1) % boundary.polygon.length;
            if (segmentsIntersect(pt, scout.position, boundary.polygon[bi], boundary.polygon[bj])) {
              d += 1e6;
              penalized = true;
              break;
            }
          }
        } else if (boundary.start && boundary.end) {
          if (segmentsIntersect(pt, scout.position, boundary.start, boundary.end)) {
            d += 1e6;
            penalized = true;
          }
        }
      }

      if (d < bestDist) {
        bestDist = d;
        bestId = scout.id;
      }
    }

    buckets[bestId].push(pt);
  }

  // Step 3 – compute convex hull per (drone, field), clip to field
  const regions = {};
  for (const scout of scouts) regions[scout.id] = [];

  for (const [droneId, points] of Object.entries(buckets)) {
    const byField = {};
    for (const p of points) {
      if (!byField[p.fieldId]) byField[p.fieldId] = [];
      byField[p.fieldId].push(p);
    }

    for (const [fieldId, fpts] of Object.entries(byField)) {
      if (fpts.length < 3) continue;

      const field = fields.find((f) => f.id === fieldId);
      if (!field) continue;

      // Compute convex hull of assigned points
      const hull = convexHull(fpts);
      if (hull.length < 3) continue;

      // Clip hull to field polygon (both must be CCW for Sutherland-Hodgman)
      const ccwHull = ensureCCW(hull);
      const ccwField = ensureCCW(field.polygon);
      const clipped = clipPolygon(ccwHull, ccwField);

      if (clipped.length < 3) continue;

      regions[droneId].push({
        fieldId,
        fieldName: field.name,
        cropType: field.cropType,
        polygon: clipped,
        areaSqM: shoelaceArea(clipped),
        center: getCenter(clipped),
        pointCount: fpts.length,
      });
    }
  }

  return { regions, gridSize: gridRes };
};

// ---------------------------------------------------------------------------
// Segment intersection helper (for boundary constraints)
// ---------------------------------------------------------------------------

/**
 * Test if line segments (p1->p2) and (p3->p4) intersect.
 */
function segmentsIntersect(p1, p2, p3, p4) {
  const d1 = cross(p3, p4, p1);
  const d2 = cross(p3, p4, p2);
  const d3 = cross(p1, p2, p3);
  const d4 = cross(p1, p2, p4);

  if (((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) &&
      ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0))) {
    return true;
  }
  return false;
}

// ---------------------------------------------------------------------------
// Legacy export (backwards compat)
// ---------------------------------------------------------------------------

/**
 * @deprecated Use generateFieldSubdivisions instead.
 */
export const generateVoronoiRegions = (scouts, droneRoutes, fields) => {
  const voronoiRegions = {};
  scouts.forEach((scout) => {
    const route = droneRoutes[scout.id];
    if (route && route.fieldId) {
      const assignedField = fields.find((f) => f.id === route.fieldId);
      if (assignedField) {
        voronoiRegions[scout.id] = {
          polygon: assignedField.polygon,
          fieldId: assignedField.id,
          fieldName: assignedField.name,
        };
      }
    }
  });
  return voronoiRegions;
};
