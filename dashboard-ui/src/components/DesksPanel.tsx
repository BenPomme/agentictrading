import { useState } from 'react';
import type { Desk } from '../types/snapshot';
import { statusColor } from '../utils/format';
import SectionPanel from './SectionPanel';
import './DesksPanel.css';

interface Props {
  desks: Desk[] | undefined;
}

export default function DesksPanel({ desks }: Props) {
  const list = desks ?? [];
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const toggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  return (
    <SectionPanel
      title="Research Systems"
      count={list.length || undefined}
      collapsible
    >
      {list.length === 0 ? (
        <div className="sp__empty">No desks configured</div>
      ) : (
        <div className="dkp__list">
          {list.map((d) => {
            const open = expanded.has(d.desk_id);
            return (
              <div key={d.desk_id} className="dkp__card">
                <div className="dkp__card-header" onClick={() => toggle(d.desk_id)}>
                  <div className="dkp__card-left">
                    <span className="dkp__label">{d.label}</span>
                    <span className="dkp__kind">{d.desk_kind}</span>
                  </div>
                  <div className="dkp__card-right">
                    <span className="dkp__counts">
                      {d.active_count}/{d.member_count}
                    </span>
                    <span className={`dkp__chevron ${open ? 'dkp__chevron--open' : ''}`}>‹</span>
                  </div>
                </div>
                {open && d.members.length > 0 && (
                  <div className="dkp__members">
                    {d.members.map((m) => (
                      <div key={m.name} className="dkp__member">
                        <span
                          className="dkp__member-dot"
                          style={{ background: statusColor(m.status) }}
                        />
                        <span className="dkp__member-name">{m.display_name || m.name}</span>
                        <span className="dkp__member-stats">
                          {m.lineage_count}L · {(m.families ?? []).length}F
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </SectionPanel>
  );
}
