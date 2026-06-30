export const id = 'athena';
export const name = 'Defense & Intelligence';
export const missionIdPrefix = 'RECON';
export const domain = 'athena';

export const advisor = {
  persona: 'Tactical Advisor',
  greeting: 'ATHENA Tactical Advisor online. Report sector status, request ISR, or query threat assessment.',
  quickActions: [
    { label: 'SITREP', msg: 'Give me a situation report.' },
    { label: 'Threats', msg: 'What are the current threat assessments?' },
    { label: 'ISR Status', msg: 'What is the current ISR status?' },
    { label: 'Help', msg: 'help' },
  ],
};

export const ui = {
  zoneLabel: 'Sector',
  zonePluralLabel: 'Sectors',
  detectionLabel: 'Contact',
  executionLabel: 'Engaging',
};

export const logo = (size = 20) => `<svg width="${size}" height="${size}" viewBox="0 0 100 100"><path d="M50 10 C30 10 15 30 15 55 C15 65 20 72 30 75 L30 85 C30 88 33 90 35 90 L40 90 L40 80 L60 80 L60 90 L65 90 C67 90 70 88 70 85 L70 75 C80 72 85 65 85 55 C85 30 70 10 50 10Z" fill="none" stroke="#f85149" stroke-width="3"/><line x1="50" y1="35" x2="50" y2="75" stroke="#f85149" stroke-width="2"/><line x1="30" y1="55" x2="70" y2="55" stroke="#f85149" stroke-width="1.5" opacity="0.5"/></svg>`;
