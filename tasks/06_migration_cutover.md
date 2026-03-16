# Task 06 — Controlled Migration Cutover

## Objective

Flip the default factory path to:
- mobkit as default runtime orchestrator backend
- Goldfish as authoritative provenance backend

while retaining tested rollback switches.

---

## Why this task exists

Earlier tasks add the new path, but they do not make it the production-default path.
This task performs the controlled cutover only after the interfaces, policies, and observability are in place.

---

## Allowed files

- config / settings defaults
- `factory/runtime/runtime_manager.py`
- `factory/orchestrator.py`
- legacy runtime warnings / deprecation points
- operator docs or status text
- tests covering default behavior

Avoid broad cleanup beyond what is needed for the cutover.

---

## Forbidden in this task

- do not delete legacy runtime completely
- do not remove projection cache
- do not remove rollback switches
- do not cut over if smoke tests fail
- do not leave any hidden default direct runtime path

---

## Required deliverables

## 1. Default backend flip
Change default backend selection so supported environments use mobkit by default.

## 2. Provenance authority flip
Treat Goldfish as authoritative provenance store when enabled by default config.

## 3. Legacy path downgrade to fallback
Legacy runtime remains available only by explicit config or fallback policy.

## 4. Operator-visible cutover state
Status and logs must clearly show that the runtime backend is now mobkit by default.

## 5. Rollback instructions
Document and test config-based rollback path.

---

## Suggested implementation steps

1. confirm Tasks 01–05 test coverage is green
2. update config defaults
3. update runtime manager default selection
4. ensure startup healthcheck fails clearly if backend unavailable
5. ensure fallback behavior is explicit
6. update operator-facing status text
7. run smoke and rollback drill
8. add tests

---

## Minimum tests required

- default backend is mobkit under default config
- legacy backend can still be selected explicitly
- runtime healthcheck failure causes documented behavior
- Goldfish provenance remains active in default path
- rollback config restores legacy runtime path

---

## Acceptance criteria

- default path now uses mobkit
- authoritative provenance now uses Goldfish
- rollback switch remains documented and tested
- no hidden direct legacy runtime path remains active by default
- tests pass

---

## Suggested verification commands

```bash
pytest -q tests -k cutover
pytest -q tests -k default_backend
pytest -q tests -k rollback
```

If the repo includes a factory smoke runner, run a one-cycle smoke in default config and again in rollback config.

---

## Rollback plan

Emergency rollback must be possible with config only:
- select legacy backend
- disable Goldfish provenance if needed
- preserve dashboard functionality in degraded mode

---

## Completion summary format

Return:
1. files changed
2. what is now default
3. rollback config tested
4. tests run and results
5. risks for Task 07
