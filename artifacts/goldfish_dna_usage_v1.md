# Goldfish DNA Usage v1

**Date:** 2026-03-18

## What Is Retrieved

A `FamilyDNAPacket` is built from local `registry.learning_memories(family_id, limit=20)` with optional enrichment from `ProvenanceService.read_family_thoughts(family_id, limit=30)`.

The packet is built once per family per `_seed_challengers()` call (i.e., once per cycle per active family).

## Where It Is Injected

### 1. Deterministic proposal path (strategy_inventor.generate_proposal)

- `_memory_hint()` now returns: last entry recommendation + `dna_top_failure=X` + `dna_best_roi=Y%`
- DNA text appended to `agent_notes` in `ScientificAgentProposal`
- This affects: proposal `thesis`, domain selection rotation, parameter adjustments

**Code path:**
```
orchestrator._seed_challengers()
  → build_family_dna_packet()
  → strategy_inventor.generate_proposal(dna_packet=packet)
    → _memory_hint(dna_packet=packet)   # enriched hint
    → notes.append(dna_packet.as_prompt_text())  # full DNA injected
```

### 2. Agent (mobkit) proposal path (MobkitOrchestratorBackend.generate_proposal)

- `dna_summary` string passed to context dict as `lineage_dna`
- Agent sees it in `shared_context` alongside `recent_learning`, `thesis`, etc.
- Agent can use family failure history when generating new hypotheses

**Code path:**
```
orchestrator._seed_challengers()
  → build_family_dna_packet()
  → agent_runtime.generate_proposal(dna_summary=packet.as_prompt_text())
    → shared_context["lineage_dna"] = dna_summary  # agent sees it
```

### 3. Goldfish enrichment (ProvenanceService.read_family_thoughts)

- Called once per family per cycle when Goldfish daemon is available
- Extracts additional retirement reasons and hard veto causes from thought records
- Falls back gracefully to empty list if daemon unavailable

## How It Changes Behavior

### Proposal generation (before vs after)

**Before:**
- Only looked at last 1 learning memory entry for hint
- `_memory_adjustments` did text pattern matching on recommendation strings
- No family-wide failure pattern awareness
- Agent saw only `recent_learning: [last 3 summaries]`

**After:**
- `_memory_hint()` surfaces top failure motif + best known ROI from full family history
- `generate_proposal()` notes include compact DNA text with failure/success patterns
- Agent sees `lineage_dna` in context — knows what has been tried and failed
- Domain rotation avoids recently failed domain combinations

### Revival logic

The revival script (`revive_paper_candidates.py`) already reads the local registry. Future revivals can call `build_family_dna_packet()` to surface the best retired ancestor and avoid repeating its known failure patterns.

### Retirement reasoning

`_record_learning_memory()` writes structured `LearningMemoryEntry` to registry and `record_learning_note()` to Goldfish. DNA extractor reads these back — creating a feedback loop: retire → record → DNA reads → next proposal inherits lesson.

## Bounds

| Limit | Value | Rationale |
|---|---|---|
| Best ancestors | 5 | Avoid over-indexing on one good result |
| Worst relatives | 5 | Surfacing failure diversity |
| Failure motifs | 5 | Top recurring blockers only |
| Success motifs | 5 | Top domain patterns only |
| Mutation deltas | 3 | Last 3 steps are most relevant |
| Goldfish thoughts | 30 | Bounded read, graceful degradation |
