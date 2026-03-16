# Task 01 — Runtime Adapter Scaffolding

## Objective

Introduce the runtime abstraction layer that separates AgenticTrading business logic from any concrete runtime implementation.

This task must not cut over to mobkit yet.
This task must preserve current default behavior.

---

## Why this task exists

The current repo likely couples the factory orchestrator to concrete runtime logic.
Before integrating mobkit, the repo needs stable local interfaces for:

- runtime task invocation
- structured outputs
- member traces
- fallback reasoning
- trace correlation

Without this boundary, later tasks will either be fragile or will spread mobkit-specific code across the codebase.

---

## Allowed files

Create or modify only files in these areas unless required test fixtures already live elsewhere:

- `factory/runtime/`
- `factory/orchestrator.py`
- current runtime file(s), likely `factory/agent_runtime.py`
- config / settings files
- tests related to runtime selection and contracts

If another file must change, state why before changing it.

---

## Forbidden in this task

- do not implement real mobkit orchestration yet
- do not implement Goldfish provenance yet
- do not remove the legacy runtime path
- do not change default backend selection away from legacy
- do not rewrite business decision logic
- do not add broad observability plumbing outside minimal trace scaffolding

---

## Required deliverables

## 1. Runtime interface
Add a repo-local `AgentRuntime` interface or abstract base with methods matching current factory use cases, including:
- generate proposal
- generate family proposal
- suggest tweak
- critique post evaluation
- diagnose bug
- resolve maintenance item
- design model
- mutate model

## 2. Runtime result envelope
Add a typed result object that carries:
- backend name
- provider/model
- success
- payload
- usage metadata
- trace identifiers
- fallback reason
- optional member traces

## 3. Runtime manager
Add a runtime manager that selects backend by config.

Expected behavior:
- default selects legacy runtime
- unsupported or disabled new backend does not become active
- explicit config can request mobkit later

## 4. Legacy wrapper
Wrap existing runtime behavior behind the new interface.
Do not change its semantics beyond what is needed to fit the interface.

## 5. Minimal trace scaffold
Add minimal correlation/trace IDs to the runtime envelope.
No full telemetry system yet.

---

## Suggested implementation steps

1. inspect how `factory/orchestrator.py` currently invokes runtime logic
2. extract a minimal protocol / ABC into `factory/runtime/agent_runtime_base.py`
3. define result contract in `factory/runtime/runtime_contracts.py`
4. implement `factory/runtime/legacy_runtime.py` as a wrapper around current behavior
5. implement `factory/runtime/runtime_manager.py`
6. update orchestrator to depend on the manager instead of concrete runtime code
7. add config / env selection
8. add tests

---

## Minimum tests required

- runtime manager selects legacy backend by default
- runtime manager can resolve requested backend names
- runtime envelope includes required fields
- orchestrator imports the runtime via manager, not direct concrete coupling

If the codebase already has equivalent tests, update them.

---

## Acceptance criteria

- repo still runs in legacy mode by default
- new runtime abstractions exist
- orchestrator depends on local interface / manager
- no user-visible behavior regression in legacy path
- tests pass

---

## Suggested verification commands

Use the repo’s real test runner. If unclear, inspect project conventions first.

Typical examples:
```bash
pytest -q tests/test_runtime_manager.py
pytest -q tests -k runtime
```

Add a small smoke command if the repo already has one.

---

## Rollback plan

If this task fails:
- restore orchestrator to direct legacy runtime selection
- keep interface files if tests pass and they are unused
- do not leave partially wired backend selection

---

## Completion summary format

Return:
1. files changed
2. how runtime selection works now
3. tests run and results
4. whether legacy remains the default
5. risks for Task 02
