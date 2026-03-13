import type { RecentAction } from '../types/snapshot';
import { relativeTime, taskTypeLabel } from '../utils/format';
import SectionPanel from './SectionPanel';
import './JournalPanel.css';

interface Props {
  actions: (RecentAction | string)[] | undefined;
}

const normalizeAction = (a: RecentAction | string): RecentAction =>
  typeof a === 'string' ? { action: 'log', ts: '', detail: a } : a;

export default function JournalPanel({ actions }: Props) {
  const list = (actions ?? []).map(normalizeAction).slice(0, 10);

  return (
    <SectionPanel
      title="Journal"
      tag="RECENT"
      tagColor="var(--info)"
      count={list.length || undefined}
      collapsible
    >
      {list.length === 0 ? (
        <div className="sp__empty">No recent actions</div>
      ) : (
        <div className="jnp__timeline">
          {list.map((a, i) => (
            <div key={i} className="jnp__entry">
              <div className="jnp__dot" />
              <div className="jnp__content">
                <div className="jnp__header">
                  <span className="jnp__action-badge">{taskTypeLabel(a.action ?? 'log')}</span>
                  <span className="jnp__ts">{relativeTime(a.ts)}</span>
                </div>
                <span className="jnp__detail">{a.detail ?? ''}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </SectionPanel>
  );
}
