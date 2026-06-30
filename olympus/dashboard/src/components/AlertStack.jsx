/**
 * CERES OS - Alert Stack
 * Bottom-right toast notifications with auto-dismiss
 * Gotham design system
 */

import React, { useEffect } from 'react';
import useFleetStore from '../store/fleetStore';

const AlertStack = () => {
  const { alerts, dismissAlert } = useFleetStore();

  // Auto-dismiss alerts after 8 seconds
  useEffect(() => {
    const timers = alerts.map((alert) =>
      setTimeout(() => {
        dismissAlert(alert.id);
      }, 8000)
    );

    return () => {
      timers.forEach(timer => clearTimeout(timer));
    };
  }, [alerts, dismissAlert]);

  if (alerts.length === 0) return null;

  return (
    <div className="absolute bottom-16 right-4 z-50 flex flex-col-reverse gap-2 pointer-events-none max-w-sm">
      {alerts.slice(0, 5).map((alert, index) => (
        <Alert
          key={alert.id}
          alert={alert}
          onDismiss={() => dismissAlert(alert.id)}
          index={index}
        />
      ))}
    </div>
  );
};

const Alert = ({ alert, onDismiss, index }) => {
  const typeConfig = {
    detection: {
      borderColor: 'border-gotham-accent-yellow',
      accentColor: 'bg-gotham-accent-yellow',
      textColor: 'text-gotham-accent-yellow',
      icon: (
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
        </svg>
      ),
    },
    emergency: {
      borderColor: 'border-gotham-accent-red',
      accentColor: 'bg-gotham-accent-red',
      textColor: 'text-gotham-accent-red',
      icon: (
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      ),
    },
    info: {
      borderColor: 'border-gotham-accent-blue',
      accentColor: 'bg-gotham-accent-blue',
      textColor: 'text-gotham-accent-blue',
      icon: (
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      ),
    },
    success: {
      borderColor: 'border-gotham-accent-green',
      accentColor: 'bg-gotham-accent-green',
      textColor: 'text-gotham-accent-green',
      icon: (
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
        </svg>
      ),
    },
  };

  const config = typeConfig[alert.type] || typeConfig.info;

  return (
    <div
      className={`bg-gotham-bg-secondary border ${config.borderColor} rounded shadow-lg pointer-events-auto flex items-start gap-2 px-3 py-2.5 min-w-[280px]`}
      style={{
        animation: 'slideUp 0.2s ease-out',
        animationFillMode: 'forwards',
      }}
    >
      {/* Left accent bar */}
      <div className={`w-0.5 self-stretch ${config.accentColor} rounded-full flex-shrink-0`} />

      {/* Icon */}
      <span className={`${config.textColor} flex-shrink-0 mt-0.5`}>{config.icon}</span>

      {/* Message */}
      <span className="flex-1 text-data text-gotham-text-secondary">{alert.message}</span>

      {/* Dismiss */}
      <button
        onClick={onDismiss}
        className="text-gotham-text-tertiary hover:text-gotham-text-secondary transition-colors flex-shrink-0 mt-0.5"
      >
        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>
  );
};

export default AlertStack;
