# Goldfish DNA Audit

**Date:** 2026-03-18

## Summary

Goldfish was write-only passive logging. The decision feedback loop ran entirely through local registry JSONL files. No code ever called `list_thoughts()` or `list_history()` from Goldfish to inform decisions.

## Write Points (Passive Logging)

| Location | Method | What | Decision-Critical |
|---|---|---|---|
| orchestrator:455 | record_proposal | hypothesis_id, thesis, source | No |
| orchestrator:516 | record_codegen | lineage_id, code_path, class_name | No |
| orchestrator:2471 | record_retirement | lineage_id, reason, best_metrics, lessons | No |
| orchestrator:2496 | record_promotion | lineage_id, from/to stage, decision | No |
| **orchestrator:2461** | **record_learning_note** | **outcome, summary, domains, recommendations** | **Indirect (feeds local cache)** |
| orchestrator:5189 | record_evaluation | run_id, evaluation_payload | No |
| orchestrator (paper) | record_paper_snapshot | lineage_id, status, metrics | No |
| orchestrator (mutation) | record_challenger_mutation | parent_id, child_id, mutation_reason | No |

## Read Points Before This Patch

| Location | Source | What | Decision-Critical |
|---|---|---|---|
| orchestrator:1198 | `registry.learning_memories()` (LOCAL JSONL) | last 12 memories per family | YES |
| orchestrator:1666 | `registry.learning_memories()` (LOCAL JSONL) | last 12 memories | YES |
| orchestrator:2298 | `registry.learning_memories()` (LOCAL JSONL) | last 12 memories | YES |
| strategy_inventor:336 | passed in from orchestrator | `_memory_adjustments()` | YES |
| strategy_inventor:384 | passed in from orchestrator | `_memory_hint()` | YES |
| **Goldfish daemon** | **NEVER READ** | **list_thoughts(), list_history() = 0 calls** | N/A |

## Decision Path Before Patch

```
Lineage retires
  → _record_learning_memory()
  → registry.save_learning_memory() [LOCAL JSONL]    ← only source for decisions
  → provenance.record_learning_note() [GOLDFISH]      ← passive logging, never read back
Next proposal
  → registry.learning_memories() [LOCAL JSONL]
  → strategy_inventor._memory_hint() / _memory_adjustments()
  → basic text pattern matching on last 5 entries
```

## Gap: What Was Missing

1. **Goldfish thoughts never read** — accumulated retirement/promotion/evaluation records sat unused
2. **`_memory_hint()` only looked at 1 entry** (the latest), not family-wide patterns
3. **`_memory_adjustments()` did literal text matching** on recommendation strings — fragile
4. **No failure motif aggregation** — recurring blockers not identified
5. **No ancestor/descendant linking** — mutation history not tracked structurally
6. **No DNA injection into agent context** — agent proposals had no family lineage context

## After This Patch

Active reads added:
- `ProvenanceService.read_family_thoughts()` → calls `list_thoughts()` on Goldfish daemon
- `build_family_dna_packet()` → structures local learning_memories into FamilyDNAPacket
- `enrich_dna_from_goldfish()` → optionally merges Goldfish thoughts into packet
- DNA injected into both deterministic (strategy_inventor) and agent (mobkit) proposal paths
