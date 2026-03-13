import { useState, useMemo } from 'react';
import type { Lineage } from '../types/snapshot';
import { formatPct } from '../utils/format';
import SectionPanel from './SectionPanel';
import './LineageBoard.css';

interface Props {
  lineages: Lineage[] | undefined;
}

type SortKey = 'lineage_id' | 'family_id' | 'role' | 'current_stage' | 'roi_pct' | 'trade_count' | 'paper_days' | 'runtime_lane_kind';

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: 'lineage_id', label: 'Lineage' },
  { key: 'family_id', label: 'Family' },
  { key: 'role', label: 'Role' },
  { key: 'current_stage', label: 'Stage' },
  { key: 'roi_pct', label: 'ROI' },
  { key: 'trade_count', label: 'Trades' },
  { key: 'paper_days', label: 'Days' },
  { key: 'runtime_lane_kind', label: 'Lane' },
];

export default function LineageBoard({ lineages }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>('roi_pct');
  const [sortAsc, setSortAsc] = useState(false);

  const sorted = useMemo(() => {
    const list = [...(lineages ?? [])];
    list.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (typeof av === 'number' && typeof bv === 'number') return sortAsc ? av - bv : bv - av;
      return sortAsc
        ? String(av ?? '').localeCompare(String(bv ?? ''))
        : String(bv ?? '').localeCompare(String(av ?? ''));
    });
    return list;
  }, [lineages, sortKey, sortAsc]);

  const handleSort = (key: SortKey) => {
    if (key === sortKey) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(false); }
  };

  return (
    <SectionPanel
      title="Lineage Board"
      count={sorted.length || undefined}
      collapsible
    >
      {sorted.length === 0 ? (
        <div className="sp__empty">No lineages</div>
      ) : (
        <div className="lnb__scroll">
          <table className="lnb__table">
            <thead>
              <tr>
                {COLUMNS.map((c) => (
                  <th
                    key={c.key}
                    className={sortKey === c.key ? 'lnb__th--active' : ''}
                    onClick={() => handleSort(c.key)}
                  >
                    {c.label}
                    {sortKey === c.key && <span className="lnb__arrow">{sortAsc ? '▲' : '▼'}</span>}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.slice(0, 20).map((l) => (
                <tr key={l.lineage_id}>
                  <td className="lnb__id">{l.lineage_id.split('/').pop()}</td>
                  <td>{l.family_id}</td>
                  <td className="lnb__role">{l.role}</td>
                  <td className="lnb__stage">{l.current_stage}</td>
                  <td
                    className="lnb__roi"
                    style={{ color: (l.monthly_roi_pct ?? l.roi_pct ?? 0) >= 0 ? 'var(--ok)' : 'var(--crit)' }}
                  >
                    {formatPct(l.monthly_roi_pct ?? l.roi_pct ?? 0)}
                  </td>
                  <td>{l.trade_count}</td>
                  <td>{l.paper_days}</td>
                  <td className="lnb__lane">{l.runtime_lane_kind || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </SectionPanel>
  );
}
