import React, { useRef, useEffect, useState, useCallback } from 'react';
import { Viewer, Entity, PolylineGraphics, PolygonGraphics, BillboardGraphics, LabelGraphics, BoxGraphics, CorridorGraphics } from 'resium';
import { Cartesian3, Cartesian2, Color, PolygonHierarchy, LabelStyle, ArcGisMapServerImageryProvider, Cartographic, Math as CesiumMath, ScreenSpaceEventHandler, ScreenSpaceEventType } from 'cesium';
import useFleetStore, { DetectionType } from '../store/fleetStore';
import { useInstance } from '../instances';
import AlertStack from './AlertStack';
import DrawingTools from './DrawingTools';
import { assignSubdivisionRoutes, calculateDistance as haversineDistance } from '../utils/pathPlanner';
import { attemptGenerateDetection } from '../utils/detectionGenerator';
import { generateFieldSubdivisions, getSubdivisionColor } from '../utils/voronoiGenerator';

const COLORS = {
  scoutActive: Color.fromCssColorString('#39d2c0'),
  scoutIdle: Color.fromCssColorString('#39d2c0').withAlpha(0.6),
  executorActive: Color.fromCssColorString('#db6d28'),
  executorIdle: Color.fromCssColorString('#db6d28').withAlpha(0.6),
  emergency: Color.fromCssColorString('#f85149'),
  offline: Color.fromCssColorString('#484f58'),
};

