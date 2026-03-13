import type { MaintenanceItem } from '../types/snapshot';
import { formatPct, statusColor } from '../utils/format';
import SectionPanel from './SectionPanel';
import './MaintenancePanel.css';

interface Props {
  items: MaintenanceItem[] | undefined;
}

function priorityDots(p: number) {
  return '●'.repeat(Math.min(p, 5)) + '○'.repeat(Math.max(0, 5 - p));
}

export default function MaintenancePanel({ items }: Props) {
  const list = items ?? [];

  return (
    <SectionPanel
      title="Maintenance"
      tag="QUEUE"
      tagColor="var(--accent)"
      count={list.length || undefined}
      collapsible
    >
      {list.length === 0 ? (
        <div className="sp__empty">Maintenance queue clear</div>
      ) : (
        <div className="mtp__scroll">
          {list.slice(0, 20).map((m, i) => (
            <div
              key={i}
              className="mtp__item"
              style={{ borderLeftColor: statusColor(m.execution_health_status) }}
            >
              <div className="mtp__row-top">
                <span className="mtp__dots" title={`Priority ${m.priority}`}>
                  {priorityDots(m.priority)}
                </span>
                <span className="mtp__family">{m.family_id}</span>
                <span className="mtp__lineage">{m.lineage_id.split('/').pop()}</span>
              </div>
              <div className="mtp__row-mid">
                <span className="mtp__action-badge">{m.action}</span>
                <span className="mtp__roi" style={{ color: m.roi_pct >= 0 ? 'var(--ok)' : 'var(--crit)' }}>
                  {formatPct(m.roi_pct)}
                </span>
                <span className="mtp__trades">{m.trade_count} trades</span>
              </div>
              {(m.recommended_actions ?? []).length > 0 && (
                <div className="mtp__actions">
                  {(m.recommended_actions ?? []).map((a, j) => (
                    <span key={j} className="mtp__rec">{a}</span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </SectionPanel>
  );
}
