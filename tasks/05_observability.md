# Task 05 — Observability, Trace Correlation, and Operator Visibility

## Objective

Implement the trace and telemetry layer required to reconstruct a full cycle across:
- factory orchestration
- runtime backend
- mob members
- Goldfish provenance
- budget decisions
- fallback paths

---

## Why this task exists

A new runtime and provenance layer are operationally unsafe without visibility.
The system must make it obvious:
- which backend was used
- how much it cost
- why it downgraded
- where the authoritative record lives
- whether fallback occurred

---

## Allowed files

- `factory/telemetry/`
- `factory/orchestrator.py`
- `factory/runtime/`
- `factory/provenance/`
- dashboard / CLI status files if they already exist
- tests related to trace and logging

Keep UI changes bounded to exposing backend, budget, and record references.

---

## Forbidden in this task

- do not change runtime defaults yet
- do not rewrite business policy
- do not introduce vendor-specific logging scattered across modules
- do not rely only on free-text logs; use structured fields

---

## Required deliverables

## 1. Trace context
Implement correlation IDs including:
- cycle_id
- trace_id
- family_id
- lineage_id
- runtime_run_id
- goldfish_record_id

## 2. Structured events
Emit structured events for:
- workflow planned
- workflow started
- member started / finished
- downgrade applied
- fallback activated
- Goldfish run created / finalized
- promotion / retirement decision

## 3. Operator status exposure
Expose at least:
- active backend
- recent fallback reasons
- current budget state
- latest Goldfish record reference
- runtime health summary

## 4. Projection hooks
Ensure local registry or status cache stores enough to render operator views quickly without becoming the authoritative provenance store.

---

## Suggested implementation steps

1. define trace context object
2. thread it through orchestrator, runtime, and provenance layers
3. define structured event schema or helper functions
4. emit runtime and provenance events
5. update status/dashboard surfaces
6. add tests

---

## Minimum tests required

- correlation IDs appear across runtime and provenance records
- fallback event is emitted
- downgrade event is emitted
- status view can render backend and record reference
- one cycle can be reconstructed from emitted data in test

---

## Acceptance criteria

- one cycle is traceable end-to-end
- operator surfaces show backend, budget, and record reference
- logging is structured enough for future indexing
- tests pass

---

## Suggested verification commands

```bash
pytest -q tests -k telemetry
pytest -q tests -k trace
pytest -q tests -k observability
```

---

## Rollback plan

- telemetry is additive
- older status views continue to function or degrade explicitly
- no critical behavior depends only on dashboard rendering

---

## Completion summary format

Return:
1. files changed
2. IDs and events introduced
3. operator-visible surfaces updated
4. tests run and results
5. risks for Task 06
