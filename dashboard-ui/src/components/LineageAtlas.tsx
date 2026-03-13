import { useState, useMemo, useCallback } from 'react';
import type {
  LineageAtlas,
  LineageAtlasFamily,
  LineageAtlasNode,
  LineageAtlasEvent,
} from '../types/snapshot';
import { relativeTime, formatPct } from '../utils/format';
import './LineageAtlas.css';

interface LineageAtlasProps {
  atlas: LineageAtlas | undefined;
}

function truncateId(id: string): string {
  const parts = id.split(/[-_/]/);
  return parts.length > 1 ? parts[parts.length - 1] : id.slice(-8);
}

function roleColor(role: string): string {
  switch (role) {
    case 'champion':
      return 'var(--warn)';
    case 'challenger':
      return 'var(--info)';
    case 'incubating':
      return 'var(--accent)';
    default:
      return 'var(--text-muted)';
  }
}

function roleBg(role: string): string {
  switch (role) {
    case 'champion':
      return 'var(--warn-dim)';
    case 'challenger':
      return 'var(--info-dim)';
    case 'incubating':
      return 'var(--accent-dim)';
    default:
      return 'var(--surface-alt)';
  }
}

function eventKindColor(kind: string): string {
  switch (kind) {
    case 'mutation':
      return 'var(--accent)';
    case 'evaluation':
      return 'var(--info)';
    case 'retirement':
      return 'var(--crit)';
    case 'promotion':
      return 'var(--warn)';
    default:
      return 'var(--text-muted)';
  }
}

function eventKindBg(kind: string): string {
  switch (kind) {
    case 'mutation':
      return 'var(--accent-dim)';
    case 'evaluation':
      return 'var(--info-dim)';
    case 'retirement':
      return 'var(--crit-dim)';
    case 'promotion':
      return 'var(--warn-dim)';
    default:
      return 'var(--surface-alt)';
  }
}

function roiColor(roi: number): string {
  if (roi > 0) return 'var(--ok)';
  if (roi < 0) return 'var(--crit)';
  return 'var(--text-muted)';
}

/* ─── Tree Node ─── */

interface TreeNodeProps {
  node: LineageAtlasNode;
  nodeMap: Map<string, LineageAtlasNode>;
  selectedId: string | null;
  onSelect: (id: string) => void;
  depth: number;
}

