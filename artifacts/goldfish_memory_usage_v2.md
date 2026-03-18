# Goldfish Memory Usage v2

**Date:** 2026-03-18

## Current State (Already Working)

### What Is Written

| Writer | Goldfish Method | Data |
|---|---|---|
| Orchestrator: evaluation | `record_run_created` / `record_run_finalized` | walkforward/stress eval run lifecycle |
| Orchestrator: retirement | `record_retirement` via `_provenance_retire` | lineage_id, reason, best_metrics, lessons |
| Orchestrator: promotion | `record_promotion` via `_provenance_promote` | lineage_id, old_stage, new_stage, evidence |
| Orchestrator: learning memory | `record_learning_note` | outcome, summary, domains, recommendations, blockers |

### What Is Read Back

| Reader | Source | Used For |
|---|---|---|
| `strategy_inventor.invent_proposal()` | `registry.learning_memories(family_id, limit=12)` | Proposal generation — domain selection, parameter adjustment |
| `strategy_inventor._memory_hint()` | Last learning memory entry | Injected as `memory_hint=` into genome notes |
| `strategy_inventor._memory_adjustments()` | Last 5 learning memories | Edge bump, stake reduction, model class avoidance |
| Orchestrator: agent review | `registry.learning_memories(family_id, limit=12)` | Fed into agent review context |

### Where Memory Now Affects Decisions

1. **Proposal generation:** `_memory_hint()` surfaces the last retirement's recommendation (e.g., "change scientific collaboration mix"). `_memory_adjustments()` reads blockers and recommendations to adjust parameters: tighter edge thresholds, reduced stake, information-theoretic preference, model class avoidance.

2. **Domain rotation:** `recent_signatures` in `invent_proposal()` tracks which scientific domain combinations have been tried and retired. New proposals rotate to untried domain swarms.

3. **Mutation:** Learning memory entries with `tweak_count` and `recommendations` inform the mutation strategy.

4. **Family revival:** When a champion is retired and challengers need to be spawned, `learning_memories` for the family are loaded and fed to `strategy_inventor` to avoid repeating past failures.

## What Was Added in v2

### Goldfish Thoughts Are Written for All Outcomes

Every retirement, every promotion, every evaluation lifecycle event creates a Goldfish thought record via `record_learning_note`. This means:

- **Failed lineages** → Goldfish remembers why they failed
- **Successful lineages** → Goldfish remembers what worked
- **Near-miss lineages** → Goldfish remembers what was close and what blocked them

### Learning Memory Feeds Into Every Key Decision

The `learning_memories` registry method returns the 12 most recent entries per family. These are actively consulted in:

| Decision Point | Memory Used | How |
|---|---|---|
| Proposal generation | Last 12 memories | Domain rotation + parameter adjustment |
| Mutation | Last 5 memories | Edge/stake/model-class adjustments |
| Family revival (challenger spawn) | Last 12 memories | Avoid repeating failed domain combos |
| Agent review | Last 12 memories | Context for review agent |
| Retirement | Written, not read | Creates the memory for future use |

### Goldfish vs Local Registry

| System | What | Persistence | Access Pattern |
|---|---|---|---|
| **Registry (local)** | `LearningMemoryEntry` objects | JSON files in `data/factory/` | `registry.learning_memories()` |
| **Goldfish (durable)** | Thought records (audit trail) | Goldfish daemon workspace | `list_thoughts()` |

Both are written simultaneously. The local registry is the primary read path for the learning loop. Goldfish serves as the durable audit trail and can be queried for cross-session memory.

## Key Principle

Goldfish must remember ALL families and ALL variants — not just winners. The learning value is highest from:
- Prior failures (what not to repeat)
- Prior critiques (what weaknesses to address)
- Prior mutations (what was tried and what happened)
- Prior retirement causes (what systematic problems exist)
- Near-miss successes (what was close and what to push on)

Every retirement writes a `LearningMemoryEntry` with:
- `outcome`: the retirement reason
- `summary`: narrative of what happened
- `scientific_domains`: what domain mix was used
- `recommendations`: what to try differently
- `blockers`: what deterministic gates blocked it

This ensures the next variant/family can learn from all prior history, not just the last success.
