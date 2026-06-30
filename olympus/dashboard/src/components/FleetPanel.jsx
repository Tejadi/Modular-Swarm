import React from 'react';
import useFleetStore, { DroneStatus } from '../store/fleetStore';
import VehicleDetail from './VehicleDetail';

const FleetPanel = () => {
  const {
    drones,
    selectedDrone,
    setSelectedDrone,
    viewMode,
    setViewMode,
    sendCommand,
    baseStations,
  } = useFleetStore();

  if (selectedDrone) {
    return (
      <VehicleDetail
        droneId={selectedDrone}
        onClose={() => setSelectedDrone(null)}
      />
    );
  }

  const droneList = Object.values(drones);
  const commandStations = droneList.filter(d => d.isLeader);
  const members = droneList.filter(d => !d.isLeader);
  // Members alternate modes: SCOUT when they carry a camera (search), else
  // EXECUTOR. "Just members for now" — no fixed scout/executor roles.
  const memberMode = (d) => {
    const s = (d.swarm?.sensors || []).map(x => String(x).toLowerCase());
    return (s.includes('camera') || s.includes('vio')) ? 'SCOUT' : 'EXECUTOR';
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex border-b border-gotham-border">
        <TabButton
          label="ALL"
          active={viewMode === 'ALL'}
          onClick={() => setViewMode('ALL')}
          count={droneList.length}
        />
        <TabButton
          label="MEMBERS"
          active={viewMode === 'MEMBERS'}
          onClick={() => setViewMode('MEMBERS')}
          count={members.length}
          accentColor="text-gotham-accent-teal"
        />
      </div>

      <div className="flex-1 overflow-y-auto scrollbar-thin">
        {viewMode === 'ALL' && commandStations.length > 0 && (
          <div>
            <div className="px-3 py-1.5 bg-gotham-bg-tertiary/50 border-b border-gotham-border-muted">
              <span className="gotham-label">Command Station ({commandStations.length})</span>
            </div>
            {commandStations.map((drone) => (
              <DroneCard
                key={drone.id}
                drone={drone}
                mode="CMD"
                onSelect={() => setSelectedDrone(drone.id)}
                onCommand={(cmd) => sendCommand(drone.id, cmd)}
              />
            ))}
          </div>
        )}

        {(viewMode === 'ALL' || viewMode === 'MEMBERS') && members.length > 0 && (
          <div>
            {viewMode === 'ALL' && (
              <div className="px-3 py-1.5 bg-gotham-bg-tertiary/50 border-b border-gotham-border-muted">
                <span className="gotham-label">Members ({members.length})</span>
              </div>
            )}
            {members.map((drone) => (
              <DroneCard
                key={drone.id}
                drone={drone}
                mode={memberMode(drone)}
                onSelect={() => setSelectedDrone(drone.id)}
                onCommand={(cmd) => sendCommand(drone.id, cmd)}
              />
            ))}
          </div>
        )}

        {viewMode === 'ALL' && baseStations.length > 0 && (
          <div>
            <div className="px-3 py-1.5 bg-gotham-bg-tertiary/50 border-b border-gotham-border-muted">
              <span className="gotham-label">Base Stations ({baseStations.length})</span>
            </div>
            {baseStations.map((station) => (
              <div key={station.id} className="px-3 py-2 border-b border-gotham-border-muted">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-data-sm font-bold px-1.5 py-0.5 rounded bg-gotham-bg-elevated text-gotham-text-secondary">
                      B
                    </span>
                    <span className="text-data text-gotham-text-secondary font-mono">{station.id}</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className={`status-led ${station.status === 'ONLINE' ? 'status-led-online' : 'status-led-offline'}`} />
                    <span className="text-data-sm text-gotham-text-tertiary">{station.status}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="px-3 py-2 border-t border-gotham-border bg-gotham-bg-tertiary/30">
        <div className="flex justify-between text-data-sm text-gotham-text-tertiary">
          <span>Online: {droneList.filter(d => d.status !== 'OFFLINE').length}/{droneList.length}</span>
          <span>Active: {droneList.filter(d => !['IDLE', 'CHARGING', 'OFFLINE'].includes(d.status)).length}</span>
        </div>
      </div>
    </div>
  );
};

const TabButton = ({ label, active, onClick, count, accentColor }) => (
  <button
    onClick={onClick}
    className={`flex-1 px-2 py-2 text-data-sm font-medium tracking-wider uppercase transition-all border-b-2 ${
      active
        ? `${accentColor || 'text-gotham-text-primary'} border-gotham-accent-blue bg-gotham-bg-tertiary/30`
        : 'text-gotham-text-tertiary border-transparent hover:text-gotham-text-secondary hover:bg-gotham-bg-tertiary/20'
    }`}
  >
    {label}
    <span className="ml-1 opacity-60">({count})</span>
  </button>
);

const DroneCard = ({ drone, mode, onSelect, onCommand }) => {
  const statusInfo = DroneStatus[drone.status];

  const getBatteryColor = (level) => {
    if (level > 60) return 'text-gotham-accent-green';
    if (level > 30) return 'text-gotham-accent-yellow';
    return 'text-gotham-accent-red';
  };

  const getBatteryBarWidth = (level) => `${level}%`;

  const getSignalBars = (rssi) => {
    if (rssi > -50) return 4;
    if (rssi > -65) return 3;
    if (rssi > -80) return 2;
    return 1;
  };

  return (
    <div
      onClick={onSelect}
      className="gotham-card mx-0 rounded-none border-x-0 border-t-0 cursor-pointer"
    >
      <div className="px-3 py-2">
        <div className="flex justify-between items-center mb-1.5">
          <div className="flex items-center gap-2">
            <span className={`text-data-sm font-bold px-1.5 py-0.5 rounded ${
              mode === 'CMD' ? 'bg-gotham-accent-blue/20 text-gotham-accent-blue'
              : mode === 'SCOUT' ? 'bg-gotham-accent-teal/20 text-gotham-accent-teal'
              : 'bg-gotham-accent-orange/20 text-gotham-accent-orange'
            }`}>
              {mode === 'CMD' ? 'CMD' : mode === 'SCOUT' ? 'SCT' : 'EXE'}
            </span>
            <span className="text-data text-gotham-text-primary font-mono font-medium">{drone.name || drone.id}</span>
          </div>

          <div className="flex items-center gap-1.5">
            {statusInfo?.pulse && (
              <span className="relative flex h-1.5 w-1.5">
                <span className={`animate-ping absolute inline-flex h-full w-full rounded-full ${statusInfo.bgColor} opacity-75`} />
                <span className={`relative inline-flex rounded-full h-1.5 w-1.5 ${statusInfo.bgColor}`} />
              </span>
            )}
            <span className={`text-data-sm font-medium ${statusInfo?.color || 'text-gotham-text-tertiary'}`}>
              {statusInfo?.label || drone.status}
            </span>
          </div>
        </div>

        <div className="flex items-center justify-between text-data-sm">
          <div className="flex items-center gap-1.5">
            <BatteryIcon level={drone.battery} />
            <span className={`font-mono ${getBatteryColor(drone.battery)}`}>{drone.battery}%</span>
          </div>

          <div className="flex items-center gap-1.5">
            <SignalIcon bars={getSignalBars(drone.signalStrength)} />
            <span className="text-gotham-text-tertiary font-mono">{drone.signalStrength}dBm</span>
          </div>

          <div className="font-mono text-gotham-text-secondary">
            <span className="text-gotham-text-tertiary">ALT </span>{drone.position.alt}m
          </div>
        </div>

        {drone.role === 'EXECUTOR' && drone.tankLevel !== undefined && (
          <div className="mt-1.5">
            <div className="flex justify-between text-data-sm mb-0.5">
              <span className="text-gotham-text-tertiary">Tank</span>
              <span className="text-gotham-accent-orange font-mono">{drone.tankLevel.toFixed(1)}L</span>
            </div>
            <div className="h-1 bg-gotham-bg-primary rounded-full overflow-hidden">
              <div
                className="h-full bg-gotham-accent-orange transition-all rounded-full"
                style={{ width: `${(drone.tankLevel / 10) * 100}%` }}
              />
            </div>
          </div>
        )}

        {drone.currentTask && (
          <div className="mt-1.5 text-data-sm text-gotham-accent-yellow bg-gotham-accent-yellow/10 px-2 py-1 rounded border border-gotham-accent-yellow/20">
            Task: {drone.currentTask}
          </div>
        )}
      </div>
    </div>
  );
};

const BatteryIcon = ({ level }) => {
  const getColor = () => {
    if (level > 60) return '#3fb950';
    if (level > 30) return '#d29922';
    return '#f85149';
  };

  return (
    <svg width="14" height="8" viewBox="0 0 16 10" fill="none">
      <rect x="0.5" y="1" width="12" height="8" rx="1" stroke={getColor()} strokeOpacity="0.6" />
      <rect x="13" y="3" width="2" height="4" rx="0.5" fill={getColor()} fillOpacity="0.6" />
      <rect x="2" y="2.5" width={level / 100 * 9} height="5" rx="0.5" fill={getColor()} />
    </svg>
  );
};

const SignalIcon = ({ bars }) => (
  <div className="flex items-end gap-px h-2.5">
    {[1, 2, 3, 4].map((i) => (
      <div
        key={i}
        className={`w-0.5 rounded-sm ${
          i <= bars ? 'bg-gotham-accent-green' : 'bg-gotham-bg-elevated'
        }`}
        style={{ height: `${i * 25}%` }}
      />
    ))}
  </div>
);

export default FleetPanel;
