import type { ModelLeagueEntry } from '../types/snapshot';
import SectionPanel from './SectionPanel';
import './LeaguePanel.css';

interface Props {
  league: ModelLeagueEntry[] | undefined;
}

function topScore(entry: ModelLeagueEntry): number {
  if (!entry.rankings?.length) return 0;
  return Math.max(...entry.rankings.map((r) => r.ranking_score));
}

export default function LeaguePanel({ league }: Props) {
  const list = league ?? [];

  return (
    <SectionPanel
      title="Model League"
      count={list.length || undefined}
      collapsible
    >
      {list.length === 0 ? (
        <div className="sp__empty">No league entries</div>
      ) : (
        <div className="lgp__scroll">
          <table className="lgp__table">
            <thead>
              <tr>
                <th>Family</th>
                <th>Incumbent</th>
                <th>Challenger</th>
                <th>Score</th>
                <th>Incubation</th>
              </tr>
            </thead>
            <tbody>
              {list.map((e) => {
                const rp = e.isolated_evidence_ready;
                return (
                  <tr key={e.family_id} className={rp ? 'lgp__row--rp' : ''}>
                    <td className="lgp__fam">
                      {e.label || e.family_id}
                      {rp && <span className="lgp__rp">R+</span>}
                    </td>
                    <td className="lgp__mono">
                      {e.primary_incumbent_lineage_id?.split('/').pop() ?? '—'}
                    </td>
                    <td className="lgp__mono">
                      {e.isolated_challenger_lineage_id?.split('/').pop() ?? '—'}
                    </td>
                    <td className="lgp__score">{topScore(e).toFixed(1)}</td>
                    <td>
                      <span className={`lgp__inc lgp__inc--${e.incubation_status}`}>
                        {e.incubation_status}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </SectionPanel>
  );
}
