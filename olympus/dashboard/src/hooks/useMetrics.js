/**
 * CERES OS - Metrics Update Hook
 * Periodically computes operational metrics from fleet state.
 */

import { useEffect } from 'react';
import useFleetStore from '../store/fleetStore';

const useMetrics = (intervalMs = 2000) => {
  const updateMetrics = useFleetStore((state) => state.updateMetrics);

  useEffect(() => {
    const timer = setInterval(() => {
      updateMetrics();
    }, intervalMs);

    return () => clearInterval(timer);
  }, [updateMetrics, intervalMs]);
};

export default useMetrics;
