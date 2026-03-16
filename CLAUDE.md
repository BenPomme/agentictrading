# CLAUDE.md
## Project memory and execution rules for Claude Code

This repo is undergoing a staged architectural refactor.

### Target architecture
- **meerkat-mobkit** = canonical runtime orchestrator backend
- **Meerkat** = agent harness / structured output / tool / sub-agent layer
- **Goldfish** = experiment lineage / provenance / durable research memory
- **AgenticTrading** = domain control plane and policy layer

The work must be performed in bounded phases, not as a single sweeping rewrite.

---

## Mandatory reading order

Before changing code, read:

1. `docs/refactor/CODEX_REFRACTOR_OVERVIEW.md`
2. `docs/refactor/IMPLEMENTATION_PLAN_DETAILED.md`
3. `docs/refactor/COST_CONTROL_AND_MULTIAGENT_POLICY.md`
4. `docs/refactor/ACCEPTANCE_TESTS_AND_DOD.md`
5. the specific task file in `tasks/` that is being implemented

Then inspect the real repo code and the external integration repos before editing.

---

## Execution model

### Work one task at a time
Only implement the currently assigned `tasks/NN_*.md` file.

### Preserve migration safety
Until the cutover task:
- preserve legacy runtime path
- preserve local projection cache
- keep new behavior behind flags

### Keep boundaries clean
Business logic must depend on local interfaces, not directly on raw external SDK APIs.

### Do not fake integrations
If mobkit, Meerkat, or Goldfish APIs differ from the planning docs:
- adapt the adapter implementation
- keep the boundary stable
- state the mismatch explicitly in your summary

---

## Repo-specific priorities

### 1. Runtime safety
Do not leave hidden direct provider calls in business logic after introducing the runtime boundary.

### 2. Provenance correctness
Do not treat JSONL or local history files as the long-term system of record after Goldfish integration.

### 3. Cost discipline
Budget ceilings, downgrade behavior, and fallback policy are core features, not optional polish.

### 4. Observability
A full cycle must be reconstructable from emitted metadata and logs.

---

## Preferred implementation style

- additive before destructive
- typed contracts before broad rewiring
- clear adapters before deep cutover
- narrow PR-sized edits
- tests with every task
- explicit fallback behavior

Use existing project conventions where possible. If conventions are weak, prefer clear typed Python over clever abstractions.

---

## What not to do

- do not attempt the entire 13-file plan in one response or one patch
- do not rename large parts of the codebase without necessity
- do not broaden task scope without saying so
- do not bury policy logic inside prompts
- do not silently ignore provenance failures
- do not introduce unbounded recursion or fanout in multi-agent workflows

---

## External dependency rule

You must inspect the actual external repos:
- `lukacf/meerkat-mobkit`
- `lukacf/meerkat`
- `lukacf/goldfish`

If their real interfaces differ from assumptions in the docs, preserve the architecture and adapt only the adapters.

---

## Test rule

For every task:
- add or update tests
- run the relevant test commands
- report exact results
- include degraded/fallback-path assertions where relevant

If a test cannot be run, state why and provide the exact command attempted.

---

## Required completion summary after each task

Return:
1. files changed
2. behavior implemented
3. tests run and results
4. assumptions or API mismatches discovered
5. risks / next task dependencies
6. rollback status

---

## Special note on autonomy

The goal is not maximum activity.
The goal is controlled, traceable, cost-bounded autonomous learning.

When forced to choose:
- prefer simpler orchestration with stronger observability
- prefer cheaper reviewer members over expensive parallelism
- prefer early retirement of weak lineages over repeated expensive rescue attempts
- prefer explicit failure over fake success
