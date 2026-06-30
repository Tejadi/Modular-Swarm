// General / defense tasking (was agricultural spray/fertilize). SEARCH-led.
export const taskTypes = {
  SEARCH: { label: 'Search', color: '#39d2c0' },
  INVESTIGATE: { label: 'Investigate', color: '#388bfd' },
  TRACK: { label: 'Track', color: '#d29922' },
  MARK: { label: 'Mark Location', color: '#a371f7' },
  INTERCEPT: { label: 'Intercept', color: '#f85149' },
};

export const detectionToTask = {
  PERSON: 'INVESTIGATE',
  VEHICLE: 'TRACK',
  WEAPON: 'INTERCEPT',
  THERMAL: 'INVESTIGATE',
  OBJECT: 'INVESTIGATE',
  UNKNOWN: 'INVESTIGATE',
};
