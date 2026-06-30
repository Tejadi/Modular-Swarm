import React from 'react';
import useFleetStore, { DroneStatus, DroneRole } from '../store/fleetStore';
import { useInstance } from '../instances';

const fmt = (v) => (v == null || Number.isNaN(Number(v)) ? '—' : Number(v).toFixed(1));
const EKF_BITS = [[1, 'GPS'], [2, 'IMU'], [4, 'VIO'], [8, 'PEER'], [16, 'CONV']];
const ekfFlagsLabel = (f) => {
  if (f == null) return '—';
  const on = EKF_BITS.filter(([b]) => f & b).map(([, n]) => n);
  return on.length ? on.join('+') : 'none';
};

const VehicleDetail = ({ droneId, onClose }) => {
  const instance = useInstance();
  const { drones, sendCommand, tasks, droneRoutes } = useFleetStore();
  const drone = drones[droneId];
  const droneRoute = droneRoutes?.[droneId];

  if (!drone) return null;

  const roleInfo = DroneRole[drone.role];
  const statusInfo = DroneStatus[drone.status];
  const activeTasks = tasks.filter(t => t.assignedTo === droneId && t.status !== 'COMPLETED');

  const getBatteryColor = (level) => {
    if (level > 60) return 'text-gotham-accent-green';
    if (level > 30) return 'text-gotham-accent-yellow';
    return 'text-gotham-accent-red';
  };

  const getBatteryBarColor = (level) => {
    if (level > 60) return 'bg-gotham-accent-green';
    if (level > 30) return 'bg-gotham-accent-yellow';
    return 'bg-gotham-accent-red';
  };

  const getSignalQuality = (rssi) => {
    if (rssi > -50) return { label: 'Excellent', color: 'text-gotham-accent-green', bars: 4 };
    if (rssi > -65) return { label: 'Good', color: 'text-gotham-accent-green', bars: 3 };
    if (rssi > -80) return { label: 'Fair', color: 'text-gotham-accent-yellow', bars: 2 };
    return { label: 'Poor', color: 'text-gotham-accent-red', bars: 1 };
  };

  const signal = getSignalQuality(drone.signalStrength);

  const timeSinceUpdate = () => {
    const diff = Date.now() - drone.lastUpdate;
    if (diff < 1000) return 'Now';
    if (diff < 60000) return `${Math.floor(diff / 1000)}s ago`;
    return `${Math.floor(diff / 60000)}m ago`;
  };

  return (
    <div className="flex flex-col h-full">
      <div className="px-3 py-2 border-b border-gotham-border flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className={`text-data-sm font-bold px-1.5 py-0.5 rounded ${
            drone.role === 'SCOUT'
              ? 'bg-gotham-accent-teal/20 text-gotham-accent-teal'
              : 'bg-gotham-accent-orange/20 text-gotham-accent-orange'
          }`}>
            {roleInfo?.icon}
          </span>
          <span className="text-gotham-text-primary font-mono text-data font-medium">{drone.name || drone.id}</span>
        </div>
        <button
          onClick={onClose}
          className="text-gotham-text-tertiary hover:text-gotham-text-secondary transition-colors p-0.5"
          title="Back to fleet"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto scrollbar-thin">
        <div className="px-3 py-2 border-b border-gotham-border-muted">
          <div className="flex items-center justify-between">
            <span className="gotham-label">Status</span>
            <div className="flex items-center gap-1.5">
              {statusInfo?.pulse && (
                <span className="relative flex h-2 w-2">
                  <span className={`animate-ping absolute inline-flex h-full w-full rounded-full ${statusInfo.bgColor} opacity-75`} />
                  <span className={`relative inline-flex rounded-full h-2 w-2 ${statusInfo.bgColor}`} />
                </span>
              )}
              <span className={`font-mono text-data font-medium ${statusInfo?.color || 'text-gotham-text-secondary'}`}>
                {statusInfo?.label || drone.status}
              </span>
            </div>
          </div>
        </div>

        <Section title="Pose">
          <DataRow label="Latitude" value={drone.position.lat?.toFixed(6)} mono />
          <DataRow label="Longitude" value={drone.position.lon?.toFixed(6)} mono />
          <DataRow label="Altitude" value={`${drone.position.alt}m AGL`} mono />
          <DataRow label="Heading" value={drone.heading != null ? `${drone.heading.toFixed(1)}°` : '—'} mono />
        </Section>

        <Section title="Sensors & EKF">
          <div className="px-3 py-1.5 flex items-center justify-between">
            <span className="text-data-sm text-gotham-text-tertiary">Onboard</span>
            <div className="flex items-center gap-1 flex-wrap justify-end">
              {(drone.swarm?.sensors?.length ? drone.swarm.sensors : ['none']).map((s) => (
                <span key={s} className="text-data-sm font-mono px-1.5 py-0.5 rounded bg-gotham-accent-teal/20 text-gotham-accent-teal uppercase">{s}</span>
              ))}
            </div>
          </div>
          <DataRow label="Fix source" value={String(drone.swarm?.position_source || drone.positionSource || '—').toUpperCase()} mono />
          <DataRow label="EKF" value={ekfFlagsLabel(drone.swarm?.ekf_flags)} mono />
          <DataRow label={'Pos / Hdg σ'} value={`${fmt(drone.swarm?.position_std_m)} m / ${fmt(drone.swarm?.heading_std_deg)}°`} mono />
          <DataRow label="Velocity" value={drone.swarm?.velocity ? `${fmt(drone.swarm.velocity.north)}, ${fmt(drone.swarm.velocity.east)} m/s` : '—'} mono />
        </Section>

        <Section title="Role & Permissions">
          <DataRow label="Class" value={String(drone.swarm?.node_class || (drone.isLeader ? 'leader' : 'member')).toUpperCase()} mono />
          <DataRow label="Provider" value={drone.swarm?.is_provider ? 'yes' : 'no'} />
          <DataRow label="Relay" value={drone.swarm?.is_relay ? 'yes' : 'no'} />
          <DataRow label="Consumer" value={drone.swarm?.is_consumer ? 'yes' : 'no'} />
          <DataRow label="Capabilities" value={drone.swarm?.capabilities?.length ? drone.swarm.capabilities.join(', ') : '—'} mono />
        </Section>

        <Section title="Power">
          <div className="px-3 py-1.5">
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-data-sm text-gotham-text-tertiary">Battery</span>
              <span className={`font-mono text-data font-medium ${getBatteryColor(drone.battery)}`}>
                {drone.battery}%
              </span>
            </div>
            <div className="h-1.5 bg-gotham-bg-primary rounded-full overflow-hidden">
              <div
                className={`h-full ${getBatteryBarColor(drone.battery)} transition-all rounded-full`}
                style={{ width: `${drone.battery}%` }}
              />
            </div>
          </div>
        </Section>

        <Section title="Communications">
          <div className="px-3 py-1.5">
            <div className="flex items-center justify-between mb-1">
              <span className="text-data-sm text-gotham-text-tertiary">Signal</span>
              <div className="flex items-center gap-2">
                <SignalBars bars={signal.bars} />
                <span className={`font-mono text-data ${signal.color}`}>{drone.signalStrength}dBm</span>
              </div>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-data-sm text-gotham-text-tertiary">Quality</span>
              <span className={`text-data ${signal.color}`}>{signal.label}</span>
            </div>
          </div>
          <DataRow label="Last Update" value={timeSinceUpdate()} />
        </Section>

        {drone.role === 'EXECUTOR' && drone.tankLevel !== undefined && (() => {
          const payload = instance.vehicleDefaults.payload;
          const capacity = payload.capacity;
          const unit = payload.unit;
          const barColor = payload.barColor || 'bg-gotham-accent-orange';
          const textColor = payload.textColor || 'text-gotham-accent-orange';
          return (
            <Section title="Payload">
              <div className="px-3 py-1.5">
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-data-sm text-gotham-text-tertiary">{payload.label}</span>
                  <span className={`font-mono text-data ${textColor}`}>{drone.tankLevel.toFixed(1)}{unit}</span>
                </div>
                <div className="h-1.5 bg-gotham-bg-primary rounded-full overflow-hidden">
                  <div
                    className={`h-full ${barColor} transition-all rounded-full`}
                    style={{ width: `${(drone.tankLevel / capacity) * 100}%` }}
                  />
                </div>
                <div className="flex justify-between mt-1">
                  <span className="text-data-sm text-gotham-text-tertiary">0{unit}</span>
                  <span className="text-data-sm text-gotham-text-tertiary">{capacity}{unit}</span>
                </div>
              </div>
            </Section>
          );
        })()}

        {activeTasks.length > 0 && (
          <Section title="Active Tasks">
            {activeTasks.map(task => (
              <div key={task.id} className="px-3 py-1.5 border-b border-gotham-border-muted last:border-0">
                <div className="flex items-center justify-between">
                  <span className="text-data text-gotham-text-secondary">{task.type}</span>
                  <span className="text-data-sm text-gotham-accent-yellow font-mono">{task.status}</span>
                </div>
                <div className="text-data-sm text-gotham-text-tertiary font-mono mt-0.5">
                  {task.targetPosition.lat.toFixed(4)}, {task.targetPosition.lon.toFixed(4)}
                </div>
              </div>
            ))}
          </Section>
        )}

        <Section title="Coverage Assignment">
          {droneRoute?.regions?.length > 0 ? (
            droneRoute.regions.map((region, i) => (
              <DataRow key={i} label={region.fieldName} value={`${Math.round(region.areaSqM)} m\u00B2`} mono />
            ))
          ) : (
            <DataRow label="Zone" value={drone.voronoiRegion || 'Unassigned'} mono />
          )}
          <DataRow label="Waypoints" value={droneRoute?.route?.length || drone.flightPath?.length || 0} mono />
        </Section>
      </div>

      <div className="px-3 py-2 border-t border-gotham-border flex gap-2">
        <button
          onClick={() => sendCommand(droneId, 'RTL')}
          className="gotham-btn-primary flex-1 text-center text-data-sm"
        >
          RTL
        </button>
        <button
          onClick={() => sendCommand(droneId, 'PAUSE')}
          className="gotham-btn-warning flex-1 text-center text-data-sm"
        >
          PAUSE
        </button>
        <button
          onClick={() => sendCommand(droneId, 'ABORT')}
          className="gotham-btn-danger flex-1 text-center text-data-sm"
        >
          ABORT
        </button>
      </div>
    </div>
  );
};

const Section = ({ title, children }) => (
  <div className="border-b border-gotham-border-muted">
    <div className="px-3 py-1.5 bg-gotham-bg-tertiary/50">
      <span className="gotham-label">{title}</span>
    </div>
    {children}
  </div>
);

const DataRow = ({ label, value, mono }) => (
  <div className="px-3 py-1 flex items-center justify-between">
    <span className="text-data-sm text-gotham-text-tertiary">{label}</span>
    <span className={`text-data text-gotham-text-secondary ${mono ? 'font-mono' : ''}`}>{value}</span>
  </div>
);

const SignalBars = ({ bars }) => (
  <div className="flex items-end gap-0.5 h-3">
    {[1, 2, 3, 4].map((i) => (
      <div
        key={i}
        className={`w-1 rounded-sm transition-all ${
          i <= bars ? 'bg-gotham-accent-green' : 'bg-gotham-bg-elevated'
        }`}
        style={{ height: `${i * 25}%` }}
      />
    ))}
  </div>
);

export default VehicleDetail;
