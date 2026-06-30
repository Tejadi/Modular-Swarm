import React, { useState } from 'react';
import useFleetStore from '../store/fleetStore';

const CommandBar = () => {
  const {
    sendGlobalCommand,
    missionActive,
    missionPhase,
    drones,
    detections,
    metrics,
    startMission,
    pauseMission,
  } = useFleetStore();

  const [confirmAbort, setConfirmAbort] = useState(false);

  const handleAbort = () => {
    if (confirmAbort) {
      sendGlobalCommand('ABORT_ALL');
      setConfirmAbort(false);
    } else {
      setConfirmAbort(true);
      setTimeout(() => setConfirmAbort(false), 3000);
    }
  };

  const handleSafeReturn = () => {
    sendGlobalCommand('RTL_ALL');
  };

  const droneList = Object.values(drones);
  const activeScouts = droneList.filter(d => d.role === 'SCOUT' && !['OFFLINE', 'IDLE', 'CHARGING'].includes(d.status)).length;
  const totalScouts = droneList.filter(d => d.role === 'SCOUT').length;
  const readyExecutors = droneList.filter(d => d.role === 'EXECUTOR' && d.status === 'IDLE').length;
  const totalExecutors = droneList.filter(d => d.role === 'EXECUTOR').length;
  const pendingDetections = detections.filter(d => d.status === 'PENDING').length;

  return (
    <div className="h-command-bar bg-gotham-bg-secondary border-t border-gotham-border flex items-center justify-between px-4">
      <div className="flex items-center gap-2">
        <button
          onClick={handleAbort}
          className={`gotham-btn-danger flex items-center gap-1.5 text-data-sm ${
            confirmAbort ? 'animate-pulse bg-gotham-accent-red/20' : ''
          }`}
        >
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
          {confirmAbort ? 'CONFIRM ABORT' : 'ABORT ALL'}
        </button>

        <button
          onClick={handleSafeReturn}
          className="gotham-btn-warning flex items-center gap-1.5 text-data-sm"
        >
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
          </svg>
          RTL ALL
        </button>
      </div>

      <div className="flex items-center gap-4">
        <StatusCounter
          label="SCOUTS"
          value={activeScouts}
          total={totalScouts}
          color="text-gotham-accent-teal"
        />
        <div className="w-px h-5 bg-gotham-border" />
        <StatusCounter
          label="EXECUTORS"
          value={readyExecutors}
          total={totalExecutors}
          color="text-gotham-accent-orange"
          sublabel="ready"
        />
        <div className="w-px h-5 bg-gotham-border" />
        <StatusCounter
          label="COVERAGE"
          value={`${metrics?.coveragePercent || 0}%`}
          color={
            (metrics?.coveragePercent || 0) > 75 ? 'text-gotham-accent-green' :
            (metrics?.coveragePercent || 0) > 40 ? 'text-gotham-accent-yellow' : 'text-gotham-accent-teal'
          }
        />
        <div className="w-px h-5 bg-gotham-border" />
        <StatusCounter
          label="PENDING"
          value={pendingDetections}
          color={pendingDetections > 0 ? 'text-gotham-accent-yellow' : 'text-gotham-text-tertiary'}
        />
        <div className="w-px h-5 bg-gotham-border" />
        <div className="flex items-center gap-1.5">
          <span className={`status-led ${missionActive ? 'status-led-online' : 'status-led-error'}`} />
          <span className={`text-data-sm font-medium ${missionActive ? 'text-gotham-accent-green' : 'text-gotham-accent-red'}`}>
            {missionActive ? 'MISSION ACTIVE' : 'MISSION HALTED'}
          </span>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <button
          onClick={pauseMission}
          className="gotham-btn flex items-center gap-1.5 text-data-sm"
        >
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            {missionPhase === 'PAUSED' ? (
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
            ) : (
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 9v6m4-6v6" />
            )}
          </svg>
          {missionPhase === 'PAUSED' ? 'RESUME' : 'PAUSE'}
        </button>

        <button
          onClick={startMission}
          className="gotham-btn-primary flex items-center gap-1.5 text-data-sm"
        >
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
          </svg>
          DEPLOY
        </button>
      </div>
    </div>
  );
};

const StatusCounter = ({ label, value, total, color, sublabel }) => (
  <div className="flex items-center gap-1.5">
    <span className="text-data-sm text-gotham-text-tertiary">{label}</span>
    <span className={`font-mono text-data font-medium ${color}`}>
      {value}{total !== undefined ? `/${total}` : ''}
    </span>
    {sublabel && <span className="text-data-sm text-gotham-text-tertiary">{sublabel}</span>}
  </div>
);

export default CommandBar;
