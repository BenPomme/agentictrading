import type { IdeasState, IdeaItem } from '../types/snapshot';
import SectionPanel from './SectionPanel';
import './IdeasPanel.css';

interface Props {
  ideas: IdeasState | undefined;
}

const STATUS_COLORS: Record<string, string> = {
  new: 'var(--info)',
  adapted: 'var(--accent)',
  incubated: 'var(--warn)',
  tested: 'var(--ok)',
  promoted: 'var(--ok)',
  rejected: 'var(--crit)',
};

function IdeaCard({ item }: { item: IdeaItem }) {
  return (
    <div className="idp__card">
      <div className="idp__card-header">
        <span className="idp__card-title">{item.title}</span>
        <span
          className="idp__card-status"
          style={{ background: `color-mix(in srgb, ${STATUS_COLORS[item.status] ?? 'var(--text-dim)'} 20%, transparent)`, color: STATUS_COLORS[item.status] ?? 'var(--text-dim)' }}
        >
          {item.status}
        </span>
      </div>
      {item.summary && (
        <span className="idp__card-summary">
          {item.summary.length > 100 ? item.summary.slice(0, 100) + '…' : item.summary}
        </span>
      )}
      {(item.family_candidates ?? []).length > 0 && (
        <div className="idp__card-families">
          {(item.family_candidates ?? []).slice(0, 3).map((f) => (
            <span key={f} className="idp__card-fam">{f}</span>
          ))}
        </div>
      )}
    </div>
  );
}

export default function IdeasPanel({ ideas }: Props) {
  if (!ideas?.present) {
    return (
      <SectionPanel title="Ideas Intake" collapsible defaultCollapsed>
        <div className="sp__empty">Ideas module not active</div>
      </SectionPanel>
    );
  }

  const counts = ideas.status_counts;
  const active = ideas.items ?? [];
  const archived = ideas.archived_items ?? [];

  return (
    <SectionPanel
      title="Ideas Intake"
      count={ideas.idea_count || undefined}
      collapsible
    >
      <div className="idp__status-bar">
        {Object.entries(counts).map(([k, v]) => (
          <span
            key={k}
            className="idp__pill"
            style={{ background: `color-mix(in srgb, ${STATUS_COLORS[k] ?? 'var(--text-dim)'} 20%, transparent)`, color: STATUS_COLORS[k] ?? 'var(--text-dim)' }}
          >
            {k} {v}
          </span>
        ))}
      </div>

      {active.length > 0 && (
        <div className="idp__cards">
          {active.map((item) => (
            <IdeaCard key={item.idea_id} item={item} />
          ))}
        </div>
      )}

      {archived.length > 0 && (
        <details className="idp__archived">
          <summary className="idp__archived-summary">
            Archived ({archived.length})
          </summary>
          <div className="idp__cards">
            {archived.slice(0, 10).map((item) => (
              <IdeaCard key={item.idea_id} item={item} />
            ))}
          </div>
        </details>
      )}
    </SectionPanel>
  );
}
