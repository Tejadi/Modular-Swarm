/**
 * CERES OS - Metrics Panel
 * Operational efficiency dashboard showing coverage, battery, distance,
 * task completion, and energy efficiency metrics.
 * Gotham design system
 */

import React from 'react';
import useFleetStore from '../store/fleetStore';

const MetricsPanel = () => {
  const { metrics, drones, tasks } = useFleetStore();
  const droneList = Object.values(drones);
  const scouts = droneList.filter((d) => d.role === 'SCOUT');

  const coveragePercent = metrics?.coveragePercent || 0;
  const averageBattery = metrics?.averageBattery || 0;
  const taskCompletionRate = metrics?.taskCompletionRate || 0;
  const distanceMap = metrics?.totalDistanceTraveled || {};
  const coverageHistory = metrics?.coverageHistory || [];

  const totalTasks = tasks.length;
  const completedTasks = tasks.filter((t) => t.status === 'COMPLETED').length;

  return (
    <div className="h-full overflow-y-auto scrollbar-thin">
      {/* Coverage Progress */}
      <div className="px-3 py-2 border-b border-gotham-border-muted">
        <div className="flex justify-between items-center mb-1.5">
          <span className="gotham-label">Coverage</span>
          <span className={`font-mono text-data font-bold ${
            coveragePercent > 75 ? 'text-gotham-accent-green' :
            coveragePercent > 40 ? 'text-gotham-accent-yellow' : 'text-gotham-accent-teal'
          }`}>
            {coveragePercent}%
          </span>
        </div>
        <div className="w-full h-2 bg-gotham-bg-tertiary rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-500"
            style={{
              width: `${coveragePercent}%`,
              backgroundColor: coveragePercent > 75 ? '#3fb950' : coveragePercent > 40 ? '#d29922' : '#39d2c0',
            }}
          />
        </div>
        {/* Mini sparkline */}
        {coverageHistory.length > 2 && (
          <div className="mt-2 h-8 flex items-end gap-px">
            {coverageHistory.slice(-30).map((h, i) => (
              <div
                key={i}
                className="flex-1 rounded-t"
                style={{
                  height: `${Math.max(2, (h.percent / 100) * 32)}px`,
                  backgroundColor: h.percent > 75 ? '#3fb950' : h.percent > 40 ? '#d29922' : '#39d2c0',
                  opacity: 0.5 + (i / 60),
                }}
              />
            ))}
          </div>
        )}
      </div>

      {/* Average Battery */}
      <div className="px-3 py-2 border-b border-gotham-border-muted">
        <div className="flex justify-between items-center mb-1">
          <span className="gotham-label">Avg Battery</span>
          <span className={`font-mono text-data font-bold ${
            averageBattery > 60 ? 'text-gotham-accent-green' :
            averageBattery > 30 ? 'text-gotham-accent-yellow' : 'text-gotham-accent-red'
          }`}>
            {averageBattery}%
          </span>
        </div>
        <div className="w-full h-1.5 bg-gotham-bg-tertiary rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all"
            style={{
              width: `${averageBattery}%`,
              backgroundColor: averageBattery > 60 ? '#3fb950' : averageBattery > 30 ? '#d29922' : '#f85149',
            }}
          />
        </div>
      </div>

      {/* Task Completion */}
      <div className="px-3 py-2 border-b border-gotham-border-muted">
        <div className="flex justify-between items-center mb-1">
          <span className="gotham-label">Tasks</span>
          <span className="text-data text-gotham-text-secondary font-mono">
            {completedTasks}/{totalTasks}
          </span>
        </div>
        <div className="w-full h-1.5 bg-gotham-bg-tertiary rounded-full overflow-hidden">
          <div
            className="h-full rounded-full bg-gotham-accent-blue transition-all"
            style={{ width: `${taskCompletionRate}%` }}
          />
        </div>
      </div>

      {/* Per-Drone Distance */}
      <div className="px-3 py-1.5 bg-gotham-bg-tertiary/50 border-b border-gotham-border-muted">
        <span className="gotham-label">Distance Traveled</span>
      </div>
      {scouts.map((drone) => {
        const dist = distanceMap[drone.id] || 0;
        const formatted = dist > 1000 ? `${(dist / 1000).toFixed(2)} km` : `${dist.toFixed(0)} m`;
        return (
          <div key={drone.id} className="px-3 py-1.5 border-b border-gotham-border-muted flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-data-sm font-bold px-1 py-0.5 rounded bg-gotham-accent-teal/20 text-gotham-accent-teal">S</span>
              <span className="text-data-sm text-gotham-text-secondary font-mono">{drone.id}</span>
            </div>
            <span className="text-data-sm text-gotham-text-primary font-mono">{formatted}</span>
          </div>
        );
      })}

      {/* Summary Stats */}
      <div className="px-3 py-2 border-b border-gotham-border-muted">
        <div className="grid grid-cols-2 gap-2">
          <MiniStat
            label="Total Distance"
            value={(() => {
              const total = Object.values(distanceMap).reduce((s, d) => s + d, 0);
              return total > 1000 ? `${(total / 1000).toFixed(1)} km` : `${total.toFixed(0)} m`;
            })()}
          />
          <MiniStat
            label="Active Scouts"
            value={`${scouts.filter((s) => !['OFFLINE', 'IDLE', 'CHARGING'].includes(s.status)).length}/${scouts.length}`}
          />
        </div>
      </div>
    </div>
  );
};

const MiniStat = ({ label, value }) => (
  <div className="bg-gotham-bg-tertiary/50 rounded px-2 py-1.5">
    <div className="text-[9px] uppercase tracking-wider text-gotham-text-tertiary mb-0.5">{label}</div>
    <div className="text-data font-mono font-medium text-gotham-text-primary">{value}</div>
  </div>
);

export default MetricsPanel;
