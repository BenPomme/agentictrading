import type { OperatorSignals } from '../types/snapshot';
import SectionPanel from './SectionPanel';
import './EscalationsPanel.css';

interface Props {
  signals: OperatorSignals | undefined;
}

interface EscalationItem {
  kind: 'escalation' | 'human_action';
  family_id?: string;
  lineage_id?: string;
  reason?: string;
  priority?: number;
  [key: string]: unknown;
}

export default function EscalationsPanel({ signals }: Props) {
  const items: EscalationItem[] = [];

  for (const e of (signals?.escalation_candidates ?? []) as EscalationItem[]) {
    items.push({ ...e, kind: 'escalation' });
  }
  for (const h of (signals?.human_action_required ?? []) as EscalationItem[]) {
    items.push({ ...h, kind: 'human_action' });
  }

  items.sort((a, b) => (a.priority ?? 99) - (b.priority ?? 99));

  const total = items.length;

  return (
    <SectionPanel
      title="Escalations"
      tag="OPERATOR"
      tagColor="var(--warn)"
      count={total || undefined}
      collapsible
    >
      {total === 0 ? (
        <div className="sp__empty">No escalations pending</div>
      ) : (
        <ul className="esp__list">
          {items.map((item, i) => (
            <li key={i} className="esp__item">
              <span className="esp__priority">{item.priority ?? '—'}</span>
              <div className="esp__body">
                <div className="esp__meta">
                  <span className={`esp__kind esp__kind--${item.kind}`}>
                    {item.kind === 'human_action' ? 'ACTION REQ' : 'ESCALATION'}
                  </span>
                  {item.family_id && <span className="esp__fam">{item.family_id}</span>}
                  {item.lineage_id && (
                    <span className="esp__lin">{(item.lineage_id as string).split('/').pop()}</span>
                  )}
                </div>
                {item.reason && <span className="esp__reason">{item.reason as string}</span>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </SectionPanel>
  );
}
