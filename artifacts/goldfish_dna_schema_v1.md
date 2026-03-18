# Goldfish DNA Schema v1

**Date:** 2026-03-18

## FamilyDNAPacket Structure

```python
@dataclass
class FamilyDNAPacket:
    family_id: str                        # which family this belongs to
    total_lineages_seen: int              # total retirement records in history
    best_ancestors: List[AncestorSummary] # top 5 by ROI
    worst_relatives: List[AncestorSummary] # bottom 5 by ROI
    failure_motifs: List[str]             # top 5 recurring blockers across all retirements
    success_motifs: List[str]             # top 5 domain patterns from non-failed or best-ROI lineages
    last_mutations: List[MutationDelta]   # last 3 inferred mutation steps with outcomes
    hard_veto_causes: List[str]           # deterministic hard-stop conditions that fired
    retirement_reasons: List[str]         # top 5 semantic retirement outcomes
```

## AncestorSummary

```python
@dataclass
class AncestorSummary:
    lineage_id: str
    roi: float        # monthly_roi_pct from best evaluation
    trades: int       # trade_count
    domains: List[str]  # scientific_domains used
    outcome: str      # retired_no_edge, retired_stalled, ...
    tweak_count: int  # mutations attempted before retirement
```

## MutationDelta

```python
@dataclass
class MutationDelta:
    parent_lineage_id: str
    child_lineage_id: str
    domains_changed: List[str]  # new domains in child vs parent
    outcome: str                # "improved" | "degraded" | "retired"
    roi_delta: float            # child_roi - parent_roi
```

## Compact Prompt Representation (as_prompt_text)

```
family_dna(family=liquidation_rebound_absorption, seen=4):
  failure_motifs: max_tweaks_exhausted_underperforming, backtest_ttl_67h_exceeded_48h
  success_patterns: econometrics, microstructure, information_theory
  retirement_reasons: max_tweaks_exhausted_underperforming
  best_ancestor: lineage=liquidation_rebound_absorption:champion roi=292.8% trades=415 domains=econometrics,microstructure
  worst_relative: lineage=liquidation_rebound_absorption:challenger:1 roi=0.0% outcome=retired_...
  last_mutation: liquidation_rebound_absorption:champion->liquidation_rebound_absorption:challenger:1 outcome=degraded roi_delta=-292.8%
```

## Source of Truth

| Field | Source | Fallback |
|---|---|---|
| best_ancestors | local registry learning_memories | empty |
| worst_relatives | local registry learning_memories | empty |
| failure_motifs | Counter of all blockers in retirements | empty |
| success_motifs | domains of non-retired OR best-ROI lineages | empty |
| last_mutations | consecutive LearningMemoryEntry pairs | empty |
| hard_veto_causes | blockers containing "veto" or "hard_block" | enriched from Goldfish thoughts |
| retirement_reasons | Counter of outcome fields in retirements | enriched from Goldfish thoughts |
