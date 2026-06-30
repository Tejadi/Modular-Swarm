import React, { useState, useRef, useCallback } from 'react';
import { Entity, PolygonGraphics, PolylineGraphics, LabelGraphics, BillboardGraphics } from 'resium';
import { Cartesian3, Cartesian2, Color, PolygonHierarchy, Cartographic, Math as CesiumMath, ScreenSpaceEventHandler, ScreenSpaceEventType } from 'cesium';
import useFleetStore, { DetectionType } from '../store/fleetStore';
import { useInstance } from '../instances';

const DrawingTools = ({ viewerRef }) => {
  const instance = useInstance();
  const [drawMode, setDrawMode] = useState(null);
  const [isDrawing, setIsDrawing] = useState(false);
  const [showToolbar, setShowToolbar] = useState(false);
  const [pendingZonePolygon, setPendingZonePolygon] = useState(null);
  const [pendingMarkerPos, setPendingMarkerPos] = useState(null);
  const handlerRef = useRef(null);
  const pointsRef = useRef([]);

  const {
    drawings,
    assignedZones,
    drawingBoundaries,
    markers,
    drones,
    addDrawing,
    clearDrawings,
    addAssignedZone,
    addBoundary,
    clearBoundaries,
    addMarker,
    clearMarkers,
    layers,
  } = useFleetStore();

  const scouts = Object.values(drones).filter((d) => d.role === 'SCOUT');

  const markerTypes = Object.entries(DetectionType).map(([key, val]) => ({
    key,
    label: val.label,
    color: val.color,
  }));

  const cartesianToLatLon = useCallback((cartesian) => {
    const carto = Cartographic.fromCartesian(cartesian);
    return {
      lat: CesiumMath.toDegrees(carto.latitude),
      lon: CesiumMath.toDegrees(carto.longitude),
    };
  }, []);

  const startDrawing = useCallback((mode) => {
    const viewer = viewerRef.current?.cesiumElement;
    if (!viewer) return;

    setDrawMode(mode);
    setIsDrawing(true);
    pointsRef.current = [];

    const handler = new ScreenSpaceEventHandler(viewer.scene.canvas);
    handlerRef.current = handler;

    if (mode === 'marker') {
      handler.setInputAction((click) => {
        const cartesian = viewer.camera.pickEllipsoid(
          click.position,
          viewer.scene.globe.ellipsoid
        );
        if (cartesian) {
          const pos = cartesianToLatLon(cartesian);
          setPendingMarkerPos(pos);
          cleanup();
        }
      }, ScreenSpaceEventType.LEFT_CLICK);

      handler.setInputAction(() => {
        cleanup();
      }, ScreenSpaceEventType.RIGHT_CLICK);
    } else {
      handler.setInputAction((click) => {
        const cartesian = viewer.camera.pickEllipsoid(
          click.position,
          viewer.scene.globe.ellipsoid
        );
        if (cartesian) {
          pointsRef.current.push(cartesian);
        }
      }, ScreenSpaceEventType.LEFT_CLICK);

      handler.setInputAction(() => {
        if (pointsRef.current.length >= 2) {
          finishDrawing(mode, pointsRef.current);
        }
        cleanup();
      }, ScreenSpaceEventType.RIGHT_CLICK);
    }
  }, [viewerRef]); // eslint-disable-line react-hooks/exhaustive-deps

  const finishDrawing = useCallback((mode, points) => {
    if (mode === 'boundary') {
      const polygon = points.map(cartesianToLatLon);
      addBoundary({
        polygon,
        cartesianPoints: [...points],
        type: points.length > 2 ? 'polygon' : 'line',
      });
    } else if (mode === 'assignZone') {
      const polygon = points.map(cartesianToLatLon);
      setPendingZonePolygon({ polygon, cartesianPoints: [...points] });
    } else {
      const drawing = {
        type: mode,
        points: [...points],
        label: mode === 'measure' ? calculateDistance(points) : '',
      };
      addDrawing(drawing);
    }
  }, [cartesianToLatLon, addBoundary, addDrawing]); // eslint-disable-line react-hooks/exhaustive-deps

  const cleanup = useCallback(() => {
    if (handlerRef.current) {
      handlerRef.current.destroy();
      handlerRef.current = null;
    }
    setIsDrawing(false);
    setDrawMode(null);
    pointsRef.current = [];
  }, []);

  const calculateDistance = useCallback((points) => {
    let total = 0;
    for (let i = 1; i < points.length; i++) {
      total += Cartesian3.distance(points[i - 1], points[i]);
    }
    return `${total.toFixed(1)} m`;
  }, []);

  const handleAssignZoneToDrone = useCallback((droneId) => {
    if (!pendingZonePolygon) return;
    addAssignedZone({
      polygon: pendingZonePolygon.polygon,
      cartesianPoints: pendingZonePolygon.cartesianPoints,
      droneId,
    });
    setPendingZonePolygon(null);
  }, [pendingZonePolygon, addAssignedZone]);

  const handlePlaceMarker = useCallback((typeKey) => {
    if (!pendingMarkerPos) return;
    const typeInfo = DetectionType[typeKey];
    addMarker({
      type: typeKey,
      label: typeInfo?.label || typeKey,
      color: typeInfo?.color || '#f85149',
      position: pendingMarkerPos,
      createdAt: Date.now(),
      source: 'operator',
    });
    setPendingMarkerPos(null);
  }, [pendingMarkerPos, addMarker]);

  const handleClearAll = useCallback(() => {
    clearDrawings();
    clearBoundaries();
    clearMarkers();
    cleanup();
  }, [clearDrawings, clearBoundaries, clearMarkers, cleanup]);

  const totalItems = drawings.length + assignedZones.length + drawingBoundaries.length + markers.length;

  return (
    <>
      {!showToolbar && (
        <div className="absolute top-4 right-4 z-40">
          <button
            onClick={() => setShowToolbar(true)}
            className="px-2 py-1.5 text-data-sm font-medium bg-gotham-bg-secondary/80 border border-gotham-border rounded text-gotham-text-tertiary hover:text-gotham-text-secondary hover:border-gotham-text-tertiary transition-all"
            title="Drawing Tools"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
            </svg>
          </button>
        </div>
      )}

      {showToolbar && (
        <div className="absolute top-4 right-4 z-40 bg-gotham-bg-secondary border border-gotham-border rounded w-48">
          <div className="flex items-center justify-between px-3 py-2 border-b border-gotham-border-muted">
            <span className="gotham-label">Draw Tools</span>
            <button
              onClick={() => setShowToolbar(false)}
              className="text-gotham-text-tertiary hover:text-gotham-text-secondary transition-colors"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          <div className="p-2 space-y-1">
            <div className="px-1 pb-1">
              <span className="text-[9px] uppercase tracking-wider text-gotham-text-tertiary">Annotate</span>
            </div>
            <ToolButton
              label="Place Marker"
              onClick={() => startDrawing('marker')}
              disabled={isDrawing}
              icon={<MarkerIcon />}
              accent="red"
            />
            <ToolButton
              label="Draw Area"
              onClick={() => startDrawing('polygon')}
              disabled={isDrawing}
              icon={<PolygonIcon />}
            />
            <ToolButton
              label="Draw Line"
              onClick={() => startDrawing('line')}
              disabled={isDrawing}
              icon={<LineIcon />}
            />
            <ToolButton
              label="Measure"
              onClick={() => startDrawing('measure')}
              disabled={isDrawing}
              icon={<MeasureIcon />}
            />

            <div className="border-t border-gotham-border-muted my-1" />
            <div className="px-1 pb-1">
              <span className="text-[9px] uppercase tracking-wider text-gotham-text-tertiary">Mission</span>
            </div>
            <ToolButton
              label="Assign Zone"
              onClick={() => startDrawing('assignZone')}
              disabled={isDrawing}
              icon={<ZoneIcon />}
              accent="teal"
            />
            <ToolButton
              label="Define Boundary"
              onClick={() => startDrawing('boundary')}
              disabled={isDrawing}
              icon={<BoundaryIcon />}
              accent="yellow"
            />

            {totalItems > 0 && (
              <>
                <div className="border-t border-gotham-border-muted my-1" />
                <ToolButton
                  label="Clear All"
                  onClick={handleClearAll}
                  variant="danger"
                  icon={<TrashIcon />}
                />
              </>
            )}
          </div>

          {isDrawing && (
            <div className="px-3 py-2 border-t border-gotham-border-muted bg-gotham-accent-blue/10">
              <p className="text-data-sm text-gotham-accent-blue font-medium mb-0.5">
                {drawMode === 'marker' ? 'Placing Marker' :
                 drawMode === 'assignZone' ? 'Drawing Zone' :
                 drawMode === 'boundary' ? 'Drawing Boundary' : 'Drawing Active'}
              </p>
              {drawMode === 'marker' ? (
                <p className="text-data-sm text-gotham-text-tertiary">Left-click: Place marker</p>
              ) : (
                <>
                  <p className="text-data-sm text-gotham-text-tertiary">Left-click: Add point</p>
                  <p className="text-data-sm text-gotham-text-tertiary">Right-click: Finish</p>
                </>
              )}
            </div>
          )}

          {pendingMarkerPos && (
            <div className="px-3 py-2 border-t border-gotham-border-muted bg-gotham-accent-red/10">
              <p className="text-data-sm text-gotham-accent-red font-medium mb-1.5">
                {instance.ui?.detectionLabel || 'Marker'} Type:
              </p>
              <div className="space-y-0.5 max-h-40 overflow-y-auto">
                {markerTypes.map((mt) => (
                  <button
                    key={mt.key}
                    onClick={() => handlePlaceMarker(mt.key)}
                    className="w-full flex items-center gap-2 px-2 py-1 rounded text-data-sm text-gotham-text-secondary hover:bg-gotham-bg-tertiary transition-all"
                  >
                    <span
                      className="w-2.5 h-2.5 rounded-sm flex-shrink-0"
                      style={{ backgroundColor: mt.color }}
                    />
                    {mt.label}
                  </button>
                ))}
              </div>
              <button
                onClick={() => setPendingMarkerPos(null)}
                className="w-full text-data-sm text-gotham-accent-red hover:bg-gotham-accent-red/10 rounded px-2 py-1 mt-1 transition-all"
              >
                Cancel
              </button>
            </div>
          )}

          {pendingZonePolygon && (
            <div className="px-3 py-2 border-t border-gotham-border-muted bg-gotham-accent-teal/10">
              <p className="text-data-sm text-gotham-accent-teal font-medium mb-1.5">Assign to Scout:</p>
              <div className="space-y-1">
                {scouts.map((scout) => (
                  <button
                    key={scout.id}
                    onClick={() => handleAssignZoneToDrone(scout.id)}
                    className="w-full flex items-center gap-2 px-2 py-1 rounded text-data-sm text-gotham-text-secondary hover:bg-gotham-bg-tertiary transition-all"
                  >
                    <span className="w-2 h-2 rounded-full bg-gotham-accent-teal" />
                    {scout.id}
                    <span className="ml-auto text-gotham-text-tertiary">{scout.battery}%</span>
                  </button>
                ))}
                <button
                  onClick={() => setPendingZonePolygon(null)}
                  className="w-full text-data-sm text-gotham-accent-red hover:bg-gotham-accent-red/10 rounded px-2 py-1 transition-all"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {totalItems > 0 && !isDrawing && !pendingZonePolygon && !pendingMarkerPos && (
            <div className="px-3 py-2 border-t border-gotham-border-muted">
              <div className="flex flex-wrap gap-2 text-data-sm text-gotham-text-tertiary">
                {drawings.length > 0 && <span>{drawings.length} drawing{drawings.length > 1 ? 's' : ''}</span>}
                {assignedZones.length > 0 && <span className="text-gotham-accent-teal">{assignedZones.length} zone{assignedZones.length > 1 ? 's' : ''}</span>}
                {drawingBoundaries.length > 0 && <span className="text-gotham-accent-yellow">{drawingBoundaries.length} boundary</span>}
                {markers.length > 0 && <span className="text-gotham-accent-red">{markers.length} marker{markers.length > 1 ? 's' : ''}</span>}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Render Annotation Drawings */}
      {drawings.map((drawing) => {
        if (drawing.type === 'polygon') {
          return (
            <Entity key={drawing.id} name={`Drawing: ${drawing.type}`}>
              <PolygonGraphics
                hierarchy={new PolygonHierarchy(drawing.points)}
                material={Color.fromCssColorString('#d29922').withAlpha(0.2)}
                outline={true}
                outlineColor={Color.fromCssColorString('#d29922')}
                outlineWidth={2}
              />
            </Entity>
          );
        } else if (drawing.type === 'line') {
          return (
            <Entity key={drawing.id} name={`Drawing: ${drawing.type}`}>
              <PolylineGraphics
                positions={drawing.points}
                width={3}
                material={Color.fromCssColorString('#388bfd')}
              />
            </Entity>
          );
        } else if (drawing.type === 'measure') {
          const midpoint = drawing.points[Math.floor(drawing.points.length / 2)];
          return (
            <React.Fragment key={drawing.id}>
              <Entity name={`Measurement: ${drawing.label}`}>
                <PolylineGraphics
                  positions={drawing.points}
                  width={3}
                  material={Color.fromCssColorString('#f85149')}
                />
              </Entity>
              <Entity position={midpoint}>
                <LabelGraphics
                  text={drawing.label}
                  font="12px Inter, sans-serif"
                  fillColor={Color.WHITE}
                  backgroundColor={Color.fromCssColorString('#0d1117').withAlpha(0.85)}
                  pixelOffset={new Cartesian2(0, -20)}
                  showBackground={true}
                  backgroundPadding={new Cartesian2(6, 4)}
                />
              </Entity>
            </React.Fragment>
          );
        }
        return null;
      })}

      {/* Render Assigned Zones */}
      {assignedZones.map((zone) => {
        const drone = drones[zone.droneId];
        const label = drone ? zone.droneId : 'Unassigned';
        return (
          <React.Fragment key={zone.id}>
            <Entity name={`Assigned Zone: ${label}`}>
              <PolygonGraphics
                hierarchy={new PolygonHierarchy(
                  zone.polygon.map((p) => Cartesian3.fromDegrees(p.lon, p.lat, 3))
                )}
                material={Color.fromCssColorString('#39d2c0').withAlpha(0.15)}
                outline={true}
                outlineColor={Color.fromCssColorString('#39d2c0')}
                outlineWidth={2}
              />
            </Entity>
            <Entity
              position={Cartesian3.fromDegrees(
                zone.polygon.reduce((s, p) => s + p.lon, 0) / zone.polygon.length,
                zone.polygon.reduce((s, p) => s + p.lat, 0) / zone.polygon.length,
                4
              )}
            >
              <LabelGraphics
                text={`ZONE: ${label}`}
                font="11px Inter, sans-serif"
                fillColor={Color.fromCssColorString('#39d2c0')}
                outlineColor={Color.fromCssColorString('#0d1117')}
                outlineWidth={3}
                showBackground={true}
                backgroundColor={Color.fromCssColorString('#0d1117').withAlpha(0.8)}
                backgroundPadding={new Cartesian2(6, 4)}
                pixelOffset={new Cartesian2(0, 0)}
              />
            </Entity>
          </React.Fragment>
        );
      })}

      {/* Render Pending Zone (being assigned) */}
      {pendingZonePolygon && (
        <Entity name="Pending Zone Assignment">
          <PolygonGraphics
            hierarchy={new PolygonHierarchy(pendingZonePolygon.cartesianPoints)}
            material={Color.fromCssColorString('#39d2c0').withAlpha(0.3)}
            outline={true}
            outlineColor={Color.fromCssColorString('#39d2c0')}
            outlineWidth={3}
          />
        </Entity>
      )}

      {/* Render Hard Boundaries (polygon or line) */}
      {drawingBoundaries.map((boundary) => {
        if (boundary.type === 'polygon' && boundary.polygon?.length >= 3) {
          return (
            <React.Fragment key={boundary.id}>
              <Entity name={`Boundary ${boundary.id}`}>
                <PolygonGraphics
                  hierarchy={new PolygonHierarchy(
                    boundary.polygon.map((p) => Cartesian3.fromDegrees(p.lon, p.lat, 4))
                  )}
                  material={Color.fromCssColorString('#d29922').withAlpha(0.1)}
                  outline={true}
                  outlineColor={Color.fromCssColorString('#d29922').withAlpha(0.9)}
                  outlineWidth={3}
                />
              </Entity>
              <Entity
                position={Cartesian3.fromDegrees(
                  boundary.polygon.reduce((s, p) => s + p.lon, 0) / boundary.polygon.length,
                  boundary.polygon.reduce((s, p) => s + p.lat, 0) / boundary.polygon.length,
                  5
                )}
              >
                <LabelGraphics
                  text="BOUNDARY"
                  font="10px Inter, sans-serif"
                  fillColor={Color.fromCssColorString('#d29922')}
                  outlineColor={Color.fromCssColorString('#0d1117')}
                  outlineWidth={2}
                  showBackground={true}
                  backgroundColor={Color.fromCssColorString('#0d1117').withAlpha(0.8)}
                  backgroundPadding={new Cartesian2(5, 3)}
                  pixelOffset={new Cartesian2(0, 0)}
                  scale={0.85}
                />
              </Entity>
            </React.Fragment>
          );
        }
        if (boundary.start && boundary.end) {
          return (
            <Entity key={boundary.id} name={`Boundary ${boundary.id}`}>
              <PolylineGraphics
                positions={[
                  Cartesian3.fromDegrees(boundary.start.lon, boundary.start.lat, 4),
                  Cartesian3.fromDegrees(boundary.end.lon, boundary.end.lat, 4),
                ]}
                width={4}
                material={Color.fromCssColorString('#d29922').withAlpha(0.9)}
              />
            </Entity>
          );
        }
        return null;
      })}

      {/* Render Manual Markers */}
      {layers.markers?.visible !== false && markers.map((marker) => {
        const size = 24;
        const svg = `
          <svg width="${size}" height="${size}" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
            <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7z" fill="${marker.color}" fill-opacity="0.85" stroke="${marker.color}" stroke-width="1"/>
            <circle cx="12" cy="9" r="3" fill="white" fill-opacity="0.9"/>
          </svg>
        `;
        const image = 'data:image/svg+xml;base64,' + btoa(svg);

        return (
          <Entity
            key={marker.id}
            name={`Marker: ${marker.label}`}
            position={Cartesian3.fromDegrees(marker.position.lon, marker.position.lat, 5)}
          >
            <BillboardGraphics
              image={image}
              width={size}
              height={size}
              verticalOrigin={1}
            />
            <LabelGraphics
              text={marker.label}
              font="9px Inter, sans-serif"
              fillColor={Color.fromCssColorString(marker.color)}
              outlineColor={Color.fromCssColorString('#0d1117')}
              outlineWidth={2}
              showBackground={true}
              backgroundColor={Color.fromCssColorString('#0d1117').withAlpha(0.8)}
              backgroundPadding={new Cartesian2(4, 2)}
              pixelOffset={new Cartesian2(0, -28)}
              scale={0.8}
            />
          </Entity>
        );
      })}
    </>
  );
};

const ToolButton = ({ label, onClick, disabled, icon, variant, accent }) => {
  let colorClass = 'text-gotham-text-secondary hover:bg-gotham-bg-tertiary';
  if (variant === 'danger') colorClass = 'text-gotham-accent-red hover:bg-gotham-accent-red/10';
  if (accent === 'teal') colorClass = 'text-gotham-accent-teal hover:bg-gotham-accent-teal/10';
  if (accent === 'yellow') colorClass = 'text-gotham-accent-yellow hover:bg-gotham-accent-yellow/10';
  if (accent === 'red') colorClass = 'text-gotham-accent-red hover:bg-gotham-accent-red/10';

  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`w-full flex items-center gap-2 px-2 py-1.5 rounded text-data-sm transition-all disabled:opacity-40 disabled:cursor-not-allowed ${colorClass}`}
    >
      {icon}
      {label}
    </button>
  );
};

const MarkerIcon = () => (
  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
  </svg>
);

const PolygonIcon = () => (
  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6l8-4 8 4v8l-8 4-8-4V6z" />
  </svg>
);

const LineIcon = () => (
  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 20L20 4" />
  </svg>
);

const MeasureIcon = () => (
  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 6h18M3 12h8m-8 6h18M9 6v12" />
  </svg>
);

const ZoneIcon = () => (
  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" />
  </svg>
);

const BoundaryIcon = () => (
  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v16h16" />
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} strokeDasharray="3 3" d="M4 12h16M12 4v16" />
  </svg>
);

const TrashIcon = () => (
  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
  </svg>
);

export default DrawingTools;
