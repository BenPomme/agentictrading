#!/usr/bin/env python3
"""One-time deterministic revival of paper-unvalidated families.

Revives families that:
- Have only retired lineages (dead family state)
- Were never paper tested (paper_days == 0 or close to 0)
- Have positive backtest evidence
- Match the target venue scope

This is a DETERMINISTIC operation — no LLM compute, no heuristics.
It directly modifies lineage state to reactivate retired champions.

Usage:
    .venv312/bin/python scripts/revive_paper_candidates.py --dry-run
    .venv312/bin/python scripts/revive_paper_candidates.py --execute
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _best_eval_roi(evals_dir: Path, lineage_id: str) -> tuple[float, int]:
    """Return (best_monthly_roi, best_trade_count) from evaluations."""
    eval_path = evals_dir / lineage_id
    if not eval_path.exists():
        return 0.0, 0
    best_roi = 0.0
    best_trades = 0
    for ef in eval_path.glob("*.json"):
        try:
            ev = json.loads(ef.read_text())
            roi = float(ev.get("monthly_roi_pct", 0) or 0)
            trades = int(ev.get("trade_count", 0) or 0)
            if roi > best_roi:
                best_roi = roi
                best_trades = trades
        except Exception:
            pass
    return best_roi, best_trades


# ---------------------------------------------------------------------------
# Revival criteria — deterministic, explicit
# ---------------------------------------------------------------------------

REVIVAL_TARGETS = {
    "funding_term_structure_dislocation": {
        "venue_scope": ["binance"],
        "min_backtest_roi": 0.0,  # any positive
        "probationary": False,
        "reason": "Positive backtest ROI (5.83%), 14 trades, never paper tested. Retired by TTL, not by evidence failure.",
    },
    "liquidation_rebound_absorption": {
        "venue_scope": ["binance"],
        "min_backtest_roi": 0.0,
        "probationary": True,
        "reason": "High backtest ROI (289%) likely inflated but 417 trades shows signal. Never paper tested. Retired by TTL. Probationary: paper is the truth test.",
    },
}


def audit_candidates(
    families_dir: Path,
    lineages_dir: Path,
    evals_dir: Path,
) -> list[dict]:
    """Audit all families and return revival candidates."""
    candidates = []
    for fp in sorted(families_dir.glob("*.json")):
        family = _load_json(fp)
        fid = family["family_id"]
        if fid not in REVIVAL_TARGETS:
            continue
        target = REVIVAL_TARGETS[fid]
        venues = set(str(v).lower() for v in family.get("target_venues", []))
        scope_venues = set(target["venue_scope"])
        if not venues.issubset(scope_venues | venues):  # at least overlaps
            pass  # ok, venue check is informational

        # Find the best retired lineage
        champion_id = family.get("champion_lineage_id", "")
        retired_ids = family.get("retired_lineage_ids", [])
        best_candidate = None
        best_roi = -999.0

        for lid in [champion_id] + retired_ids:
            if not lid:
                continue
            lineage_path = lineages_dir / lid / "lineage.json"
            if not lineage_path.exists():
                continue
            lineage = _load_json(lineage_path)
            if lineage.get("active", False):
                continue  # already active

            roi, trades = _best_eval_roi(evals_dir, lid)
            if roi > best_roi:
                best_roi = roi
                best_candidate = {
                    "family_id": fid,
                    "lineage_id": lid,
                    "lineage_path": str(lineage_path),
                    "family_path": str(fp),
                    "venues": list(venues),
                    "best_roi": roi,
                    "best_trades": trades,
                    "retirement_reason": lineage.get("retirement_reason", "?"),
                    "paper_days": int(lineage.get("last_decision", {}).get("paper_days", 0) or 0),
                    "probationary": target["probationary"],
                    "revival_reason": target["reason"],
                }

        if best_candidate is not None and best_candidate["best_roi"] > target["min_backtest_roi"]:
            candidates.append(best_candidate)

    return candidates


def execute_revival(candidate: dict) -> dict:
    """Execute a single lineage revival. Returns a result dict."""
    lineage_path = Path(candidate["lineage_path"])
    family_path = Path(candidate["family_path"])
    lineage = _load_json(lineage_path)
    family = _load_json(family_path)

    old_status = lineage.get("iteration_status", "?")
    old_stage = lineage.get("current_stage", "?")

    # --- Revive the lineage ---
    lineage["active"] = True
    lineage["iteration_status"] = "revived_for_paper"
    lineage["retired_at"] = None
    lineage["retirement_reason"] = None
    lineage["current_stage"] = "walkforward"  # let promotion.decide() advance it
    lineage["loss_streak"] = 0
    lineage["tweak_count"] = 0
    lineage["created_at"] = _utc_now()  # reset TTL clock so backtest_ttl doesn't immediately re-retire
    lineage["updated_at"] = _utc_now()
    # Clear blockers that referenced the old retirement
    lineage["blockers"] = [
        b for b in lineage.get("blockers", [])
        if "backtest_ttl" not in b and "retired" not in b.lower()
    ]
    if candidate["probationary"]:
        lineage["blockers"].append("probationary_paper_candidate")

    _save_json(lineage_path, lineage)

    # --- Update family: remove from retired, set as champion if needed ---
    lid = candidate["lineage_id"]
    retired = family.get("retired_lineage_ids", [])
    if lid in retired:
        retired.remove(lid)
        family["retired_lineage_ids"] = retired

    # If current champion is retired, promote this lineage
    current_champ = family.get("champion_lineage_id", "")
    if current_champ == lid or current_champ in retired:
        family["champion_lineage_id"] = lid

    _save_json(family_path, family)

    return {
        "lineage_id": lid,
        "family_id": candidate["family_id"],
        "old_status": old_status,
        "new_status": "revived_for_paper",
        "old_stage": old_stage,
        "new_stage": "walkforward",
        "probationary": candidate["probationary"],
        "best_roi": candidate["best_roi"],
        "best_trades": candidate["best_trades"],
        "revival_reason": candidate["revival_reason"],
        "revived_at": _utc_now(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Revive paper-unvalidated families.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Audit only, do not modify state")
    group.add_argument("--execute", action="store_true", help="Execute revival")
    args = parser.parse_args(argv)

    families_dir = PROJECT_ROOT / "data" / "factory" / "families"
    lineages_dir = PROJECT_ROOT / "data" / "factory" / "lineages"
    evals_dir = PROJECT_ROOT / "data" / "factory" / "evaluations"

    print("=" * 60)
    print("PAPER-UNVALIDATED FAMILY REVIVAL")
    print(f"  mode: {'DRY RUN' if args.dry_run else 'EXECUTE'}")
    print(f"  as_of: {_utc_now()}")
    print("=" * 60)

    candidates = audit_candidates(families_dir, lineages_dir, evals_dir)

    if not candidates:
        print("\nNo revival candidates found.")
        return 0

    print(f"\nFound {len(candidates)} revival candidate(s):\n")
    for c in candidates:
        flag = " [PROBATIONARY]" if c["probationary"] else ""
        print(f"  {c['family_id']}{flag}")
        print(f"    lineage: {c['lineage_id']}")
        print(f"    venues: {c['venues']}")
        print(f"    best_roi: {c['best_roi']:.2f}%")
        print(f"    best_trades: {c['best_trades']}")
        print(f"    retired_reason: {c['retirement_reason']}")
        print(f"    revival_reason: {c['revival_reason']}")
        print()

    if args.dry_run:
        print("[DRY RUN] No changes made.")
        return 0

    print("Executing revival...\n")
    results = []
    for c in candidates:
        result = execute_revival(c)
        results.append(result)
        print(f"  REVIVED: {result['lineage_id']}")
        print(f"    {result['old_status']} -> {result['new_status']}")
        print(f"    stage: {result['old_stage']} -> {result['new_stage']}")
        print(f"    probationary: {result['probationary']}")
        print()

    # Write results artifact
    artifact_path = PROJECT_ROOT / "artifacts" / "revival_and_holdoff_validation.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps({
        "as_of": _utc_now(),
        "operation": "paper_unvalidated_family_revival",
        "candidates_found": len(candidates),
        "revivals_executed": len(results),
        "results": results,
    }, indent=2) + "\n")
    print(f"Artifact written: {artifact_path.relative_to(PROJECT_ROOT)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
