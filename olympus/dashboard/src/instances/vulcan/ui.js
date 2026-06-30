export const id = 'vulcan';
export const name = 'Industrial & Infrastructure';
export const missionIdPrefix = 'INSP';
export const domain = 'vulcan';

export const advisor = {
  persona: 'Inspection Advisor',
  greeting: 'VULCAN Inspection Advisor online. Query asset condition, schedule inspections, or review findings.',
  quickActions: [
    { label: 'Findings', msg: 'What defects have been found so far?' },
    { label: 'Priority', msg: 'What are the highest priority items?' },
    { label: 'Coverage', msg: 'What is the current inspection coverage?' },
    { label: 'Help', msg: 'help' },
  ],
};

export const ui = {
  zoneLabel: 'Asset',
  zonePluralLabel: 'Assets',
  detectionLabel: 'Defect',
  executionLabel: 'Inspecting',
};

export const logo = (size = 20) => `<svg width="${size}" height="${size}" viewBox="0 0 100 100"><path d="M25 65 L75 65 L80 75 L20 75 Z" fill="none" stroke="#db6d28" stroke-width="3" stroke-linejoin="round"/><rect x="35" y="75" width="30" height="8" rx="2" fill="none" stroke="#db6d28" stroke-width="2.5"/><path d="M55 20 L55 45 L70 45 L70 55 L55 55 L55 65" stroke="#db6d28" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/><rect x="42" y="10" width="26" height="15" rx="3" fill="none" stroke="#db6d28" stroke-width="2.5"/></svg>`;
