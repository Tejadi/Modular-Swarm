/**
 * Synthetic Farm Generator (backward-compatible re-export)
 * Delegates to the profile-driven environmentGenerator.
 */

import { generateEnvironment } from './environmentGenerator';
import agriculture from '../profiles/agriculture';

/**
 * Generate a complete synthetic farm (agriculture layout)
 * @param {number} centerLat - Farm center latitude
 * @param {number} centerLon - Farm center longitude
 * @param {number} sizeMeters - Farm size in meters (default 500)
 * @returns {Object} Complete farm data structure
 */
export const generateSyntheticFarm = (centerLat, centerLon, sizeMeters = 500) => {
  return generateEnvironment(agriculture, centerLat, centerLon, sizeMeters);
};