const Overwatch = () => {
  const instance = useInstance();
  const viewerRef = useRef(null);
  const imageryLayerRef = useRef(null);
  const [subdivisionData, setSubdivisionData] = useState(null);
  const [mouseCoords, setMouseCoords] = useState(null);
  const [showGoto, setShowGoto] = useState(false);
  const [gotoInput, setGotoInput] = useState('');

  const {
    drones,
    detections,
    baseStations,
    fieldBoundary,
    syntheticFarm,
    droneRoutes,
    selectedDrone,
    selectedDetection,
    showVoronoi,
    showFlightPaths,
    showDetections,
    layers,
    assignedZones,
    drawingBoundaries,
    setSelectedDrone,
    setSelectedDetection,
    setDroneRoutes,
    updateWaypointIndex,
    updateDronePosition,
    addDetection,
    addDistanceTraveled,
    reassignZone,
  } = useFleetStore();

  const [reassignDropdown, setReassignDropdown] = useState(null);

  // Accept either {lat,lon} or {latitude,longitude}; return undefined (skip
  // rendering) when coords are missing so a half-formed frame can't crash Cesium.
  const toCartesian = useCallback((pos) => {
    if (!pos) return undefined;
    const lon = pos.lon ?? pos.longitude;
    const lat = pos.lat ?? pos.latitude;
    if (typeof lon !== 'number' || typeof lat !== 'number') return undefined;
    return Cartesian3.fromDegrees(lon, lat, pos.alt ?? pos.altitude ?? 0);
  }, []);

  // A point distM metres from pos along a compass heading (CW from North) — for
  // the heading/orientation arrow.
  const headingPoint = useCallback((pos, headingDeg, distM = 25) => {
    const lat = pos?.lat ?? pos?.latitude;
    const lon = pos?.lon ?? pos?.longitude;
    if (typeof lat !== 'number' || typeof lon !== 'number') return undefined;
    const h = ((headingDeg || 0) * Math.PI) / 180;
    const dN = Math.cos(h) * distM;
    const dE = Math.sin(h) * distM;
    return {
      lat: lat + dN / 111320,
      lon: lon + dE / (111320 * Math.cos((lat * Math.PI) / 180)),
      alt: pos.alt ?? pos.altitude ?? 0,
    };
  }, []);

  // Compact onboard-sensor tag, e.g. "GPS·IMU" — shown on every module's label.
  const sensorTag = useCallback((drone) => {
    const s = drone?.swarm?.sensors;
    return s && s.length ? `\n[${s.join('·').toUpperCase()}]` : '\n[no sensors]';
  }, []);

  const getDroneColor = useCallback((drone) => {
    if (drone.status === 'EMERGENCY') return COLORS.emergency;
    if (drone.status === 'OFFLINE') return COLORS.offline;

    const isActive = !['IDLE', 'CHARGING', 'OFFLINE'].includes(drone.status);
    if (drone.role === 'SCOUT') {
      return isActive ? COLORS.scoutActive : COLORS.scoutIdle;
    }
    return isActive ? COLORS.executorActive : COLORS.executorIdle;
  }, []);

  const createTacticalSymbol = useCallback((drone, isSelected) => {
    const color = getDroneColor(drone);
    const cssColor = `rgb(${Math.round(color.red * 255)}, ${Math.round(color.green * 255)}, ${Math.round(color.blue * 255)})`;
    const size = isSelected ? 40 : 32;
    const strokeWidth = isSelected ? 2.5 : 2;

    if (drone.role === 'SCOUT') {
      const svg = `
        <svg width="${size}" height="${size}" viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">
          <polygon points="20,4 36,20 20,36 4,20" fill="${cssColor}" fill-opacity="0.25" stroke="${cssColor}" stroke-width="${strokeWidth}"/>
          <circle cx="20" cy="20" r="5" fill="none" stroke="${cssColor}" stroke-width="1.5"/>
          <circle cx="20" cy="20" r="2" fill="${cssColor}"/>
          <line x1="20" y1="4" x2="20" y2="0" stroke="${cssColor}" stroke-width="2" stroke-linecap="round"/>
          ${isSelected ? `<polygon points="20,4 36,20 20,36 4,20" fill="none" stroke="#388bfd" stroke-width="1.5" stroke-dasharray="3 2"/>` : ''}
        </svg>
      `;
      return 'data:image/svg+xml;base64,' + btoa(svg);
    }

    const svg = `
      <svg width="${size}" height="${size}" viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">
        <polygon points="20,4 36,20 20,36 4,20" fill="${cssColor}" fill-opacity="0.25" stroke="${cssColor}" stroke-width="${strokeWidth}"/>
        <path d="M 22 12 L 17 21 L 21 21 L 18 28 L 24 19 L 20 19 Z" fill="${cssColor}"/>
        <line x1="20" y1="4" x2="20" y2="0" stroke="${cssColor}" stroke-width="2" stroke-linecap="round"/>
        ${isSelected ? `<polygon points="20,4 36,20 20,36 4,20" fill="none" stroke="#388bfd" stroke-width="1.5" stroke-dasharray="3 2"/>` : ''}
      </svg>
    `;
    return 'data:image/svg+xml;base64,' + btoa(svg);
  }, [getDroneColor]);

  const createDetectionSymbol = useCallback((detection) => {
    const typeInfo = DetectionType[detection.type];
    const cssColor = typeInfo?.color || '#f85149';
    const isPending = detection.status === 'PENDING';
    const size = isPending ? 28 : 22;

    const svg = `
      <svg width="${size}" height="${size}" viewBox="0 0 28 28" xmlns="http://www.w3.org/2000/svg">
        <polygon points="14,2 26,14 14,26 2,14" fill="${cssColor}" fill-opacity="0.3" stroke="${cssColor}" stroke-width="1.5"/>
        <line x1="9" y1="9" x2="19" y2="19" stroke="${cssColor}" stroke-width="1.5" stroke-linecap="round"/>
        <line x1="19" y1="9" x2="9" y2="19" stroke="${cssColor}" stroke-width="1.5" stroke-linecap="round"/>
        ${isPending ? `<circle cx="14" cy="14" r="13" fill="none" stroke="${cssColor}" stroke-width="1" stroke-dasharray="3 2" opacity="0.6"/>` : ''}
      </svg>
    `;
    return 'data:image/svg+xml;base64,' + btoa(svg);
  }, []);

  const createBaseStationSymbol = useCallback(() => {
    const svg = `
      <svg width="24" height="24" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <rect x="3" y="6" width="18" height="12" fill="#e6edf3" fill-opacity="0.2" stroke="#e6edf3" stroke-width="1.5" rx="1"/>
        <line x1="12" y1="2" x2="12" y2="6" stroke="#e6edf3" stroke-width="1.5"/>
        <circle cx="12" cy="2" r="1.5" fill="#3fb950"/>
        <line x1="8" y1="4" x2="12" y2="2" stroke="#e6edf3" stroke-width="1" opacity="0.5"/>
        <line x1="16" y1="4" x2="12" y2="2" stroke="#e6edf3" stroke-width="1" opacity="0.5"/>
      </svg>
    `;
    return 'data:image/svg+xml;base64,' + btoa(svg);
  }, []);

  useEffect(() => {
    const timer = setTimeout(async () => {
      if (viewerRef.current?.cesiumElement && fieldBoundary.length > 0) {
        try {
          const viewer = viewerRef.current.cesiumElement;

          try {
            const arcGisProvider = await ArcGisMapServerImageryProvider.fromUrl(
              'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer'
            );
            viewer.imageryLayers.removeAll();
            const imgLayer = viewer.imageryLayers.addImageryProvider(arcGisProvider);
            imageryLayerRef.current = imgLayer;
          } catch (imgError) {
            console.warn('[Overwatch] ArcGIS imagery failed, using default tiles:', imgError);
          }

          const centerLat = fieldBoundary.reduce((sum, p) => sum + p.lat, 0) / fieldBoundary.length;
          const centerLon = fieldBoundary.reduce((sum, p) => sum + p.lon, 0) / fieldBoundary.length;

          viewer.camera.flyTo({
            destination: Cartesian3.fromDegrees(centerLon, centerLat, 2000),
            orientation: {
              heading: 0,
              pitch: -Math.PI / 2,
              roll: 0,
            },
            duration: 2,
          });

          viewer.scene.globe.enableLighting = false;
          viewer.scene.globe.show = true;
          viewer.scene.globe.showGroundAtmosphere = false;
          viewer.scene.requestRenderMode = true;
          viewer.scene.fog.enabled = false;
          viewer.scene.skyAtmosphere.show = false;
          viewer.scene.sun.show = false;
          viewer.scene.moon.show = false;
          viewer.scene.skyBox.show = false;
          viewer.scene.backgroundColor = Color.fromCssColorString('#0d1117');
          viewer.scene.fxaa = false;
          viewer.scene.globe.maximumScreenSpaceError = 2;
        } catch (error) {
          console.error('[Overwatch] Error initializing Cesium viewer:', error);
        }
      }
    }, 500);

    return () => clearTimeout(timer);
  }, [fieldBoundary]);

  useEffect(() => {
    if (!viewerRef.current?.cesiumElement || !imageryLayerRef.current) return;
    const layer = imageryLayerRef.current;
    layer.show = layers.satellite?.visible !== false;
    layer.alpha = layers.satellite?.opacity ?? 1.0;
    viewerRef.current.cesiumElement.scene.requestRender();
  }, [layers.satellite?.visible, layers.satellite?.opacity]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let handler = null;
    const timer = setTimeout(() => {
      if (!viewerRef.current?.cesiumElement) return;
      const viewer = viewerRef.current.cesiumElement;
      handler = new ScreenSpaceEventHandler(viewer.scene.canvas);

      handler.setInputAction((movement) => {
        const ray = viewer.camera.getPickRay(movement.endPosition);
        if (ray) {
          const position = viewer.scene.globe.pick(ray, viewer.scene);
          if (position) {
            const carto = Cartographic.fromCartesian(position);
            setMouseCoords({
              lat: CesiumMath.toDegrees(carto.latitude),
              lon: CesiumMath.toDegrees(carto.longitude),
            });
          }
        }
      }, ScreenSpaceEventType.MOUSE_MOVE);
    }, 1000);

    return () => {
      clearTimeout(timer);
      if (handler) handler.destroy();
    };
  }, []);

  const handleGoto = useCallback(() => {
    const parts = gotoInput.split(',').map((s) => s.trim());
    if (parts.length === 2) {
      const lat = parseFloat(parts[0]);
      const lon = parseFloat(parts[1]);
      if (!isNaN(lat) && !isNaN(lon) && lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180 && viewerRef.current?.cesiumElement) {
        viewerRef.current.cesiumElement.camera.flyTo({
          destination: Cartesian3.fromDegrees(lon, lat, 600),
          orientation: { heading: 0, pitch: -Math.PI / 2, roll: 0 },
          duration: 1.5,
        });
        setShowGoto(false);
        setGotoInput('');
      }
    }
  }, [gotoInput]);

  const recomputeSubdivisions = useCallback(() => {
    if (!syntheticFarm || !syntheticFarm.fields) return;

    const scouts = Object.values(drones).filter((d) => d.role === 'SCOUT');
    if (scouts.length === 0) return;

    const subdiv = generateFieldSubdivisions(
      syntheticFarm.fields,
      scouts,
      {
        gridResolution: 5,
        batteryWeight: true,
        manualZones: assignedZones,
        boundaries: drawingBoundaries,
      },
    );
    setSubdivisionData(subdiv);

    const routesMap = assignSubdivisionRoutes(
      scouts,
      syntheticFarm.fields,
      subdiv.regions,
    );
    setDroneRoutes(routesMap);
  }, [syntheticFarm, drones, assignedZones, drawingBoundaries, setDroneRoutes]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    recomputeSubdivisions();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (assignedZones.length > 0 || drawingBoundaries.length > 0) {
      recomputeSubdivisions();
    }
  }, [assignedZones.length, drawingBoundaries.length]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const interval = setInterval(() => {
      if (!droneRoutes || Object.keys(droneRoutes).length === 0) return;

      Object.entries(droneRoutes).forEach(([droneId, routeData]) => {
        const drone = drones[droneId];
        if (!drone || drone.status === 'IDLE' || drone.status === 'OFFLINE') return;

        const { route, currentWaypointIndex } = routeData;

        if (currentWaypointIndex >= route.length) {
          updateWaypointIndex(droneId, 0);
          return;
        }

        const targetWaypoint = route[currentWaypointIndex];
        const currentPos = drone.position;

        const deltaLat = targetWaypoint.lat - currentPos.lat;
        const deltaLon = targetWaypoint.lon - currentPos.lon;
        const distance = Math.sqrt(deltaLat ** 2 + deltaLon ** 2);

        const WAYPOINT_THRESHOLD = 0.00001;

        if (distance < WAYPOINT_THRESHOLD) {
          updateWaypointIndex(droneId, currentWaypointIndex + 1);
        } else {
          const speed = 5;
          const step = (speed / 111320) / 10;

          const newPos = {
            lat: currentPos.lat + (deltaLat / distance) * step,
            lon: currentPos.lon + (deltaLon / distance) * step,
            alt: targetWaypoint.alt,
          };

          const dist = haversineDistance(currentPos, newPos);
          if (dist > 0 && dist < 100) {
            addDistanceTraveled(droneId, dist);
          }

          updateDronePosition(droneId, newPos);
        }

        if (drone.status === 'SCANNING' && syntheticFarm && syntheticFarm.fields) {
          const currentField = syntheticFarm.fields.find((f) => {
            const lats = f.polygon.map((p) => p.lat);
            const lons = f.polygon.map((p) => p.lon);
            return (
              drone.position.lat >= Math.min(...lats) && drone.position.lat <= Math.max(...lats) &&
              drone.position.lon >= Math.min(...lons) && drone.position.lon <= Math.max(...lons)
            );
          });

          // Real-only: detections come from the camera/YOLO pipeline over the
          // network (ceres/detection/*), never synthesized from drone motion.
          void currentField;
        }
      });
    }, 100);

    return () => clearInterval(interval);
  }, [droneRoutes, drones, syntheticFarm, updateWaypointIndex, updateDronePosition, addDetection, addDistanceTraveled]);

  const isLayerVisible = (key) => layers[key]?.visible !== false;
  const getLayerOpacity = (key) => layers[key]?.opacity ?? 1.0;

  return (
    <div className="w-full h-full bg-gotham-bg-primary overflow-hidden relative">
      <Viewer
        ref={viewerRef}
        full
        timeline={false}
        animation={false}
        baseLayerPicker={false}
        homeButton={false}
        navigationHelpButton={false}
        sceneModePicker={false}
        geocoder={false}
        fullscreenButton={false}
        vrButton={false}
        infoBox={false}
        selectionIndicator={false}
        shadows={false}
        shouldAnimate={false}
        requestRenderMode={true}
        maximumRenderTimeChange={Infinity}
      >
        {isLayerVisible('fieldBoundaries') && syntheticFarm && syntheticFarm.fields.map((field) => (
          <React.Fragment key={field.id}>
            <Entity name={field.name}>
              <PolygonGraphics
                hierarchy={new PolygonHierarchy(
                  field.polygon.map((p) => Cartesian3.fromDegrees(p.lon, p.lat, 0))
                )}
                material={Color.fromCssColorString(field.color).withAlpha(0.30 * getLayerOpacity('fieldBoundaries'))}
                outline={true}
                outlineColor={Color.fromCssColorString('#e6edf3').withAlpha(0.85 * getLayerOpacity('fieldBoundaries'))}
                outlineWidth={2}
              />
              <LabelGraphics
                text={`${field.name}\n${field.metadata?.label || field.cropType?.toUpperCase() || ''}`}
                font="13px Inter, sans-serif"
                fillColor={Color.fromCssColorString('#e6edf3').withAlpha(0.95 * getLayerOpacity('fieldBoundaries'))}
                outlineColor={Color.fromCssColorString('#0d1117')}
                outlineWidth={3}
                style={LabelStyle.FILL_AND_OUTLINE}
                pixelOffset={new Cartesian2(0, 0)}
                scale={1.0}
                showBackground={true}
                backgroundColor={Color.fromCssColorString('#161b22').withAlpha(0.75)}
                backgroundPadding={new Cartesian2(6, 4)}
              />
            </Entity>
            <Entity name={`${field.name} border`}>
              <PolylineGraphics
                positions={[...field.polygon, field.polygon[0]].map(
                  (p) => Cartesian3.fromDegrees(p.lon, p.lat, 1)
                )}
                width={3}
                material={Color.fromCssColorString('#e6edf3').withAlpha(0.8 * getLayerOpacity('fieldBoundaries'))}
                clampToGround={true}
              />
            </Entity>
          </React.Fragment>
        ))}

        {isLayerVisible('fieldBoundaries') && syntheticFarm && syntheticFarm.boundary && (
          <Entity name="AO Boundary">
            <PolygonGraphics
              hierarchy={new PolygonHierarchy(
                syntheticFarm.boundary.map((p) => Cartesian3.fromDegrees(p.lon, p.lat, 0))
              )}
              material={Color.TRANSPARENT}
              outline={true}
              outlineColor={Color.fromCssColorString('#388bfd').withAlpha(0.5 * getLayerOpacity('fieldBoundaries'))}
              outlineWidth={2}
            />
          </Entity>
        )}

        {isLayerVisible('fieldBoundaries') && syntheticFarm && syntheticFarm.buildings.map((building) => (
          <Entity
            key={building.id}
            name={building.name}
            position={Cartesian3.fromDegrees(
              building.position.lon,
              building.position.lat,
              building.dimensions.height / 2
            )}
          >
            <BoxGraphics
              dimensions={new Cartesian3(
                building.dimensions.width,
                building.dimensions.length,
                building.dimensions.height
              )}
              material={Color.fromCssColorString('#21262d').withAlpha(0.8)}
              outline={true}
              outlineColor={Color.fromCssColorString('#30363d')}
              outlineWidth={1}
            />
            <LabelGraphics
              text={building.name}
              font="10px Inter, sans-serif"
              fillColor={Color.fromCssColorString('#8b949e')}
              outlineColor={Color.fromCssColorString('#0d1117')}
              outlineWidth={2}
              style={LabelStyle.FILL_AND_OUTLINE}
              pixelOffset={new Cartesian2(0, building.dimensions.height / 2 + 15)}
              scale={0.7}
            />
          </Entity>
        ))}

        {isLayerVisible('fieldBoundaries') && syntheticFarm && syntheticFarm.infrastructure.roads.map((road) => (
          <Entity key={road.id} name={road.name}>
            <CorridorGraphics
              positions={road.path.map((p) => Cartesian3.fromDegrees(p.lon, p.lat, 0))}
              width={road.width}
              material={Color.fromCssColorString('#21262d').withAlpha(0.5)}
              outline={true}
              outlineColor={Color.fromCssColorString('#30363d').withAlpha(0.3)}
              outlineWidth={1}
            />
          </Entity>
        ))}

        {isLayerVisible('coverageZones') && showVoronoi && subdivisionData &&
          Object.entries(subdivisionData.regions).map(([droneId, regions], droneIndex) => (
            <React.Fragment key={`subdiv-${droneId}`}>
              {regions.map((region) => {
                const color = getSubdivisionColor(droneIndex);
                return (
                  <React.Fragment key={`subdiv-${droneId}-${region.fieldId}`}>
                    <Entity name={`${droneId} - ${region.fieldName}`}>
                      <PolygonGraphics
                        hierarchy={new PolygonHierarchy(
                          region.polygon.map((p) => Cartesian3.fromDegrees(p.lon, p.lat, 2))
                        )}
                        material={Color.fromCssColorString(color).withAlpha(0.20 * getLayerOpacity('coverageZones'))}
                        outline={false}
                      />
                      <LabelGraphics
                        text={`${droneId.split('-').pop()?.toUpperCase()} | ${Math.round(region.areaSqM / 1000)}k m\u00B2`}
                        font="10px Inter, sans-serif"
                        fillColor={Color.fromCssColorString(color).withAlpha(0.9 * getLayerOpacity('coverageZones'))}
                        outlineColor={Color.fromCssColorString('#0d1117')}
                        outlineWidth={2}
                        style={LabelStyle.FILL_AND_OUTLINE}
                        pixelOffset={new Cartesian2(0, 0)}
                        scale={0.8}
                      />
                    </Entity>
                    <Entity name={`${droneId} - ${region.fieldName} border`}>
                      <PolylineGraphics
                        positions={[...region.polygon, region.polygon[0]].map(
                          (p) => Cartesian3.fromDegrees(p.lon, p.lat, 2)
                        )}
                        width={2}
                        material={Color.fromCssColorString(color).withAlpha(0.6 * getLayerOpacity('coverageZones'))}
                        clampToGround={true}
                      />
                    </Entity>
                  </React.Fragment>
                );
              })}
            </React.Fragment>
          ))
        }

        {isLayerVisible('baseStations') && baseStations.map((station) => (
          <Entity
            key={station.id}
            name={station.id}
            position={toCartesian({ ...station.position, alt: 5 })}
          >
            <BillboardGraphics
              image={createBaseStationSymbol()}
              width={24}
              height={24}
            />
            <LabelGraphics
              text={station.id.toUpperCase()}
              font="9px Inter, sans-serif"
              fillColor={Color.fromCssColorString('#8b949e')}
              outlineColor={Color.fromCssColorString('#0d1117')}
              outlineWidth={2}
              style={LabelStyle.FILL_AND_OUTLINE}
              pixelOffset={new Cartesian2(0, -20)}
              scale={0.8}
            />
          </Entity>
        ))}

        {Object.values(drones).filter((drone) => toCartesian(drone.position)).map((drone) => (
          <React.Fragment key={drone.id}>
            <Entity
              name={drone.id}
              position={toCartesian(drone.position)}
              onClick={() => setSelectedDrone(drone.id)}
            >
              <BillboardGraphics
                image={createTacticalSymbol(drone, selectedDrone === drone.id)}
                width={selectedDrone === drone.id ? 40 : 32}
                height={selectedDrone === drone.id ? 40 : 32}
                rotation={CesiumMath.toRadians(-(drone.heading ?? drone.position?.heading ?? 0))}
                alignedAxis={Cartesian3.UNIT_Z}
              />
              <LabelGraphics
                text={`${drone.name || drone.id}${sensorTag(drone)}`}
                font="9px Inter, sans-serif"
                fillColor={Color.fromCssColorString(
                  drone.role === 'SCOUT' ? '#39d2c0' : '#db6d28'
                )}
                outlineColor={Color.fromCssColorString('#0d1117')}
                outlineWidth={3}
                style={LabelStyle.FILL_AND_OUTLINE}
                pixelOffset={new Cartesian2(0, -24)}
                scale={0.9}
              />
            </Entity>

            {/* Heading / orientation arrow — points the way the module is facing. */}
            {drone.position && drone.heading != null && (
              <Entity name={`${drone.id}-heading`}>
                <PolylineGraphics
                  positions={[
                    toCartesian(drone.position),
                    toCartesian(headingPoint(drone.position, drone.heading)),
                  ]}
                  width={2}
                  material={getDroneColor(drone)}
                />
              </Entity>
            )}

            {isLayerVisible('flightPaths') && showFlightPaths && drone.flightPath.length > 1 && (
              <Entity name={`${drone.id}-trail`}>
                <PolylineGraphics
                  positions={drone.flightPath.map((p) => toCartesian(p))}
                  width={1.5}
                  material={getDroneColor(drone).withAlpha(0.3 * getLayerOpacity('flightPaths'))}
                />
              </Entity>
            )}
          </React.Fragment>
        ))}

        {isLayerVisible('detections') && showDetections && detections.filter((d) => toCartesian(d.position)).map((detection) => (
          <Entity
            key={detection.id}
            name={detection.id}
            position={toCartesian(detection.position)}
            onClick={() => setSelectedDetection(detection.id)}
          >
            <BillboardGraphics
              image={createDetectionSymbol(detection)}
              width={detection.status === 'PENDING' ? 28 : 22}
              height={detection.status === 'PENDING' ? 28 : 22}
            />
            {detection.status === 'PENDING' && (
              <LabelGraphics
                text={DetectionType[detection.type]?.label || detection.type}
                font="9px Inter, sans-serif"
                fillColor={Color.fromCssColorString(DetectionType[detection.type]?.color || '#f85149')}
                outlineColor={Color.fromCssColorString('#0d1117')}
                outlineWidth={2}
                style={LabelStyle.FILL_AND_OUTLINE}
                pixelOffset={new Cartesian2(0, -20)}
                scale={0.7}
              />
            )}
          </Entity>
        ))}

        <DrawingTools viewerRef={viewerRef} />
      </Viewer>

      <AlertStack />

      {reassignDropdown && (
        <div
          className="absolute z-50 bg-gotham-bg-secondary border border-gotham-border rounded shadow-lg w-44"
          style={{ top: reassignDropdown.y, left: reassignDropdown.x }}
        >
          <div className="px-3 py-1.5 border-b border-gotham-border-muted">
            <span className="text-data-sm text-gotham-text-tertiary">Reassign to:</span>
          </div>
          {Object.values(drones).filter((d) => d.role === 'SCOUT').map((scout) => (
            <button
              key={scout.id}
              onClick={() => {
                if (reassignDropdown.zoneId) {
                  reassignZone(reassignDropdown.zoneId, scout.id);
                }
                setReassignDropdown(null);
                recomputeSubdivisions();
              }}
              className="w-full flex items-center gap-2 px-3 py-1.5 text-data-sm text-gotham-text-secondary hover:bg-gotham-bg-tertiary transition-all"
            >
              <span className="w-2 h-2 rounded-full bg-gotham-accent-teal" />
              {scout.id}
              <span className="ml-auto text-gotham-text-tertiary">{scout.battery}%</span>
            </button>
          ))}
          <button
            onClick={() => setReassignDropdown(null)}
            className="w-full px-3 py-1.5 text-data-sm text-gotham-text-tertiary hover:bg-gotham-bg-tertiary border-t border-gotham-border-muted"
          >
            Cancel
          </button>
        </div>
      )}

      <div className="absolute bottom-4 left-4 flex flex-col gap-1 z-40">
        <MapButton
          label="2D/3D"
          onClick={() => {
            if (viewerRef.current?.cesiumElement) {
              const viewer = viewerRef.current.cesiumElement;
              const is3D = viewer.scene.mode === 3;
              if (is3D) {
                viewer.scene.morphTo2D(1.0);
              } else {
                viewer.scene.morphTo3D(1.0);
              }
            }
          }}
        />
        <MapButton
          label="FIT"
          onClick={() => {
            if (viewerRef.current?.cesiumElement) {
              const viewer = viewerRef.current.cesiumElement;
              const centerLat = fieldBoundary.reduce((sum, p) => sum + p.lat, 0) / fieldBoundary.length;
              const centerLon = fieldBoundary.reduce((sum, p) => sum + p.lon, 0) / fieldBoundary.length;
              viewer.camera.flyTo({
                destination: Cartesian3.fromDegrees(centerLon, centerLat, 600),
                orientation: { heading: 0, pitch: -Math.PI / 2, roll: 0 },
                duration: 1,
              });
            }
          }}
        />
      </div>

      <div className="absolute bottom-4 right-4 z-40 flex flex-col items-end gap-1">
        {showGoto && (
          <div className="bg-gotham-bg-secondary/90 border border-gotham-border rounded px-2 py-1.5 flex gap-1.5 animate-slide-up">
            <input
              type="text"
              value={gotoInput}
              onChange={(e) => setGotoInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleGoto()}
              placeholder="lat, lon"
              className="bg-gotham-bg-primary border border-gotham-border-muted rounded px-2 py-0.5 text-data-sm text-gotham-text-primary font-mono w-40 focus:border-gotham-accent-blue focus:outline-none"
              autoFocus
            />
            <button
              onClick={handleGoto}
              className="px-2 py-0.5 text-data-sm bg-gotham-accent-blue/20 text-gotham-accent-blue rounded hover:bg-gotham-accent-blue/30 transition-colors font-medium"
            >
              FLY
            </button>
          </div>
        )}
        <div className="bg-gotham-bg-secondary/80 border border-gotham-border rounded px-2 py-1 flex items-center gap-2">
          <span className="text-data-sm text-gotham-text-tertiary font-mono">
            {mouseCoords
              ? `${Math.abs(mouseCoords.lat).toFixed(4)}\u00B0${mouseCoords.lat >= 0 ? 'N' : 'S'} ${Math.abs(mouseCoords.lon).toFixed(4)}\u00B0${mouseCoords.lon >= 0 ? 'E' : 'W'}`
              : '\u2014'}
          </span>
          <button
            onClick={() => setShowGoto(!showGoto)}
            className={`text-data-sm font-medium transition-colors ${showGoto ? 'text-gotham-accent-blue' : 'text-gotham-text-tertiary hover:text-gotham-accent-blue'}`}
          >
            GO TO
          </button>
        </div>
      </div>
    </div>
  );
};

const MapButton = ({ label, onClick }) => (
  <button
    onClick={onClick}
    className="px-2 py-1.5 text-data-sm font-medium bg-gotham-bg-secondary/80 border border-gotham-border rounded text-gotham-text-tertiary hover:text-gotham-text-secondary hover:border-gotham-text-tertiary transition-all"
  >
    {label}
  </button>
);

export default Overwatch;
