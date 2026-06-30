import { detectionTypes, detectionGeneration } from './models';
import { taskTypes, detectionToTask } from './executors';
import { operatingArea, vehicleDefaults, environment } from './config';
import { id, name, missionIdPrefix, domain, advisor, ui, logo } from './ui';
import { integrations } from './integrations';

const vulcan = {
  id, name, missionIdPrefix, domain,
  operatingArea, detectionTypes, detectionGeneration,
  taskTypes, detectionToTask,
  vehicleDefaults, environment,
  advisor, ui, integrations, logo,
};

export default vulcan;
