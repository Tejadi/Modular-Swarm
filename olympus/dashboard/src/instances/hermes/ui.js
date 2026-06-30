export const id = 'hermes';
export const name = 'Search & Rescue';
export const missionIdPrefix = 'SAR';
export const domain = 'hermes';

export const advisor = {
  persona: 'SAR Coordinator',
  greeting: 'HERMES SAR Coordinator online. Report search status, mark findings, or request resource deployment.',
  quickActions: ['Search Status', 'Findings', 'Weather', 'Help'],
};

export const ui = {
  zoneLabel: 'Grid',
  zonePluralLabel: 'Grids',
  detectionLabel: 'Finding',
  executionLabel: 'Responding',
};

export const logo = (size = 20) => `<svg width="${size}" height="${size}" viewBox="0 0 100 100"><line x1="50" y1="15" x2="50" y2="90" stroke="#388bfd" stroke-width="3" stroke-linecap="round"/><circle cx="50" cy="12" r="5" fill="#388bfd"/><path d="M50 30 C35 30 30 40 40 45 C50 50 50 50 50 50" fill="none" stroke="#388bfd" stroke-width="2.5"/><path d="M50 30 C65 30 70 40 60 45 C50 50 50 50 50 50" fill="none" stroke="#388bfd" stroke-width="2.5"/><path d="M50 50 C35 50 30 60 40 65 C50 70 50 70 50 70" fill="none" stroke="#388bfd" stroke-width="2.5"/><path d="M50 50 C65 50 70 60 60 65 C50 70 50 70 50 70" fill="none" stroke="#388bfd" stroke-width="2.5"/><path d="M35 20 C30 15 25 18 30 22 L42 28" fill="none" stroke="#388bfd" stroke-width="2"/><path d="M65 20 C70 15 75 18 70 22 L58 28" fill="none" stroke="#388bfd" stroke-width="2"/></svg>`;
