# Goldfish DNA Examples v1

**Date:** 2026-03-18

## Example: liquidation_rebound_absorption Family

### Before (no DNA)

Proposal notes:
```
proposal_kind=mutation
lead_agent=Microstructure Analyst
collaborators=Econometrician
memory_hint=avoid repeating econometrics,microstructure,information_theory without a structural change
```

Agent context (mobkit):
```json
{
  "family_id": "liquidation_rebound_absorption",
  "thesis": "We believe we can create alpha by identifying forced-deleveraging episodes...",
  "recent_learning": [
    "liquidation_rebound_absorption:champion retired after 2 tweaks..."
  ]
}
```

### After (with DNA)

Proposal notes:
```
proposal_kind=mutation
lead_agent=Microstructure Analyst
collaborators=Econometrician
memory_hint=avoid repeating econometrics,microstructure,information_theory without a structural change | dna_top_failure=max_tweaks_exhausted_underperforming | dna_best_roi=292.8%
family_dna(family=liquidation_rebound_absorption, seen=4):
  failure_motifs: max_tweaks_exhausted_underperforming
  success_patterns: econometrics, microstructure, information_theory
  retirement_reasons: max_tweaks_exhausted_underperforming
  best_ancestor: lineage=liquidation_rebound_absorption:champion roi=292.8% trades=415 domains=econometrics,microstructure
  worst_relative: lineage=liquidation_rebound_absorption:challenger:1 roi=0.0% outcome=retired_...
  last_mutation: ...champion->...challenger:1 outcome=degraded roi_delta=-292.8%
```

Agent context (mobkit):
```json
{
  "family_id": "liquidation_rebound_absorption",
  "thesis": "We believe we can create alpha by identifying forced-deleveraging episodes...",
  "recent_learning": ["liquidation_rebound_absorption:champion retired after 2 tweaks..."],
  "lineage_dna": "family_dna(family=liquidation_rebound_absorption, seen=4):\n  failure_motifs: max_tweaks_exhausted_underperforming\n  best_ancestor: roi=292.8% trades=415\n  last_mutation: ...outcome=degraded roi_delta=-292.8%"
}
```

### What Changes

The agent now knows:
1. The family has 4 retired lineages — this is not a first attempt
2. The best-known ROI was 292.8% (likely inflated) with 415 trades
3. The dominant failure mode is `max_tweaks_exhausted_underperforming` — not data issues, not veto
4. The last mutation degraded ROI by 292.8% — suggesting over-mutation or wrong direction
5. Success patterns used `econometrics + microstructure` — the agent can confirm or challenge this

Instead of generating another variant with the same domain mix and same features, a well-directed agent should propose a structurally different approach — different horizon, different signal type, or different entry trigger.

## Example: funding_term_structure_dislocation Family

After running several cycles, the DNA for this family would look like:
```
family_dna(family=funding_term_structure_dislocation, seen=N):
  failure_motifs: walkforward_window_coverage_insufficient, stress_eval_negative
  success_patterns: econometrics, control_rl
  retirement_reasons: backtest_ttl_50h_exceeded_48h
  best_ancestor: lineage=funding_term_structure_dislocation:champion roi=5.8% trades=14
```

Key insight for agent: "This family was retired by TTL, not by negative evidence. The best-known ROI of 5.8% is modest but positive. The failure was infrastructure-level (TTL), not signal-level. Proposals should build on the existing 8h funding window hypothesis rather than abandoning it."

## Concrete Change in Parameter Adjustments

When `dna_top_failure = max_tweaks_exhausted_underperforming`:
- `_memory_adjustments()` sees "tighten edge thresholds" recommendation → `edge_bump += 0.01`
- Strategy inventor increases `selected_min_edge` by 0.01 in next proposal
- This prevents generating more cheap, low-edge variants that keep getting retired

When `dna_best_roi = 292.8%` but mutation is `degraded`:
- Memory hint surfaces "avoid repeating same domains without structural change"
- Strategy inventor rotates to an untried domain swarm for next challenger
