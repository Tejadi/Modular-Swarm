/**
 * CERES OS - Overlay Manager
 * ATAK-style layer toggle manager for map overlays
 */

import React from 'react';
import useFleetStore from '../store/fleetStore';

const OverlayManager = () => {
  const { layers, setLayerVisibility, setLayerOpacity } = useFleetStore();

  const layerConfig = [
    { key: 'satellite', label: 'Satellite Imagery', icon: SatelliteIcon },
    { key: 'fieldBoundaries', label: 'Zone Boundaries', icon: BoundaryIcon },
    { key: 'coverageZones', label: 'Coverage Zones', icon: CoverageIcon },
    { key: 'flightPaths', label: 'Flight Paths', icon: PathIcon },
    { key: 'detections', label: 'Detections', icon: DetectionIcon },
    { key: 'baseStations', label: 'Base Stations', icon: StationIcon },
    { key: 'markers', label: 'Manual Markers', icon: MarkerIcon },
  ];

  return (
    <div className="flex flex-col h-full">
      <div className="px-3 py-2">
        <span className="gotham-label">Map Layers</span>
      </div>
      <div className="flex-1 overflow-y-auto scrollbar-thin">
        {layerConfig.map(({ key, label, icon: Icon }) => {
          const layer = layers[key];
          if (!layer) return null;
          return (
            <LayerRow
              key={key}
              label={label}
              icon={<Icon />}
              visible={layer.visible}
              opacity={layer.opacity}
              onToggle={() => setLayerVisibility(key, !layer.visible)}
              onOpacityChange={(val) => setLayerOpacity(key, val)}
            />
          );
        })}
      </div>
    </div>
  );
};

const LayerRow = ({ label, icon, visible, opacity, onToggle, onOpacityChange }) => (
  <div className={`px-3 py-2 border-b border-gotham-border-muted transition-colors ${visible ? '' : 'opacity-50'}`}>
    <div className="flex items-center justify-between mb-1">
      <div className="flex items-center gap-2">
        <button
          onClick={onToggle}
          className={`toggle-switch ${visible ? 'active' : ''}`}
        />
        <div className="flex items-center gap-1.5">
          <span className="text-gotham-text-tertiary w-4 h-4">{icon}</span>
          <span className="text-data text-gotham-text-secondary">{label}</span>
        </div>
      </div>
    </div>
    {visible && (
      <div className="flex items-center gap-2 pl-10 mt-1">
        <span className="text-data-sm text-gotham-text-tertiary w-8">
          {Math.round(opacity * 100)}%
        </span>
        <input
          type="range"
          min="0"
          max="100"
          value={Math.round(opacity * 100)}
          onChange={(e) => onOpacityChange(parseInt(e.target.value) / 100)}
          className="flex-1 h-1"
        />
      </div>
    )}
  </div>
);

// Layer icons as inline SVGs
const SatelliteIcon = () => (
  <svg viewBox="0 0 16 16" fill="currentColor" className="w-4 h-4">
    <path d="M3.5 6.5L1 9l2 2 2.5-2.5M9.5 12.5L7 15l2-2 2.5-2.5M6.5 3.5l3-3 5 5-3 3M4 8l4 4M2 10l4 4" stroke="currentColor" strokeWidth="1" fill="none"/>
  </svg>
);

const BoundaryIcon = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-4 h-4">
    <rect x="2" y="2" width="12" height="12" strokeDasharray="3 2" />
  </svg>
);

const CoverageIcon = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-4 h-4">
    <path d="M8 1L1 8l7 7 7-7z" />
    <path d="M8 4v8M4 8h8" strokeWidth="1" opacity="0.5" />
  </svg>
);

const PathIcon = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-4 h-4">
    <path d="M2 14L6 6l4 4 4-10" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

const DetectionIcon = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-4 h-4">
    <circle cx="8" cy="8" r="3" />
    <circle cx="8" cy="8" r="6" strokeDasharray="2 2" />
  </svg>
);

const StationIcon = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-4 h-4">
    <path d="M8 2v8M4 14h8M6 10l2 4 2-4" />
    <path d="M3 5a5 5 0 0 1 10 0" strokeWidth="1" />
  </svg>
);

const MarkerIcon = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-4 h-4">
    <path d="M8 1C5.2 1 3 3.2 3 6c0 3.5 5 9 5 9s5-5.5 5-9c0-2.8-2.2-5-5-5z" />
    <circle cx="8" cy="6" r="2" />
  </svg>
);

export default OverlayManager;
