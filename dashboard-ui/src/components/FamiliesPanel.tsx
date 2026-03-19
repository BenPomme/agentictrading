import type { Family } from '../types/snapshot';
import { formatPct, relativeTime, statusColor, venueIcon } from '../utils/format';
import SectionPanel from './SectionPanel';
import './FamiliesPanel.css';

interface Props {
  families: Family[] | undefined;
}

export default function FamiliesPanel({ families }: Props) {
  const list = families ?? [];

  return (
    <SectionPanel
      title="Research Families"
      count={list.length || undefined}
      collapsible
    >
      {list.length === 0 ? (
        <div className="sp__empty">No families registered</div>
      ) : (
        <div className="fmp__grid">
          {list.map((f) => (
            <div key={f.family_id} className="fmp__card">
              <div className="fmp__card-header">
                <span className="fmp__label">{f.label || f.family_id}</span>
                <span
                  className="fmp__status-dot"
                  style={{ background: statusColor(f.status ?? 'unknown') }}
                  title={f.status ?? 'unknown'}
                />
              </div>
              <div className="fmp__meta">
                <span className="fmp__venue">
                  {venueIcon((f.target_venues ?? [f.venue ?? ''])[0] ?? '')} {f.venue ?? '—'}
                </span>
                <span className="fmp__lineages">
                  {f.active_lineage_count}/{f.lineage_count} lineages
                </span>
              </div>
              {f.champion_lineage_id && (
                <div className="fmp__champion">
                  <span className="fmp__champ-label">CHAMPION</span>
                  <span
                    className="fmp__champ-roi"
                    style={{ color: f.champion_roi_pct >= 0 ? 'var(--ok)' : 'var(--crit)' }}
                  >
                    {formatPct(f.champion_roi_pct)}
                  </span>
                  <span className="fmp__champ-trades">{f.champion_trade_count}t</span>
                </div>
              )}
              <div className="fmp__meta">
                <span className="fmp__lineages">
                  {String(f.champion_paper_state ?? 'unknown').replace(/_/g, ' ')}
                </span>
                <span className="fmp__lineages">
                  {f.current_runner_portfolio_id ?? 'no runner'}
                </span>
              </div>
              <div className="fmp__meta">
                <span className="fmp__lineages">
                  last activity {f.last_activity_at ? relativeTime(f.last_activity_at) : '—'}
                </span>
                <span className="fmp__lineages">
                  last agent {f.last_agent_run_at ? relativeTime(f.last_agent_run_at) : '—'}
                </span>
              </div>
              <div className="fmp__meta">
                <span className="fmp__lineages">
                  {(String(f.origin ?? 'unknown')).replace(/_/g, ' ')}
                </span>
                <span className="fmp__lineages">
                  {String(f.source_idea_id ?? 'no idea')}
                </span>
              </div>
              {f.research_positive && <span className="fmp__rp-badge">R+</span>}
            </div>
          ))}
        </div>
      )}
    </SectionPanel>
  );
}
