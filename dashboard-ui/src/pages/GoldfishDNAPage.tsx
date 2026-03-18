import SectionPanel from '../components/SectionPanel';
import type { DashboardSnapshot, SnapshotV2, DNAPacket } from '../types/snapshot';
import { relativeTime } from '../utils/format';
import './pages.css';

interface Props {
  snapshot: DashboardSnapshot | null;
  snapshotV2: SnapshotV2 | null;
}

// ── DNA packet card ──────────────────────────────────────────────────────────

function DNAPacketCard({ pkt }: { pkt: DNAPacket }) {
  const hasData = pkt.total_lineages_seen > 0;
  return (
    <div className="dna-packet-card">
      <div className="dna-packet-card__header">
        <span className="dna-packet-card__family">{pkt.family_id}</span>
        <span className="dna-packet-card__meta">
          {pkt.total_lineages_seen} lineage{pkt.total_lineages_seen !== 1 ? 's' : ''} seen
        </span>
      </div>

      {!hasData && (
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.68rem', color: 'var(--text-muted)' }}>
          No learning memories yet.
        </span>
      )}

      {hasData && (
        <>
          <div className="dna-packet-card__kpi-row">
            <div className="dna-kpi">
              <span className="dna-kpi__label">Best ROI</span>
              <span
                className={`dna-kpi__value${
                  pkt.best_known_roi != null && pkt.best_known_roi > 0
                    ? ' dna-kpi__value--ok'
                    : pkt.best_known_roi != null && pkt.best_known_roi < 0
                    ? ' dna-kpi__value--crit'
                    : ''
                }`}
              >
                {pkt.best_known_roi != null
                  ? `${pkt.best_known_roi.toFixed(1)}%`
                  : '—'}
              </span>
            </div>
            <div className="dna-kpi">
              <span className="dna-kpi__label">Motifs</span>
              <span className="dna-kpi__value">{pkt.failure_motifs.length}</span>
            </div>
            <div className="dna-kpi">
              <span className="dna-kpi__label">Vetoes</span>
              <span
                className={`dna-kpi__value${pkt.hard_veto_causes.length > 0 ? ' dna-kpi__value--warn' : ' dna-kpi__value--ok'}`}
              >
                {pkt.hard_veto_causes.length}
              </span>
            </div>
          </div>

          {/* Failure motifs */}
          {pkt.failure_motifs.length > 0 && (
            <div className="dna-motif-section">
              <span className="dna-motif-label">Failure motifs</span>
              <div className="dna-motif-list">
                {pkt.failure_motifs.slice(0, 6).map((m, i) => (
                  <span key={i} className="dna-motif-tag dna-motif-tag--fail">
                    {m}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Success motifs */}
          {pkt.success_motifs.length > 0 && (
            <div className="dna-motif-section">
              <span className="dna-motif-label">Success patterns</span>
              <div className="dna-motif-list">
                {pkt.success_motifs.slice(0, 6).map((m, i) => (
                  <span key={i} className="dna-motif-tag dna-motif-tag--success">
                    {m}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Hard vetoes */}
          {pkt.hard_veto_causes.length > 0 && (
            <div className="dna-motif-section">
              <span className="dna-motif-label">Hard vetoes</span>
              <div className="dna-motif-list">
                {pkt.hard_veto_causes.slice(0, 4).map((v, i) => (
                  <span key={i} className="dna-motif-tag dna-motif-tag--veto">
                    {v}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Best ancestor */}
          {pkt.best_ancestors.length > 0 && (
            <div>
              <span className="dna-motif-label">Best ancestor</span>
              {pkt.best_ancestors.slice(0, 1).map((a) => (
                <div key={a.lineage_id} className="dna-ancestor">
                  {a.lineage_id.length > 32 ? '…' + a.lineage_id.slice(-28) : a.lineage_id}
                  <span className="dna-ancestor__roi">+{a.roi.toFixed(1)}%</span>
                  {' '}· {a.trades} trades · {a.outcome}
                  {a.domains.length > 0 && (
                    <span style={{ color: 'var(--text-muted)', marginLeft: 4 }}>
                      [{a.domains.slice(0, 3).join(', ')}]
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export function GoldfishDNAPage({ snapshot, snapshotV2 }: Props) {
  const rs = snapshot?.factory?.research_summary;
  const gh = snapshotV2?.goldfish_health;
  const dnaPackets = snapshotV2?.dna_packets ?? [];

  const hasAnyDna = dnaPackets.some((p) => p.total_lineages_seen > 0);

  return (
    <div className="page">
      <div className="page__header">
        <h2 className="page__title">Goldfish DNA Intelligence</h2>
        <p className="page__subtitle">
          Provenance health, lineage memory, failure motifs, and success
          patterns
        </p>
      </div>

      {/* Goldfish health section */}
      {gh && (
        <SectionPanel
          title="Goldfish Provenance Health"
          tag={
            gh.enabled && gh.learning_files > 0
              ? 'active'
              : gh.enabled
              ? 'enabled, no writes'
              : 'disabled'
          }
          tagColor={
            gh.enabled && gh.learning_files > 0
              ? 'var(--ok)'
              : gh.enabled
              ? 'var(--warn)'
              : 'var(--text-muted)'
          }
        >
          <div className="goldfish-health-grid">
            <div className="goldfish-stat">
              <span className="goldfish-stat__label">Provenance</span>
              <span
                className={`goldfish-stat__value${gh.enabled ? ' goldfish-stat__value--ok' : ' goldfish-stat__value--warn'}`}
              >
                {gh.enabled ? 'enabled' : 'disabled'}
              </span>
            </div>
            <div className="goldfish-stat">
              <span className="goldfish-stat__label">Learning Files</span>
              <span
                className={`goldfish-stat__value${gh.learning_files > 0 ? ' goldfish-stat__value--ok' : ' goldfish-stat__value--muted'}`}
              >
                {gh.learning_files}
              </span>
            </div>
            <div className="goldfish-stat">
              <span className="goldfish-stat__label">Latest Write</span>
              <span
                className={`goldfish-stat__value${gh.latest_write ? ' goldfish-stat__value--ok' : ' goldfish-stat__value--muted'}`}
              >
                {gh.latest_write ? relativeTime(gh.latest_write) : '—'}
              </span>
            </div>
            <div className="goldfish-stat">
              <span className="goldfish-stat__label">Strict Mode</span>
              <span
                className={`goldfish-stat__value${gh.strict_mode ? '' : ' goldfish-stat__value--muted'}`}
              >
                {gh.strict_mode ? 'on' : 'off'}
              </span>
            </div>
            <div className="goldfish-stat">
              <span className="goldfish-stat__label">Workspace Root</span>
              <span
                className="goldfish-stat__value goldfish-stat__value--muted"
                title={gh.workspace_root}
                style={{ fontSize: '0.68rem', fontWeight: 400 }}
              >
                {gh.workspace_root.length > 20
                  ? '…' + gh.workspace_root.slice(-18)
                  : gh.workspace_root}
              </span>
            </div>
          </div>
          <p
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '0.65rem',
              color: 'var(--text-muted)',
              marginTop: 8,
            }}
          >
            {gh.note}
          </p>
        </SectionPanel>
      )}

      {/* Provenance metrics from research summary */}
      {rs && (
        <SectionPanel title="Provenance Metrics">
          <div className="dna-stat-grid">
            <div className="dna-stat">
              <span className="dna-stat__label">Learning Memories</span>
              <span className="dna-stat__value">{rs.learning_memory_count ?? '—'}</span>
            </div>
            <div className="dna-stat">
              <span className="dna-stat__label">Positive Models</span>
              <span className="dna-stat__value">{rs.positive_model_count ?? '—'}</span>
            </div>
            <div className="dna-stat">
              <span className="dna-stat__label">Research Positives</span>
              <span className="dna-stat__value">
                {rs.research_positive_model_count ?? '—'}
              </span>
            </div>
            <div className="dna-stat">
              <span className="dna-stat__label">Agent-Generated</span>
              <span className="dna-stat__value">
                {rs.agent_generated_lineage_count ?? '—'}
              </span>
            </div>
            <div className="dna-stat">
              <span className="dna-stat__label">Real-Agent Lineages</span>
              <span className="dna-stat__value">
                {rs.real_agent_lineage_count ?? '—'}
              </span>
            </div>
            <div className="dna-stat">
              <span className="dna-stat__label">Artifact-Backed</span>
              <span className="dna-stat__value">
                {rs.artifact_backed_lineage_count ?? '—'}
              </span>
            </div>
          </div>
        </SectionPanel>
      )}

      {/* DNA packets per family */}
      {dnaPackets.length > 0 ? (
        <SectionPanel
          title="Family DNA Packets"
          count={dnaPackets.length}
          tag={hasAnyDna ? 'memory active' : 'no data yet'}
          tagColor={hasAnyDna ? 'var(--ok)' : 'var(--text-muted)'}
        >
          <div className="dna-packet-grid">
            {dnaPackets.map((pkt) => (
              <DNAPacketCard key={pkt.family_id} pkt={pkt} />
            ))}
          </div>
        </SectionPanel>
      ) : (
        <div className="page__placeholder">
          <span className="page__placeholder-icon">∿</span>
          <span className="page__placeholder-title">No DNA packets yet</span>
          <span className="page__placeholder-desc">
            Family DNA packets are built from LearningMemoryEntry records in
            the registry. Run the factory for a few cycles to accumulate
            learning memories, then they will appear here.
          </span>
        </div>
      )}

      {/* DNA system architecture */}
      <SectionPanel title="DNA Architecture" collapsible defaultCollapsed>
        <div className="dna-roadmap">
          <div className="dna-roadmap__item">
            <strong>FamilyDNAPacket</strong> — aggregates failure_motifs,
            success_motifs, best_ancestors, mutation_deltas, hard_veto_causes
            per family
          </div>
          <div className="dna-roadmap__item">
            <strong>build_family_dna_packet(family_id, learning_memories)</strong>{' '}
            — reads local registry LearningMemoryEntry (up to 15 per family)
          </div>
          <div className="dna-roadmap__item">
            <strong>enrich_dna_from_goldfish(packet, thoughts)</strong> —
            optional enrichment from Goldfish daemon thought history
          </div>
          <div className="dna-roadmap__item">
            <strong>packet.as_prompt_text()</strong> — compact context injected
            into every agent proposal and mutation prompt via dna_summary param
          </div>
          <div className="dna-roadmap__item">
            <strong>ProvenanceService.read_family_thoughts(family_id)</strong>{' '}
            — reads Goldfish JSONL thoughts; enriches DNA with cross-lineage
            patterns
          </div>
        </div>

        <div
          className="budget-gap-note"
          style={{ marginTop: 12 }}
        >
          Planned: write health timeline, DNA packet inspector with full motif
          history, provenance audit trail viewer, memory influence heatmap
          (which DNA motifs correlated with positive outcomes).
        </div>
      </SectionPanel>
    </div>
  );
}
