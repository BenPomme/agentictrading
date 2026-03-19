export type Zone =
  | 'factory-health'
  | 'pipeline'
  | 'paper-models'
  | 'family-explorer'
  | 'promotion-queue'
  | 'compute-cost'
  | 'venue-readiness'
  | 'alerts';

export interface NavItem {
  zone: Zone;
  label: string;
  icon: string;
  description: string;
}

export const NAV_ITEMS: NavItem[] = [
  {
    zone: 'factory-health',
    label: 'Overview',
    icon: '◎',
    description: 'Operator overview, progression, and live dashboard status',
  },
  {
    zone: 'pipeline',
    label: 'Research Funnel',
    icon: '⇢',
    description: 'Stage funnel, progression quality, and stuck detector',
  },
  {
    zone: 'paper-models',
    label: 'Paper / Shadow',
    icon: '▤',
    description: 'Featured paper models, shadow lineages, and live monitoring',
  },
  {
    zone: 'family-explorer',
    label: 'Families',
    icon: '⊞',
    description: 'Family lifecycle, lineage tree, model league',
  },
  {
    zone: 'promotion-queue',
    label: 'Promotion Queue',
    icon: '↑',
    description: 'Promotion-ready, accumulating, blocked, and live-readiness signals',
  },
  {
    zone: 'compute-cost',
    label: 'Compute',
    icon: '⌁',
    description: 'Budget governance, agent runs, token burn',
  },
  {
    zone: 'venue-readiness',
    label: 'Venues',
    icon: '◌',
    description: 'Connector health, scope enforcement, blocker map',
  },
  {
    zone: 'alerts',
    label: 'Alerts',
    icon: '⚠',
    description: 'Incidents, escalations, maintenance queue',
  },
];
