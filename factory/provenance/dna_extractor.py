"""FamilyDNAPacket — compact lineage memory for active retrieval.

Distils LearningMemoryEntry history into a structured packet that
proposal generation, mutation, revival, and retirement reasoning
can use directly.

Design:
- Primary source: local registry learning_memories (always available)
- Optional enrichment: Goldfish thoughts (if daemon is up)
- Returns FamilyDNAPacket — a bounded, human-readable summary
- Graceful degradation: returns an empty packet if data is missing

Key constraints:
- Best 5 related ancestors (highest ROI)
- Worst 5 failed relatives (lowest ROI / most retirements)
- Top 5 recurring failure motifs
- Top 5 recurring success indicators
- Last 3 mutation deltas with outcomes
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from factory.contracts import LearningMemoryEntry


@dataclass
class AncestorSummary:
    lineage_id: str
    roi: float
    trades: int
    domains: List[str]
    outcome: str
    tweak_count: int


@dataclass
class MutationDelta:
    parent_lineage_id: str
    child_lineage_id: str
    domains_changed: List[str]
    outcome: str  # "improved" | "degraded" | "retired"
    roi_delta: float


@dataclass
class FamilyDNAPacket:
    """Compact lineage memory for a single family."""
    family_id: str
    total_lineages_seen: int = 0
    best_ancestors: List[AncestorSummary] = field(default_factory=list)
    worst_relatives: List[AncestorSummary] = field(default_factory=list)
    failure_motifs: List[str] = field(default_factory=list)       # top recurring blockers
    success_motifs: List[str] = field(default_factory=list)        # domains/patterns in non-failed
    last_mutations: List[MutationDelta] = field(default_factory=list)
    hard_veto_causes: List[str] = field(default_factory=list)
    retirement_reasons: List[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return self.total_lineages_seen == 0

    def as_prompt_text(self) -> str:
        """Return a compact, human-readable DNA summary for injection into agent notes."""
        if self.is_empty():
            return ""
        lines = [f"family_dna(family={self.family_id}, seen={self.total_lineages_seen}):"]
        if self.failure_motifs:
            lines.append(f"  failure_motifs: {', '.join(self.failure_motifs[:5])}")
        if self.success_motifs:
            lines.append(f"  success_patterns: {', '.join(self.success_motifs[:5])}")
        if self.retirement_reasons:
            lines.append(f"  retirement_reasons: {', '.join(self.retirement_reasons[:5])}")
        if self.hard_veto_causes:
            lines.append(f"  hard_vetoes: {', '.join(self.hard_veto_causes[:3])}")
        if self.best_ancestors:
            best = self.best_ancestors[0]
            lines.append(
                f"  best_ancestor: lineage={best.lineage_id} roi={best.roi:.1f}% "
                f"trades={best.trades} domains={','.join(best.domains)}"
            )
        if self.worst_relatives:
            worst = self.worst_relatives[0]
            lines.append(
                f"  worst_relative: lineage={worst.lineage_id} roi={worst.roi:.1f}% "
                f"outcome={worst.outcome}"
            )
        if self.last_mutations:
            mut = self.last_mutations[-1]
            lines.append(
                f"  last_mutation: {mut.parent_lineage_id}->{mut.child_lineage_id} "
                f"outcome={mut.outcome} roi_delta={mut.roi_delta:+.1f}%"
            )
        return "\n".join(lines)

    def dominant_failure_pattern(self) -> Optional[str]:
        return self.failure_motifs[0] if self.failure_motifs else None

    def best_known_roi(self) -> float:
        if not self.best_ancestors:
            return 0.0
        return self.best_ancestors[0].roi


def build_family_dna_packet(
    family_id: str,
    learning_memories: Sequence[LearningMemoryEntry],
    *,
    max_ancestors: int = 5,
    max_relatives: int = 5,
    max_mutations: int = 3,
) -> FamilyDNAPacket:
    """Build a FamilyDNAPacket from local learning_memories.

    This is the primary path — no Goldfish daemon required.
    Call enrich_dna_from_goldfish() separately to add Goldfish-sourced data.
    """
    entries = [m for m in learning_memories if m.family_id == family_id]
    if not entries:
        return FamilyDNAPacket(family_id=family_id)

    # --- Ancestor summaries ---
    summaries: List[AncestorSummary] = []
    for m in entries:
        roi = float((m.metrics or {}).get("monthly_roi_pct", 0) or 0)
        trades = int((m.metrics or {}).get("trade_count", 0) or 0)
        summaries.append(AncestorSummary(
            lineage_id=m.lineage_id,
            roi=roi,
            trades=trades,
            domains=list(m.scientific_domains or []),
            outcome=m.outcome,
            tweak_count=m.tweak_count or 0,
        ))

    best_ancestors = sorted(summaries, key=lambda s: s.roi, reverse=True)[:max_ancestors]
    worst_relatives = sorted(summaries, key=lambda s: s.roi)[:max_relatives]

    # --- Failure motifs (recurring blockers across all retirements) ---
    all_blockers: List[str] = []
    for m in entries:
        if m.outcome.startswith("retired"):
            all_blockers.extend(m.blockers or [])
    failure_motif_counts = Counter(all_blockers)
    failure_motifs = [b for b, _ in failure_motif_counts.most_common(5)]

    # --- Hard veto causes (deterministic hard blocks) ---
    hard_veto_causes: List[str] = []
    for m in entries:
        for blocker in (m.blockers or []):
            if any(kw in blocker.lower() for kw in ["veto", "hard_block", "negative_slippage"]):
                if blocker not in hard_veto_causes:
                    hard_veto_causes.append(blocker)

    # --- Success motifs (domain patterns in non-failed lineages, if any) ---
    success_domains: List[str] = []
    for m in entries:
        if not m.outcome.startswith("retired"):
            success_domains.extend(m.scientific_domains or [])
    if not success_domains:
        # Fall back: domains of the highest-ROI ancestors
        for anc in best_ancestors[:2]:
            success_domains.extend(anc.domains)
    success_domain_counts = Counter(success_domains)
    success_motifs = [d for d, _ in success_domain_counts.most_common(5)]

    # --- Retirement reasons (semantic outcomes) ---
    retirement_reason_counts = Counter(
        m.outcome for m in entries if m.outcome.startswith("retired")
    )
    retirement_reasons = [r for r, _ in retirement_reason_counts.most_common(5)]

    # --- Last mutation deltas (infer from consecutive entries on same family) ---
    mutations: List[MutationDelta] = []
    sorted_entries = sorted(entries, key=lambda m: m.created_at)
    for i in range(1, len(sorted_entries)):
        prev = sorted_entries[i - 1]
        curr = sorted_entries[i]
        prev_roi = float((prev.metrics or {}).get("monthly_roi_pct", 0) or 0)
        curr_roi = float((curr.metrics or {}).get("monthly_roi_pct", 0) or 0)
        changed_domains = [
            d for d in curr.scientific_domains
            if d not in prev.scientific_domains
        ]
        if changed_domains or abs(curr_roi - prev_roi) > 0.5:
            outcome = "improved" if curr_roi > prev_roi else "degraded"
            if curr.outcome.startswith("retired"):
                outcome = "retired"
            mutations.append(MutationDelta(
                parent_lineage_id=prev.lineage_id,
                child_lineage_id=curr.lineage_id,
                domains_changed=changed_domains,
                outcome=outcome,
                roi_delta=curr_roi - prev_roi,
            ))
    last_mutations = mutations[-max_mutations:] if mutations else []

    return FamilyDNAPacket(
        family_id=family_id,
        total_lineages_seen=len(entries),
        best_ancestors=best_ancestors,
        worst_relatives=worst_relatives,
        failure_motifs=failure_motifs,
        success_motifs=success_motifs,
        last_mutations=last_mutations,
        hard_veto_causes=hard_veto_causes[:5],
        retirement_reasons=retirement_reasons,
    )


def enrich_dna_from_goldfish(
    packet: FamilyDNAPacket,
    goldfish_thoughts: List[Dict[str, Any]],
) -> FamilyDNAPacket:
    """Optionally enrich a DNA packet with Goldfish thought records.

    Goldfish thoughts contain structured provenance that may not be in
    the local registry (e.g., cross-session history, challenger mutations).

    Call this only when Goldfish daemon is available. Fails gracefully —
    any exception returns the original packet unchanged.
    """
    try:
        extra_failures: List[str] = []
        extra_retirements: List[str] = []
        for thought in goldfish_thoughts:
            content = str(thought.get("thought", "") or thought.get("content", ""))
            if "RETIREMENT" in content or "retired" in content.lower():
                # Extract reason tag if present
                for part in content.split("|"):
                    part = part.strip()
                    if part.startswith("reason="):
                        reason = part.replace("reason=", "").strip()
                        if reason and reason not in extra_retirements:
                            extra_retirements.append(reason)
            if "hard_veto" in content.lower() or "HARD_VETO" in content:
                for part in content.split("|"):
                    part = part.strip()
                    if "veto" in part.lower() and part not in extra_failures:
                        extra_failures.append(part)

        # Merge without duplicating existing entries
        merged_retirements = list(dict.fromkeys(
            packet.retirement_reasons + [r for r in extra_retirements if r not in packet.retirement_reasons]
        ))[:5]
        merged_vetoes = list(dict.fromkeys(
            packet.hard_veto_causes + [f for f in extra_failures if f not in packet.hard_veto_causes]
        ))[:5]

        return FamilyDNAPacket(
            family_id=packet.family_id,
            total_lineages_seen=packet.total_lineages_seen,
            best_ancestors=packet.best_ancestors,
            worst_relatives=packet.worst_relatives,
            failure_motifs=packet.failure_motifs,
            success_motifs=packet.success_motifs,
            last_mutations=packet.last_mutations,
            hard_veto_causes=merged_vetoes,
            retirement_reasons=merged_retirements,
        )
    except Exception:
        return packet  # always fall back to unmodified packet
