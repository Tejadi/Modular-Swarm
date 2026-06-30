import { useMemo } from 'react';
import ceres from './ceres';
import athena from './athena';
import vulcan from './vulcan';
import hermes from './hermes';

export const INSTANCES = { ceres, athena, vulcan, hermes };

const REQUIRED_FIELDS = ['id', 'name', 'missionIdPrefix', 'domain', 'operatingArea', 'detectionTypes', 'taskTypes', 'detectionToTask', 'vehicleDefaults', 'advisor', 'environment', 'ui'];

export const validateInstance = (instance) => {
  if (!instance || typeof instance !== 'object') return false;
  return REQUIRED_FIELDS.every((field) => field in instance);
};

const getInstanceId = () => {
  if (typeof window !== 'undefined') {
    const params = new URLSearchParams(window.location.search);
    const urlInstance = params.get('instance');
    if (urlInstance && INSTANCES[urlInstance]) return urlInstance;
  }
  const envInstance = process.env.REACT_APP_INSTANCE;
  if (envInstance && INSTANCES[envInstance]) return envInstance;
  return 'athena';
};

let _activeInstance = null;

export const getActiveInstance = () => {
  if (!_activeInstance) {
    const id = getInstanceId();
    _activeInstance = INSTANCES[id] || INSTANCES.athena;
  }
  return _activeInstance;
};

export const useInstance = () => {
  return useMemo(() => getActiveInstance(), []);
};

export const getActiveProfile = getActiveInstance;
export const useMissionProfile = useInstance;
export const PROFILES = INSTANCES;

export default getActiveInstance;
