# AGENTS.md
## Implementation rules for Codex working in this repo

This repo is being migrated to a new architecture where:

- **meerkat-mobkit** is the canonical runtime orchestrator backend
- **Meerkat** is the agent harness layer
- **Goldfish** is the experiment lineage / provenance layer
- **AgenticTrading** remains the domain control plane

You are not allowed to “implement the whole refactor” in one pass.
You must work **task by task** using the files in `tasks/`.

---

## Read order before making changes

Read these files in order before editing code:

1. `docs/refactor/CODEX_REFRACTOR_OVERVIEW.md`
2. `docs/refactor/IMPLEMENTATION_PLAN_DETAILED.md`
3. `docs/refactor/COST_CONTROL_AND_MULTIAGENT_POLICY.md`
4. `docs/refactor/ACCEPTANCE_TESTS_AND_DOD.md`
5. the specific `tasks/NN_*.md` file you were asked to implement

Then inspect the relevant source files in the repo before changing anything.

---

## Working mode

### Required
- implement only the requested task
- keep changes bounded to the allowed file list in the task
- preserve backward compatibility until the cutover task
- add or update tests for every behavior change
- run the task’s required verification commands
- summarize risks, assumptions, and follow-ups

### Forbidden
- do not refactor unrelated modules opportunistically
- do not remove legacy behavior early
- do not invent external repo APIs
- do not silently bypass the runtime adapter boundary
- do not introduce hidden direct provider calls in business logic
- do not merge provenance writes into ad hoc local JSON only
- do not widen file scope unless the task explicitly requires it

---

## External repo usage rules

The implementation depends on:
- `lukacf/meerkat-mobkit`
- `lukacf/meerkat`
- `lukacf/goldfish`

You must inspect the real code in those repos before wiring integrations.

If the expected API differs from the plan:
- keep the architectural boundary the same
- adapt the adapter internals
- document the difference in your summary
- do not change the task scope without stating why

---

## Required architecture boundaries

## Business logic boundary
Factory business logic must depend on repo-local interfaces, not raw external APIs.

## Orchestration boundary
All orchestrated LLM work must go through the runtime adapter and orchestration backend boundary.

## Provenance boundary
Experiment lineage must go through the Goldfish provenance layer, not direct scattered writes.

## Governance boundary
Budget and downgrade logic must live in governance modules, not inline in orchestration code.

---

## Implementation discipline

### 1. Preserve a runnable repo
Each task must leave the repo in a state that can still run tests and smoke checks.

### 2. Prefer additive changes first
Before replacing behavior, add the interface, flag, wrapper, or adapter.

### 3. Gate all new behavior
Use config / feature flags so new behavior is not forced on by accident before cutover.

### 4. Keep contracts typed
Use dataclasses or typed models for cross-layer payloads. Avoid anonymous dict sprawl.

### 5. Make fallback explicit
If a new backend is unavailable, the code must:
- fail clearly, or
- fall back clearly if policy allows

Never silently swap behavior.

---

## Test expectations

Every task must include:
- unit tests for new contracts and policy
- integration or smoke coverage if the task touches external boundaries
- clear assertion of fallback / degraded behavior where relevant

Do not claim a task is done without tests or a documented reason a test could not be run.

---

## Logging and observability expectations

When adding new flows:
- attach correlation IDs
- log fallback reasons
- expose backend names
- expose budget decisions
- expose Goldfish record references where applicable

No new opaque background behavior.

---

## Commit / patch style

Prefer small, reviewable patches.
Keep function responsibilities narrow.
Avoid broad renames unless they are central to the task.
Add docstrings or comments only where they clarify boundary semantics.

---

## Required output after each task

After completing a task, provide:

1. files changed
2. behavior added or changed
3. tests run and results
4. assumptions about external repo APIs
5. risks or follow-up work
6. whether rollback behavior was preserved

---

## If blocked

If blocked by a real mismatch in external APIs:
- stop broad implementation
- implement the local boundary and tests
- leave a clearly marked adapter stub or TODO
- document exactly what must be inspected next

Do not “fake” an integration to make tests pass.
