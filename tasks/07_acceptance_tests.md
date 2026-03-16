# Task 07 — Acceptance, Hardening, and Cleanup

## Objective

Finalize the refactor by:
- proving the system satisfies the acceptance criteria
- tightening tests and docs
- removing fake or misleading integration semantics
- preserving only intentional fallback paths

---

## Why this task exists

A migration is not complete when code compiles.
It is complete when:
- the default path is real
- the legacy path is intentionally bounded
- observability is sufficient
- operator docs are clear
- misleading placeholder integrations are gone

---

## Allowed files

- tests
- docs / operator docs / README sections relevant to architecture
- status or dashboard files if needed for final clarity
- deprecated bridge/wrapper files that need cleanup or deprecation notes
- small code changes required to satisfy final acceptance issues

Do not perform new architectural work beyond acceptance and cleanup.

---

## Forbidden in this task

- do not add new major features
- do not broaden runtime behavior
- do not rework provenance mapping unless required to fix acceptance failures
- do not remove rollback capabilities

---

## Required deliverables

## 1. Acceptance suite completion
Ensure the repo satisfies `docs/refactor/ACCEPTANCE_TESTS_AND_DOD.md`.

## 2. Final cleanup
Remove or deprecate misleading placeholder names or code paths.
Example: a “bridge” that no longer bridges anything should be renamed or explicitly marked deprecated.

## 3. Operator documentation
Update architecture and operational docs so the default path is described accurately.

## 4. Rollback drill evidence
Run and document rollback drill steps and outcomes.

## 5. Final risk register
Document any known limitations that remain after merge.

---

## Suggested implementation steps

1. run full targeted acceptance suite
2. identify failures or gaps
3. fix minimal code/docs needed to close gaps
4. update docs to reflect real architecture
5. mark deprecated files clearly
6. run rollback drill
7. produce final summary

---

## Minimum tests required

- all targeted tests from prior tasks
- one full-cycle smoke in default path
- one rollback smoke in legacy path
- one degraded-provenance smoke if supported
- one manual or semi-manual acceptance checklist result stored in summary or docs

---

## Acceptance criteria

- definition of done is satisfied
- docs reflect reality
- no misleading fake integration language remains
- fallback is preserved but clearly secondary
- tests pass

---

## Suggested verification commands

```bash
pytest -q
```

If the full test suite is too large, run:
```bash
pytest -q tests -k "runtime or goldfish or mobkit or governance or telemetry or cutover"
```

Also run any existing smoke scripts documented by the repo.

---

## Rollback plan

No rollback from this task should remove the tested runtime and provenance cutover.
If cleanup introduces issues, revert only cleanup/doc changes, not the already verified architecture.

---

## Completion summary format

Return:
1. files changed
2. acceptance gates passed
3. smoke and rollback drill results
4. remaining known limitations
5. recommendation on whether the branch is merge-ready
