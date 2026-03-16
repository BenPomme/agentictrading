# Task 02 — Goldfish Client and Provenance Mapping

## Objective

Introduce real Goldfish-backed provenance plumbing while keeping local registry/history behavior as a projection cache during migration.

This task must make Goldfish an actual integration boundary rather than a placeholder or naming convention.

---

## Why this task exists

The factory must record durable experiment lineage:
- proposals
- backtests
- critiques
- promotions
- retirements
- learning notes

This cannot remain fragmented across local JSON/JSONL files if the goal is reproducibility and durable experiment memory.

---

## Allowed files

- `factory/provenance/`
- `factory/goldfish_bridge.py`
- `factory/experiment_runner.py`
- `factory/registry.py`
- `factory/orchestrator.py`
- config / settings files
- tests for provenance integration

Keep scope bounded. Do not implement mobkit in this task.

---

## Forbidden in this task

- do not cut over the runtime to mobkit
- do not rewrite the full experiment runner
- do not remove local registry projection behavior
- do not silently swallow Goldfish failures
- do not treat scaffold-only file writing as a finished integration

---

## Required deliverables

## 1. Goldfish client
Add a repo-local `GoldfishClient` or equivalent provenance service that:
- ensures project initialization if required
- ensures daemon/client readiness if required
- creates or reuses workspace context
- creates runs / records
- finalizes results
- inspects and lists records
- tags records
- logs durable learning notes

## 2. Mapping layer
Define how these AgenticTrading concepts map into Goldfish:
- family
- lineage
- experiment evaluation
- promotion
- retirement
- learning memory

## 3. Hybrid record flow
Keep local execution for experiments if necessary, but for each evaluation:
- create or register a Goldfish run
- finalize the run with results
- attach correlation metadata
- write projection reference into local registry

## 4. De-placeholder bridge
Either:
- replace `factory/goldfish_bridge.py` internals to delegate to the new client, or
- deprecate it and redirect callers to the new provenance layer

---

## Suggested implementation steps

1. inspect current `factory/goldfish_bridge.py` and local registry/history usage
2. inspect real Goldfish APIs in the external repo
3. implement repo-local Goldfish adapter
4. define mapping helpers for family/lineage/run metadata
5. update experiment runner to register/finalize provenance
6. update registry to store authoritative record references
7. add tests

---

## Minimum tests required

- Goldfish client initializes or connects correctly in test mode
- one evaluation maps to one Goldfish run/record
- projection cache stores record reference
- retirement or promotion mapping writes expected metadata
- Goldfish failure is visible and not silently ignored

Use mocks or fixtures if direct daemon-backed integration is too heavy for unit tests, but include at least one higher-confidence smoke path if feasible.

---

## Acceptance criteria

- Goldfish is a real runtime dependency for provenance when enabled
- the repo no longer relies only on local JSON/JSONL for authoritative experiment history
- local registry remains usable as a projection cache
- no runtime cutover yet
- tests pass

---

## Suggested verification commands

```bash
pytest -q tests -k goldfish
pytest -q tests -k provenance
```

If the repo has a smoke runner, add or run a one-evaluation smoke test in temp root.

---

## Rollback plan

- feature flag can disable Goldfish provenance path
- local projection cache remains enough for temporary degraded operation
- no partial finalization should masquerade as success

---

## Completion summary format

Return:
1. files changed
2. how Goldfish is now used
3. whether local registry is still authoritative or only projection
4. tests run and results
5. risks for Task 03
