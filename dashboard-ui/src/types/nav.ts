export type Zone =
  | 'factory-health'
  | 'pipeline'
  | 'paper-models'
  | 'family-explorer'
  | 'goldfish-dna'
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
    label: 'Factory Health',
    icon: '⬡',
    description: 'Runtime status, readiness score, cycle metrics',
  },
  {
    zone: 'pipeline',
    label: 'Pipeline',
    icon: '⇡',
    description: 'Stage funnel, promotion queue, stuck detector',
  },
  {
    zone: 'paper-models',
    label: 'Paper Models',
    icon: '▣',
    description: 'Active paper lineages, P&L, holdoff state',
  },
  {
    zone: 'family-explorer',
    label: 'Families',
    icon: '⊞',
    description: 'Family lifecycle, lineage tree, model league',
  },
  {
    zone: 'goldfish-dna',
    label: 'Goldfish DNA',
    icon: '∿',
    description: 'Provenance health, memory influence, DNA packets',
  },
  {
    zone: 'compute-cost',
    label: 'Compute / Cost',
    icon: '⚡',
    description: 'Budget governance, agent runs, token burn',
  },
  {
    zone: 'venue-readiness',
    label: 'Venues',
    icon: '◉',
    description: 'Connector health, scope enforcement, blocker map',
  },
  {
    zone: 'alerts',
    label: 'Alerts',
    icon: '⚠',
    description: 'Incidents, escalations, maintenance queue',
  },
];
