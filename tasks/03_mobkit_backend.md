# Task 03 — mobkit Backend Integration

## Objective

Implement the canonical mobkit orchestration backend behind the runtime adapter and enable at least one real workflow through it behind feature flags.

This task introduces the new runtime path but does not make it the default yet.

---

## Why this task exists

The factory needs explicit multi-agent orchestration:
- role-based coordination
- member isolation
- shared context
- structured final synthesis
- explicit failure handling
- runtime-level cancellation and healthcheck

This should live in mobkit, not in prompt choreography or direct provider calls.

---

## Allowed files

- `factory/runtime/`
- `factory/orchestrator.py`
- config / settings files
- tests for runtime backends
- minimal support files required for schemas / contracts

Do not expand into unrelated provenance or dashboard work unless absolutely necessary.

---

## Forbidden in this task

- do not change default backend to mobkit yet
- do not remove legacy runtime
- do not implement full observability stack yet
- do not bypass the runtime adapter
- do not hardcode unverified mobkit APIs; inspect the real repo first

---

## Required deliverables

## 1. mobkit backend implementation
Add `MobkitOrchestratorBackend` (name may vary) implementing the local `OrchestratorBackend` interface.

It must support at least:
- backend healthcheck
- one structured workflow
- one multi-member workflow
- explicit failure surface
- explicit fallback reason

## 2. Runtime integration
Add `MobkitRuntime` or equivalent runtime implementation that uses the backend for factory task methods.

## 3. Workflow profiles
Implement named workflow profiles for at least:
- proposal generation or critique
- one additional structured task

Each profile must define:
- roles
- models / tiers
- schema
- tool scope
- retries
- timeout

## 4. Member traces
Return member-level metadata in the runtime envelope when available.

---

## Suggested implementation steps

1. inspect the real `lukacf/meerkat-mobkit` repo
2. determine how it expects workflows / mobs / runtime sessions to be declared
3. implement a repo-local backend adapter
4. map one business task to a multi-member mob workflow
5. map one business task to a structured single-task path
6. wire the new runtime into the runtime manager
7. keep it disabled by default
8. add tests and small smoke path

---

## Minimum tests required

- backend healthcheck behavior
- runtime manager can select mobkit backend when enabled
- at least one workflow returns schema-valid payload
- failure to initialize backend produces explicit error or fallback reason
- member traces are present or explicitly represented as unavailable

---

## Acceptance criteria

- mobkit backend exists and can be selected
- at least one real workflow uses it
- default backend remains legacy
- fallback behavior is explicit
- tests pass

---

## Suggested verification commands

```bash
pytest -q tests -k mobkit
pytest -q tests -k runtime
```

If feasible, run a single lightweight workflow smoke command using test config.

---

## Rollback plan

- backend selection remains config-driven
- legacy path remains default
- failing mobkit backend can be fully bypassed without code edits

---

## Completion summary format

Return:
1. files changed
2. which workflows now use mobkit
3. how fallback behaves
4. tests run and results
5. risks for Task 04
