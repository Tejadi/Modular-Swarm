import React, { useState, useEffect } from 'react';
import useFleetStore from '../store/fleetStore';
import { useInstance } from '../instances';

const HeaderBar = () => {
  const instance = useInstance();
  const {
    connectionStatus,
    missionPhase,
    missionStartTime,
    drones,
  } = useFleetStore();

  const [elapsed, setElapsed] = useState('00:00:00');
  const [currentTime, setCurrentTime] = useState('');

  useEffect(() => {
    const interval = setInterval(() => {
      setCurrentTime(new Date().toLocaleTimeString('en-US', { hour12: false }));

      if (missionStartTime) {
        const diff = Math.floor((Date.now() - missionStartTime) / 1000);
        const h = String(Math.floor(diff / 3600)).padStart(2, '0');
        const m = String(Math.floor((diff % 3600) / 60)).padStart(2, '0');
        const s = String(diff % 60).padStart(2, '0');
        setElapsed(`${h}:${m}:${s}`);
      }
    }, 1000);
    return () => clearInterval(interval);
  }, [missionStartTime]);

  const droneList = Object.values(drones);
  const onlineCount = droneList.filter(d => d.status !== 'OFFLINE').length;

  const phaseColors = {
    IDLE: 'text-gotham-text-tertiary',
    PLANNING: 'text-gotham-accent-blue',
    DEPLOYING: 'text-gotham-accent-yellow',
    SCANNING: 'text-gotham-accent-teal',
    EXECUTING: 'text-gotham-accent-orange',
    PAUSED: 'text-gotham-accent-yellow',
    EMERGENCY: 'text-gotham-accent-red',
    COMPLETE: 'text-gotham-accent-green',
  };

  return (
    <div className="h-header bg-gotham-bg-secondary border-b border-gotham-border flex items-center justify-between px-4 z-50 select-none">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <div className="flex-shrink-0" dangerouslySetInnerHTML={{ __html: instance.logo(22) }} />
          <span className="text-gotham-text-primary font-display font-semibold text-sm tracking-wider">{(instance.name || instance.id).toUpperCase()}</span>
        </div>
        <div className="w-px h-5 bg-gotham-border" />
        <span className="gotham-label tracking-widest" style={{ fontSize: '9px', letterSpacing: '0.15em' }}>OLYMPUS</span>
      </div>

      <div className="flex items-center gap-6">
        <div className="flex items-center gap-3">
          <span className="gotham-label">Mission</span>
          <span className="text-gotham-text-primary font-mono text-data">{instance.missionIdPrefix}-001</span>
        </div>
        <div className="w-px h-5 bg-gotham-border" />
        <div className="flex items-center gap-2">
          <span className="gotham-label">Phase</span>
          <span className={`font-mono text-data font-medium ${phaseColors[missionPhase] || 'text-gotham-text-secondary'}`}>
            {missionPhase || 'IDLE'}
          </span>
        </div>
        <div className="w-px h-5 bg-gotham-border" />
        <div className="flex items-center gap-2">
          <span className="gotham-label">T+</span>
          <span className="text-gotham-text-primary font-mono text-data tabular-nums">{elapsed}</span>
        </div>
        <div className="w-px h-5 bg-gotham-border" />
        <div className="flex items-center gap-2">
          <span className="gotham-label">Fleet</span>
          <span className="text-gotham-text-primary font-mono text-data">{onlineCount}/{droneList.length}</span>
        </div>
      </div>

      <div className="flex items-center gap-4">
        <StatusLED label="ZENOH" status={connectionStatus === 'CONNECTED' ? 'online' : 'error'} />
        <StatusLED label="MESH" status={onlineCount > 0 ? 'online' : 'warning'} />
        <div className="w-px h-5 bg-gotham-border" />
        <span className="font-mono text-data text-gotham-text-tertiary tabular-nums">{currentTime}</span>
      </div>
    </div>
  );
};

const StatusLED = ({ label, status, detail }) => {
  const ledClass = {
    online: 'status-led-online',
    warning: 'status-led-warning',
    error: 'status-led-error',
    offline: 'status-led-offline',
  }[status] || 'status-led-offline';

  return (
    <div className="flex items-center gap-1.5">
      <span className={`status-led ${ledClass}`} />
      <span className="text-gotham-text-tertiary text-data-sm">{label}</span>
      {detail && <span className="text-gotham-text-tertiary text-data-sm opacity-60">{detail}</span>}
    </div>
  );
};

export default HeaderBar;