function TreeNode({ node, nodeMap, selectedId, onSelect, depth }: TreeNodeProps) {
  const isSelected = selectedId === node.lineage_id;
  const childIds = node.child_lineage_ids ?? node.children ?? [];
  const children = childIds
    .map((cid) => nodeMap.get(cid))
    .filter((n): n is LineageAtlasNode => !!n);

  return (
    <div className="la-tree__subtree" style={{ '--depth': depth } as React.CSSProperties}>
      <div
        className={`la-tree__node ${isSelected ? 'la-tree__node--selected' : ''}`}
        onClick={() => onSelect(node.lineage_id)}
      >
        <div className="la-tree__node-header">
          <span className="la-tree__node-id">{truncateId(node.lineage_id)}</span>
          <span
            className="la-tree__role-badge"
            style={{ color: roleColor(node.role), background: roleBg(node.role) }}
          >
            {node.role}
          </span>
        </div>
        <div className="la-tree__node-metrics">
          <span className="la-tree__stage-pill">{node.current_stage ?? node.stage ?? 'unknown'}</span>
          <span className="la-tree__roi" style={{ color: roiColor(node.monthly_roi_pct ?? node.roi_pct ?? 0) }}>
            {formatPct(node.monthly_roi_pct ?? node.roi_pct ?? 0)}
          </span>
          <span className="la-tree__trades">{node.trade_count} trades</span>
        </div>
      </div>
      {children.length > 0 && (
        <div className="la-tree__children">
          {children.map((child) => (
            <TreeNode
              key={child.lineage_id}
              node={child}
              nodeMap={nodeMap}
              selectedId={selectedId}
              onSelect={onSelect}
              depth={depth + 1}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/* ─── Inspector ─── */

function Inspector({ node }: { node: LineageAtlasNode | null }) {
  if (!node) {
    return (
      <div className="la-inspector la-inspector--empty">
        <div className="la-inspector__placeholder">
          <span className="la-inspector__placeholder-icon">⬡</span>
          <span>Select a lineage node</span>
        </div>
      </div>
    );
  }

  const fields: [string, React.ReactNode][] = [
    ['Lineage ID', <span className="la-inspector__mono">{node.lineage_id}</span>],
    [
      'Role',
      <span
        className="la-inspector__badge"
        style={{ color: roleColor(node.role), background: roleBg(node.role) }}
      >
        {node.role}
      </span>,
    ],
    ['Stage', <span className="la-inspector__stage">{node.current_stage ?? node.stage ?? 'unknown'}</span>],
    [
      'ROI',
      <span style={{ color: roiColor(node.monthly_roi_pct ?? node.roi_pct ?? 0), fontFamily: 'var(--font-mono)' }}>
        {formatPct(node.monthly_roi_pct ?? node.roi_pct ?? 0)}
      </span>,
    ],
    ['Trade Count', <span className="la-inspector__mono">{node.trade_count}</span>],
    [
      'Parent',
      (node.parent_lineage_id ?? node.parent_id) ? (
        <span className="la-inspector__mono">{truncateId(node.parent_lineage_id ?? node.parent_id!)}</span>
      ) : (
        <span className="la-inspector__null">root</span>
      ),
    ],
    [
      'Children',
      <span className="la-inspector__mono">
        {(() => {
          const ids = node.child_lineage_ids ?? node.children ?? [];
          return ids.length > 0 ? ids.map(truncateId).join(', ') : 'none';
        })()}
      </span>,
    ],
  ];

  return (
    <div className="la-inspector">
      <h4 className="la-inspector__title">Lineage Inspector</h4>
      <div className="la-inspector__fields">
        {fields.map(([label, value]) => (
          <div key={label as string} className="la-inspector__row">
            <span className="la-inspector__label">{label}</span>
            <span className="la-inspector__value">{value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ─── Evolution Ledger ─── */

function EvolutionLedger({ events }: { events: LineageAtlasEvent[] }) {
  if (events.length === 0) {
    return (
      <div className="la-ledger">
        <h4 className="la-ledger__title">Evolution Ledger</h4>
        <div className="la-ledger__empty">No events recorded</div>
      </div>
    );
  }

  return (
    <div className="la-ledger">
      <h4 className="la-ledger__title">Evolution Ledger</h4>
      <div className="la-ledger__scroll">
        <div className="la-ledger__timeline">
          {events.map((evt, i) => (
            <div key={`${evt.ts}-${evt.lineage_id}-${i}`} className="la-ledger__event">
              <div className="la-ledger__dot" style={{ background: eventKindColor(evt.kind) }} />
              <div className="la-ledger__event-body">
                <div className="la-ledger__event-header">
                  <span className="la-ledger__ts">{relativeTime(evt.ts)}</span>
                  <span
                    className="la-ledger__kind"
                    style={{
                      color: eventKindColor(evt.kind),
                      background: eventKindBg(evt.kind),
                    }}
                  >
                    {evt.kind}
                  </span>
                  <span className="la-ledger__lineage-id">{truncateId(evt.lineage_id)}</span>
                </div>
                <div className="la-ledger__detail">{evt.detail}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ─── Main Component ─── */

export default function LineageAtlasView({ atlas }: LineageAtlasProps) {
  const [selectedFamilyIdx, setSelectedFamilyIdx] = useState(0);
  const [selectedLineageId, setSelectedLineageId] = useState<string | null>(null);

  const families = atlas?.families ?? [];
  const family: LineageAtlasFamily | undefined = families[selectedFamilyIdx];

  const nodeMap = useMemo(() => {
    const map = new Map<string, LineageAtlasNode>();
    if (family) {
      for (const node of family.nodes) {
        map.set(node.lineage_id, node);
      }
    }
    return map;
  }, [family]);

  const rootNodes = useMemo(() => {
    if (!family) return [];
    return family.root_lineage_ids
      .map((id) => nodeMap.get(id))
      .filter((n): n is LineageAtlasNode => !!n);
  }, [family, nodeMap]);

  const selectedNode = selectedLineageId ? (nodeMap.get(selectedLineageId) ?? null) : null;

  const handleFamilySelect = useCallback(
    (idx: number) => {
      setSelectedFamilyIdx(idx);
      setSelectedLineageId(null);
    },
    [],
  );

  const handleNodeSelect = useCallback((id: string) => {
    setSelectedLineageId((prev) => (prev === id ? null : id));
  }, []);

  if (!atlas || families.length === 0) {
    return (
      <div className="la la--empty">
        <div className="la__empty-state">
          <span className="la__empty-icon">⬡</span>
          <span className="la__empty-label">No lineage atlas data available</span>
        </div>
      </div>
    );
  }

  return (
    <div className="la">
      {/* Family Tabs */}
      <div className="la__tabs">
        {families.map((fam, idx) => (
          <button
            key={fam.family_id}
            className={`la__tab ${idx === selectedFamilyIdx ? 'la__tab--active' : ''}`}
            onClick={() => handleFamilySelect(idx)}
          >
            <span className="la__tab-label">{fam.label}</span>
            <span className="la__tab-count">{fam.nodes.length}</span>
          </button>
        ))}
      </div>

      {/* Main Content: Tree + Inspector */}
      <div className="la__main">
        <div className="la__tree-panel">
          <div className="la__tree-scroll">
            {rootNodes.length > 0 ? (
              <div className="la-tree">
                {rootNodes.map((root) => (
                  <TreeNode
                    key={root.lineage_id}
                    node={root}
                    nodeMap={nodeMap}
                    selectedId={selectedLineageId}
                    onSelect={handleNodeSelect}
                    depth={0}
                  />
                ))}
              </div>
            ) : (
              <div className="la__tree-empty">No root nodes found</div>
            )}
          </div>
        </div>

        <div className="la__inspector-panel">
          <Inspector node={selectedNode} />
        </div>
      </div>

      {/* Evolution Ledger */}
      {family && <EvolutionLedger events={family.history} />}
    </div>
  );
}
