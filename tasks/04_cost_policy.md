# Task 04 — Cost Policy, Budget Ledger, and Downgrade Logic

## Objective

Implement enforceable budget governance across runtime tasks, mob workflows, and strategy families.

This task must move the repo from advisory cost control to deterministic policy enforcement.

---

## Why this task exists

A fully autonomous research factory can overspend or thrash if:
- retries are uncontrolled
- member fanout grows
- deep reviews remain unrestricted
- weak lineages continue too long

Budgeting must be explicit, visible, and test-covered.

---

## Allowed files

- `factory/governance/`
- `factory/runtime/`
- `factory/orchestrator.py`
- config / settings files
- `factory/registry.py` if needed for projection
- tests for governance and runtime downgrade behavior

---

## Forbidden in this task

- do not cut over defaults yet
- do not rewrite dashboard/UI broadly
- do not add broad observability work outside what is necessary to expose policy results
- do not hide budget failures inside prompts or runtime magic

---

## Required deliverables

## 1. Cost policy module
Implement policy definitions for:
- global budget
- family budget
- lineage budget
- task budget
- member budget

## 2. Budget ledger
Implement a ledger that captures:
- planned budget
- actual usage
- downgrade actions
- stop actions
- fallback reasons

## 3. Downgrade cascade
Implement deterministic downgrade order such as:
1. lower token limit
2. remove nonessential reviewer members
3. switch reviewers to cheaper model tiers
4. collapse mob to single structured task
5. fallback or stop

## 4. Circuit breakers
Implement breaker logic for:
- global overspend
- family overspend
- repeated schema failure
- runaway fallback or instability

---

## Suggested implementation steps

1. inspect current cost guard logic, if any
2. define policy objects and their config source
3. wire policy checks into runtime manager and/or mobkit runtime
4. record planned and actual usage in the ledger
5. implement downgrade actions and event fields
6. update orchestrator to obey stop decisions
7. add tests

---

## Minimum tests required

- task budget downgrade
- family throttle mode
- global stop mode
- lineage spend forcing retirement or pause
- member removal when budget too low
- repeated invalid output triggers failure instead of infinite retry

---

## Acceptance criteria

- budgets are enforced, not merely logged
- downgrade decisions are deterministic
- stop conditions exist for hard breaches
- policy results are available to later observability work
- tests pass

---

## Suggested verification commands

```bash
pytest -q tests -k budget
pytest -q tests -k cost_policy
pytest -q tests -k governance
```

---

## Rollback plan

- feature flag can place policy in observe-only mode
- runtime remains callable if strict enforcement is disabled
- downgrade metadata remains additive

---

## Completion summary format

Return:
1. files changed
2. budget levels implemented
3. downgrade cascade behavior
4. tests run and results
5. risks for Task 05
