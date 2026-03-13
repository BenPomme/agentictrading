import type { Alert } from '../types/snapshot';
import SectionPanel from './SectionPanel';
import './AlertsPanel.css';

const SEVERITY_ORDER: Record<string, number> = { critical: 0, error: 1, warning: 2, info: 3 };
const SEVERITY_ICON: Record<string, string> = { critical: '⛔', error: '🔴', warning: '⚠️', info: 'ℹ️' };

interface Props {
  alerts: Alert[] | undefined;
}

export default function AlertsPanel({ alerts }: Props) {
  const sorted = [...(alerts ?? [])].sort(
    (a, b) => (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9),
  );

  return (
    <SectionPanel
      title="Alerts"
      tag="LIVE"
      tagColor="var(--crit)"
      count={sorted.length || undefined}
      collapsible
    >
      {sorted.length === 0 ? (
        <div className="sp__empty">No active alerts</div>
      ) : (
        <ul className="alp__list">
          {sorted.map((a, i) => (
            <li
              key={i}
              className={`alp__item ${a.severity === 'critical' ? 'alp__item--crit' : ''}`}
            >
              <span className="alp__icon">{SEVERITY_ICON[a.severity] ?? '•'}</span>
              <div className="alp__content">
                <span className="alp__title">{a.title}</span>
                <span className="alp__detail">{a.detail}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </SectionPanel>
  );
}
