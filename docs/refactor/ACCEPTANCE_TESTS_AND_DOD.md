# Acceptance Tests and Definition of Done
## Refactor completion criteria for mobkit + Meerkat + Goldfish integration

## Purpose

This document defines the required test gates, manual checks, observability checks, and rollback drills that must pass before the refactor is considered complete.

This document is the final gate for merging the new default runtime path.

---

## Definition of Done

The refactor is complete only when all of the following are true.

## 1. Runtime cutover is real
- AgenticTrading uses the runtime adapter boundary.
- mobkit is the default orchestrator backend for supported task types.
- the legacy runtime path exists only as a fallback, not as the default hidden path.
- backend selection is visible in logs and status output.

## 2. Goldfish is the real provenance layer
- every experiment-like cycle produces a Goldfish run/record
- finalized results exist for completed evaluations
- promotions and retirements write tags / durable notes
- local registry entries point to authoritative record references

## 3. Cost governance is enforceable
- a task exceeding budget downgrades or stops predictably
- family-level throttling works
- global budget stop works
- downgrade actions are visible to operators

## 4. Multi-agent behavior is explicit
- at least one proposal or critique workflow uses real coordinated members
- member-level traces are captured
- member-level budgets are enforced
- output remains schema-valid

## 5. Observability is complete
- one full cycle can be reconstructed from logs, traces, and Goldfish record data
- fallback reasons are visible
- backend health is visible
- spend summaries are visible

## 6. Rollback is tested
- runtime can be switched back to legacy by config
- provenance can be disabled by config for emergency operation
- dashboards remain functional in fallback mode

---

## Test pyramid

## Unit tests
Fast tests for contracts and deterministic policy behavior.

## Integration tests
Short-lived end-to-end interactions between factory modules and external backends or test doubles.

## Smoke tests
One-cycle or one-task full-path checks in a temp environment.

## Failure-path tests
Budget trips, backend outages, invalid schemas, fallback activation, and transient provenance failures.

## Manual operator tests
Dashboard visibility, CLI output, and rollback drills.

---

## Required unit tests

Create or update tests for at least the following.

## Runtime contract tests
- `test_runtime_manager_selects_legacy_by_default`
- `test_runtime_manager_selects_mobkit_when_enabled`
- `test_agent_run_envelope_serializes_all_required_fields`
- `test_trace_context_propagates_ids`

## Schema enforcement tests
- `test_structured_task_rejects_invalid_output`
- `test_mob_workflow_returns_schema_valid_final_payload`
- `test_schema_retry_limit_is_respected`

## Cost policy tests
- `test_task_budget_downgrades_token_limit`
- `test_member_budget_removes_nonessential_reviewer`
- `test_family_budget_enters_throttled_mode`
- `test_global_budget_trips_circuit_breaker`
- `test_lineage_budget_forces_retirement`

## Provenance mapping tests
- `test_factory_family_maps_to_goldfish_workspace`
- `test_lineage_evaluation_maps_to_goldfish_run`
- `test_retirement_maps_to_record_tag_and_thought`
- `test_promotion_maps_to_record_tag`

## Fallback behavior tests
- `test_mobkit_healthcheck_failure_falls_back_when_allowed`
- `test_mobkit_healthcheck_failure_raises_when_fallback_disabled`
- `test_goldfish_write_failure_surfaces_operator_visible_error`

---

## Required integration tests

## 1. Goldfish integration smoke
Scenario:
- temp project root
- initialize provenance layer
- create workspace or equivalent namespace
- create run
- finalize run
- inspect record

Assertions:
- record exists
- correlation metadata exists
- local projection cache references record

## 2. mobkit structured workflow smoke
Scenario:
- run one supported structured workflow through backend
- use minimal schema and narrow context

Assertions:
- backend healthcheck passes
- workflow returns schema-valid payload
- usage metadata returned
- trace IDs present

## 3. mobkit multi-member workflow smoke
Scenario:
- execute one small proposal or critique workflow with multiple members

Assertions:
- member traces present
- per-member usage present or explicitly unavailable but represented
- final payload valid
- orchestration backend identifies itself as mobkit

## 4. Full factory cycle smoke
Scenario:
- run one small factory cycle in temp root with test data
- create or mutate a candidate
- evaluate candidate
- critique results
- write provenance

Assertions:
- runtime used is the configured backend
- Goldfish record exists
- evaluation artifacts exist
- registry projection updated
- no silent direct legacy runtime call occurred when disabled

---

## Failure-path tests

## Budget breach test
Scenario:
- artificially low task budget

Assertions:
- downgrade or stop occurs
- event logged
- no infinite retry

## Family throttle test
Scenario:
- exceed family budget with repeated workflows

Assertions:
- new family ideation disabled
- mutation-only mode or pause activated
- operator signal visible

## Runtime outage test
Scenario:
- mobkit backend unavailable

Assertions:
- healthcheck fails clearly
- fallback path used if enabled
- no hidden partial run
- operator-visible event emitted

## Provenance outage test
Scenario:
- Goldfish unavailable or write failure

Assertions:
- failure is visible
- partial local projection is marked degraded
- task completion policy behaves as configured
- no false “success” on provenance write

## Invalid output test
Scenario:
- runtime returns schema-invalid response repeatedly

Assertions:
- retry count bounded
- workflow downgraded or failed
- reason visible in logs

---

## Manual verification checklist

Before merge, an operator must verify the following manually.

## Backend visibility
- current backend is visible in CLI / dashboard
- backend changes after config switch are reflected without code edits

## Spend visibility
- remaining budget is visible
- recent downgrade events are visible
- top-spending family is visible

## Provenance visibility
- latest Goldfish record ID is visible
- promotion / retirement tags are visible
- one record can be inspected end-to-end

## Rollback visibility
- setting legacy backend restores legacy path
- disabling provenance shows degraded mode explicitly
- no dashboard crash in degraded mode

---

## Observability requirements

A full cycle must emit correlated identifiers for:
- cycle
- family
- lineage
- runtime run
- Goldfish record

A full cycle must expose the following operator-visible statuses:
- backend selected
- budget state
- fallback state
- provenance write status
- circuit breaker state

---

## Performance acceptance targets

These targets should be adjusted to the real environment, but the test suite must verify the direction of travel.

- backend healthcheck: fast enough for startup
- no exponential runtime growth with number of reviewers
- no uncontrolled fanout in mob workflows
- no repeated duplicate Goldfish writes for same finalization step

Performance acceptance is qualitative unless the repo already defines benchmark baselines.

---

## Rollback drill

Before final merge, perform this drill:

1. run smoke cycle with mobkit backend enabled
2. confirm Goldfish record written
3. switch to legacy backend
4. rerun smoke cycle
5. confirm cycle still works
6. re-enable mobkit backend
7. disable Goldfish provenance
8. rerun smoke cycle in degraded mode
9. confirm operator signal indicates degraded provenance

This drill must be documented in the merge summary.

---

## Final merge gate

Do not merge the cutover unless all are true:
- test suite green
- smoke tests green
- one manual rollback drill completed
- one manual provenance inspection completed
- operator-visible dashboards or status commands show backend and budget state
- no hidden default direct provider path remains active

---

## Post-merge watchlist

For the first production-like runs, monitor:
- fallback rate
- schema failure rate
- Goldfish write failure rate
- family-level throttling frequency
- member-level spend imbalance
- repeated lineage failure classes

If these drift badly, revert to legacy backend and resume from rollback plan.
