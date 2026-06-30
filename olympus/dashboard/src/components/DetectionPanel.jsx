import React, { useState } from 'react';
import useFleetStore, { DetectionType, DroneRole, DroneStatus } from '../store/fleetStore';
import { useInstance } from '../instances';
import OverlayManager from './OverlayManager';
import MetricsPanel from './MetricsPanel';
import AiChat from './AiChat';
import useMetrics from '../hooks/useMetrics';

const TABS = ['ENTITIES', 'DETECTIONS', 'METRICS', 'AI', 'LAYERS'];

const DetectionPanel = () => {
  const [activeTab, setActiveTab] = useState('DETECTIONS');

  useMetrics(2000);

  return (
    <div className="flex flex-col h-full">
      <div className="flex border-b border-gotham-border">
        {TABS.map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`flex-1 px-2 py-2 text-data-sm font-medium tracking-wider uppercase transition-all border-b-2 ${
              activeTab === tab
                ? 'text-gotham-text-primary border-gotham-accent-blue bg-gotham-bg-tertiary/30'
                : 'text-gotham-text-tertiary border-transparent hover:text-gotham-text-secondary hover:bg-gotham-bg-tertiary/20'
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-hidden">
        {activeTab === 'ENTITIES' && <EntitiesTab />}
        {activeTab === 'DETECTIONS' && <DetectionsTab />}
        {activeTab === 'METRICS' && <MetricsPanel />}
        {activeTab === 'AI' && <AiChat />}
        {activeTab === 'LAYERS' && <OverlayManager />}
      </div>
    </div>
  );
};

const EntitiesTab = () => {
  const { drones, setSelectedDrone } = useFleetStore();
  const droneList = Object.values(drones);
  const commandStations = droneList.filter(d => d.isLeader);
  const members = droneList.filter(d => !d.isLeader);
  // Members alternate scout (has camera) / executor (no camera).
  const memberMode = (d) => {
    const s = (d.swarm?.sensors || []).map(x => String(x).toLowerCase());
    return (s.includes('camera') || s.includes('vio')) ? 'SCOUT' : 'EXECUTOR';
  };

  return (
    <div className="h-full overflow-y-auto scrollbar-thin">
      <div className="px-3 py-1.5 bg-gotham-bg-tertiary/50 border-b border-gotham-border-muted">
        <span className="gotham-label">Command Station ({commandStations.length})</span>
      </div>
      {commandStations.map(d => (
        <EntityRow key={d.id} drone={d} mode="CMD" onClick={() => setSelectedDrone(d.id)} />
      ))}

      <div className="px-3 py-1.5 bg-gotham-bg-tertiary/50 border-b border-gotham-border-muted">
        <span className="gotham-label">Members ({members.length})</span>
      </div>
      {members.map(d => (
        <EntityRow key={d.id} drone={d} mode={memberMode(d)} onClick={() => setSelectedDrone(d.id)} />
      ))}

      {droneList.length === 0 && (
        <div className="px-3 py-4 text-data-sm text-gotham-text-tertiary text-center">No modules online.</div>
      )}
    </div>
  );
};

const EntityRow = ({ drone, mode, onClick }) => {
  const statusInfo = DroneStatus[drone.status];
  const battery = drone.battery ?? 0;
  const rssi = drone.signalStrength ?? -60;

  return (
    <div
      onClick={onClick}
      className="px-3 py-2 border-b border-gotham-border-muted cursor-pointer hover:bg-gotham-bg-tertiary transition-colors flex items-center justify-between"
    >
      <div className="flex items-center gap-2">
        <span className={`text-data-sm font-bold px-1 py-0.5 rounded ${
          mode === 'CMD' ? 'bg-gotham-accent-blue/20 text-gotham-accent-blue'
          : mode === 'SCOUT' ? 'bg-gotham-accent-teal/20 text-gotham-accent-teal'
          : 'bg-gotham-accent-orange/20 text-gotham-accent-orange'
        }`}>
          {mode === 'CMD' ? 'CMD' : mode === 'SCOUT' ? 'SCT' : 'EXE'}
        </span>
        <span className="text-data text-gotham-text-secondary font-mono">{drone.name || drone.id}</span>
      </div>
      <div className="flex items-center gap-3">
        <span className={`text-data-sm ${statusInfo?.color || 'text-gotham-text-tertiary'}`}>
          {statusInfo?.label || drone.status}
        </span>
        <span className="text-data-sm font-mono text-gotham-text-tertiary">{rssi}dBm</span>
        <span className={`text-data-sm font-mono ${
          battery > 60 ? 'text-gotham-accent-green' : battery > 30 ? 'text-gotham-accent-yellow' : 'text-gotham-accent-red'
        }`}>
          {battery}%
        </span>
      </div>
    </div>
  );
};

const DetectionsTab = () => {
  const instance = useInstance();
  const {
    detections,
    selectedDetection,
    setSelectedDetection,
    drones,
    updateDetection,
    addTask,
  } = useFleetStore();

  const sortedDetections = [...detections].sort((a, b) => b.timestamp - a.timestamp);

  const pendingCount = detections.filter(d => d.status === 'PENDING').length;
  const assignedCount = detections.filter(d => d.status === 'ASSIGNED').length;
  const completedCount = detections.filter(d => d.status === 'COMPLETED').length;

  const getTimeSince = (timestamp) => {
    const diff = Date.now() - timestamp;
    if (diff < 60000) return `${Math.floor(diff / 1000)}s ago`;
    if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
    return `${Math.floor(diff / 3600000)}h ago`;
  };

  const handleAssign = (detection) => {
    const executors = Object.values(drones).filter(d =>
      d.role === 'EXECUTOR' && d.status === 'IDLE' && d.battery > 20
    );
    if (executors.length === 0) return;
    const best = executors[0];

    updateDetection(detection.id, {
      status: 'ASSIGNED',
      assignedTo: best.id,
    });

    addTask({
      type: instance.detectionToTask[detection.type] || Object.keys(instance.taskTypes)[0],
      targetPosition: detection.position,
      status: 'PENDING',
      assignedTo: best.id,
      priority: 'high',
      detectionId: detection.id,
    });
  };

  return (
    <div className="flex flex-col h-full">
      <div className="px-3 py-2 border-b border-gotham-border-muted flex gap-2">
        <StatBadge label="Pending" value={pendingCount} color="yellow" />
        <StatBadge label="Assigned" value={assignedCount} color="blue" />
        <StatBadge label="Done" value={completedCount} color="green" />
      </div>

      <div className="flex-1 overflow-y-auto scrollbar-thin">
        {sortedDetections.length === 0 ? (
          <div className="px-3 py-8 text-center text-gotham-text-tertiary text-data">
            No detections yet
          </div>
        ) : (
          sortedDetections.map((detection) => (
            <DetectionCard
              key={detection.id}
              detection={detection}
              isSelected={selectedDetection === detection.id}
              onSelect={() => setSelectedDetection(detection.id)}
              onAssign={() => handleAssign(detection)}
              timeSince={getTimeSince(detection.timestamp)}
            />
          ))
        )}
      </div>

      <div className="px-3 py-2 border-t border-gotham-border bg-gotham-bg-tertiary/30">
        <div className="flex flex-wrap gap-x-3 gap-y-1">
          {Object.entries(DetectionType).map(([key, info]) => (
            <div key={key} className="flex items-center gap-1 text-data-sm text-gotham-text-tertiary">
              <span className="w-2 h-2 rounded-full" style={{ backgroundColor: info.color }} />
              <span>{info.label}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

const StatBadge = ({ label, value, color }) => {
  const colors = {
    yellow: 'bg-gotham-accent-yellow/10 text-gotham-accent-yellow border-gotham-accent-yellow/20',
    blue: 'bg-gotham-accent-blue/10 text-gotham-accent-blue border-gotham-accent-blue/20',
    green: 'bg-gotham-accent-green/10 text-gotham-accent-green border-gotham-accent-green/20',
  };

  return (
    <div className={`px-2 py-1 rounded border text-data-sm ${colors[color]}`}>
      <span className="font-bold font-mono">{value}</span>
      <span className="ml-1 opacity-70">{label}</span>
    </div>
  );
};

const DetectionCard = ({ detection, isSelected, onSelect, onAssign, timeSince }) => {
  const typeInfo = DetectionType[detection.type];

  const severityColor = {
    critical: 'bg-gotham-accent-red',
    high: 'bg-gotham-accent-orange',
    medium: 'bg-gotham-accent-yellow',
    low: 'bg-gotham-accent-blue',
  }[typeInfo?.severity] || 'bg-gotham-text-tertiary';

  const statusColor = {
    PENDING: 'text-gotham-accent-yellow',
    ASSIGNED: 'text-gotham-accent-blue',
    IN_PROGRESS: 'text-gotham-accent-teal',
    COMPLETED: 'text-gotham-accent-green',
  }[detection.status] || 'text-gotham-text-tertiary';

  return (
    <div
      onClick={onSelect}
      className={`border-b border-gotham-border-muted cursor-pointer transition-all flex ${
        isSelected
          ? 'bg-gotham-accent-blue/10 border-l-2 border-l-gotham-accent-blue'
          : 'hover:bg-gotham-bg-tertiary border-l-2 border-l-transparent'
      }`}
    >
      <div className={`w-1 ${severityColor} flex-shrink-0`} />

      <div className="flex-1 px-3 py-2">
        <div className="flex justify-between items-center mb-1">
          <div className="flex items-center gap-1.5">
            <span
              className="w-2.5 h-2.5 rounded-full"
              style={{ backgroundColor: typeInfo?.color || '#888' }}
            />
            <span className="text-data text-gotham-text-primary font-medium">
              {typeInfo?.label || detection.type}
            </span>
          </div>
          <span className={`text-data-sm font-mono font-medium ${statusColor}`}>
            {detection.status}
          </span>
        </div>

        <div className="flex items-center justify-between text-data-sm text-gotham-text-tertiary">
          <span className="font-mono">
            {(detection.position?.lat ?? detection.position?.latitude)?.toFixed(4)},{' '}
            {(detection.position?.lon ?? detection.position?.longitude)?.toFixed(4)}
          </span>
          <span>{timeSince}</span>
        </div>

        <div className="flex items-center justify-between mt-0.5 text-data-sm">
          <span className="text-gotham-text-tertiary">
            Conf: <span className={`font-mono ${
              detection.confidence > 0.9 ? 'text-gotham-accent-green' :
              detection.confidence > 0.7 ? 'text-gotham-accent-yellow' : 'text-gotham-accent-red'
            }`}>{(detection.confidence * 100).toFixed(0)}%</span>
          </span>
          <span className="text-gotham-accent-teal">{detection.detectedBy}</span>
        </div>

        {detection.assignedTo && (
          <div className="mt-1 text-data-sm text-gotham-text-tertiary">
            Assigned: <span className="text-gotham-accent-orange font-mono">{detection.assignedTo}</span>
          </div>
        )}

        {isSelected && detection.status === 'PENDING' && (
          <button
            onClick={(e) => { e.stopPropagation(); onAssign(); }}
            className="mt-1.5 w-full gotham-btn-primary text-data-sm text-center py-1"
          >
            Assign Executor
          </button>
        )}
      </div>
    </div>
  );
};

export default DetectionPanel;
