/**
 * CERES OS - Telemetry Graph
 * Real-time telemetry visualization using Recharts
 * Gotham design system
 */

import React, { useState, useEffect } from 'react';
import {
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Area,
  AreaChart,
} from 'recharts';
import useFleetStore from '../store/fleetStore';

const TelemetryGraph = ({ droneId }) => {
  const { drones } = useFleetStore();
  const [telemetryHistory, setTelemetryHistory] = useState([]);
  const [viewType, setViewType] = useState('battery');

  const drone = droneId ? drones[droneId] : null;

  useEffect(() => {
    const interval = setInterval(() => {
      if (!drone) return;

      setTelemetryHistory((prev) => {
        const now = new Date();
        const newEntry = {
          time: now.toLocaleTimeString('en-US', {
            hour12: false,
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
          }),
          battery: drone.battery + (Math.random() - 0.5) * 2,
          altitude: drone.position.alt + (Math.random() - 0.5) * 5,
          signal: drone.signalStrength + (Math.random() - 0.5) * 10,
          timestamp: now.getTime(),
        };

        const updated = [...prev, newEntry];
        return updated.slice(-30);
      });
    }, 1000);

    return () => clearInterval(interval);
  }, [drone]);

  if (!drone) {
    return (
      <div className="w-full h-full flex items-center justify-center text-gotham-text-tertiary text-data">
        Select a vehicle to view telemetry
      </div>
    );
  }

  const configs = {
    battery: { key: 'battery', color: '#3fb950', label: 'Battery (%)', domain: [0, 100] },
    altitude: { key: 'altitude', color: '#39d2c0', label: 'Altitude (m)', domain: [0, 100] },
    signal: { key: 'signal', color: '#d29922', label: 'Signal (dBm)', domain: [-100, -30] },
  };

  const config = configs[viewType];

  return (
    <div className="w-full h-full flex flex-col">
      {/* Header */}
      <div className="flex justify-between items-center mb-2 px-1">
        <div className="flex items-center gap-2">
          <span className="text-data-sm font-medium text-gotham-text-tertiary">{drone.id}</span>
          <span className={`text-data-sm px-1.5 py-0.5 rounded ${
            drone.role === 'SCOUT'
              ? 'bg-gotham-accent-teal/20 text-gotham-accent-teal'
              : 'bg-gotham-accent-orange/20 text-gotham-accent-orange'
          }`}>
            {drone.role}
          </span>
        </div>

        <div className="flex gap-1">
          <ToggleBtn label="BAT" active={viewType === 'battery'} onClick={() => setViewType('battery')} />
          <ToggleBtn label="ALT" active={viewType === 'altitude'} onClick={() => setViewType('altitude')} />
          <ToggleBtn label="SIG" active={viewType === 'signal'} onClick={() => setViewType('signal')} />
        </div>
      </div>

      {/* Chart */}
      <div className="flex-1">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={telemetryHistory} margin={{ top: 5, right: 5, left: -20, bottom: 5 }}>
            <defs>
              <linearGradient id={`gradient-${config.key}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={config.color} stopOpacity={0.3} />
                <stop offset="95%" stopColor={config.color} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
            <XAxis
              dataKey="time"
              stroke="#30363d"
              tick={{ fill: '#484f58', fontSize: 9 }}
              interval="preserveStartEnd"
            />
            <YAxis
              stroke="#30363d"
              tick={{ fill: '#484f58', fontSize: 9 }}
              domain={config.domain}
              width={35}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: '#161b22',
                border: '1px solid #30363d',
                borderRadius: '4px',
                fontSize: '11px',
                color: '#8b949e',
              }}
              labelStyle={{ color: '#484f58' }}
            />
            <Area
              type="monotone"
              dataKey={config.key}
              stroke={config.color}
              strokeWidth={1.5}
              fill={`url(#gradient-${config.key})`}
              dot={false}
              activeDot={{ r: 3, fill: config.color }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Current Values */}
      <div className="flex justify-between text-data-sm text-gotham-text-tertiary mt-1 px-1">
        <span>{config.label}</span>
        <span className="font-mono" style={{ color: config.color }}>
          {telemetryHistory.length > 0
            ? telemetryHistory[telemetryHistory.length - 1][config.key]?.toFixed(1)
            : '--'
          }
        </span>
      </div>
    </div>
  );
};

const ToggleBtn = ({ label, active, onClick }) => (
  <button
    onClick={onClick}
    className={`px-2 py-0.5 text-data-sm font-medium rounded transition-all ${
      active
        ? 'bg-gotham-accent-blue/20 text-gotham-accent-blue border border-gotham-accent-blue/30'
        : 'text-gotham-text-tertiary hover:text-gotham-text-secondary border border-transparent'
    }`}
  >
    {label}
  </button>
);

export default TelemetryGraph;
