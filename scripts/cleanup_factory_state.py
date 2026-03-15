#!/usr/bin/env python3
"""One-time factory state cleanup.

Fixes:
1. Promote best-fitness lineage to champion in each family
2. Prune zombie lineages (keep top N per family, retire the rest)
3. Remove stale arbitrage-era portfolios
4. Attach backtest ROI to champion lineages from optimization results
5. Remove HMM family reference from lineages (HMM has no lineages yet, only backtest results)

Usage:
    python3 scripts/cleanup_factory_state.py --dry-run   # preview changes
    python3 scripts/cleanup_factory_state.py             # apply changes
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

REGISTRY_ROOT = PROJECT_ROOT / "data" / "factory"
LINEAGES_DIR = REGISTRY_ROOT / "lineages"
FAMILIES_DIR = REGISTRY_ROOT / "families"
STATE_PATH = REGISTRY_ROOT / "state" / "summary.json"
PORTFOLIOS_DIR = PROJECT_ROOT / "data" / "portfolios"
BACKTEST_DIR = PROJECT_ROOT / "data" / "backtest_results"

MAX_ACTIVE_PER_FAMILY = 10
MAX_SHADOW = 5
MAX_PAPER = 2

STALE_PORTFOLIOS = [
    "research_factory",
    "hedge_research",
    "hedge_validation",
    "mev_scout_sol",
    "command_center",
    "dummy",
    "betfair_crossbook_consensus",
    "betfair_execution_book",
    "betfair_prediction_league",
    "betfair_suspension_lag",
    "betfair_timezone_decay",
]


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json(path: Path, data: dict, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _best_backtest_roi(family_id: str) -> float | None:
    family_dir = BACKTEST_DIR / family_id
    if not family_dir.exists():
        return None
    best = None
    for fp in family_dir.glob("*_optuna_results.json"):
        data = _read_json(fp)
        metrics = data.get("best_metrics", {})
        roi = float(metrics.get("total_return_pct", metrics.get("total_return", 0)) or 0)
        if best is None or roi > best:
            best = roi
    return best


def cleanup(dry_run: bool = True) -> None:
    prefix = "[DRY-RUN] " if dry_run else ""
    now_iso = datetime.now(timezone.utc).isoformat()

    # === 1. Load all lineages ===
    all_lineages: dict[str, dict] = {}
    for lineage_dir in sorted(LINEAGES_DIR.iterdir()):
        lineage_path = lineage_dir / "lineage.json"
        if lineage_path.exists():
            data = _read_json(lineage_path)
            if data:
                all_lineages[lineage_dir.name] = data

    print(f"Total lineages in registry: {len(all_lineages)}")

    # === 2. Group by family ===
    families: dict[str, list[dict]] = {}
    for lid, data in all_lineages.items():
        fam = data.get("family_id", "unknown")
        families.setdefault(fam, []).append(data)

    print(f"Families: {list(families.keys())}")

    # === 3. For each family: sort by fitness, promote champion, prune zombies ===
    retained_lineage_ids: set[str] = set()
    retired_lineage_ids: set[str] = set()
    champion_updates: dict[str, str] = {}

    for fam_id, lineages in families.items():
        active = [l for l in lineages if l.get("active", True) and not l.get("retired_at")]
        retired = [l for l in lineages if not l.get("active", True) or l.get("retired_at")]

        active_sorted = sorted(active, key=lambda l: -float(l.get("fitness_score", 0) or 0))

        if not active_sorted:
            print(f"  {fam_id}: no active lineages, skipping")
            continue

        best = active_sorted[0]
        best_id = best.get("lineage_id", "?")
        best_fitness = float(best.get("fitness_score", 0) or 0)
        old_champion = next((l for l in active if l.get("role") == "champion"), None)
        old_champion_id = old_champion.get("lineage_id", "?") if old_champion else "none"
        old_champion_fitness = float(old_champion.get("fitness_score", 0) or 0) if old_champion else -999

        print(f"\n  {fam_id} ({len(active)} active, {len(retired)} retired):")
        print(f"    Current champion: {old_champion_id} (fitness={old_champion_fitness:.2f})")
        print(f"    Best fitness:     {best_id} (fitness={best_fitness:.2f})")

        if best_id != old_champion_id:
            print(f"    {prefix}PROMOTING {best_id} to champion (was {old_champion_id})")
            champion_updates[fam_id] = best_id

        keep_ids = set()
        keep_ids.add(best_id)
        paper_count = 0
        shadow_count = 0

        for l in active_sorted[1:]:
            lid = l.get("lineage_id", "?")
            if paper_count < MAX_PAPER:
                keep_ids.add(lid)
                paper_count += 1
            elif shadow_count < MAX_SHADOW:
                keep_ids.add(lid)
                shadow_count += 1

        for l in active_sorted:
            lid = l.get("lineage_id", "?")
            if lid in keep_ids:
                retained_lineage_ids.add(lid)
            else:
                retired_lineage_ids.add(lid)

        bt_roi = _best_backtest_roi(fam_id)
        if bt_roi is not None:
            print(f"    Backtest ROI for family: {bt_roi:.2f}%")

        to_retire = len(active) - len(keep_ids)
        if to_retire > 0:
            print(f"    {prefix}RETIRING {to_retire} zombie lineages (keeping {len(keep_ids)})")

    print(f"\n=== Summary ===")
    print(f"  Retained: {len(retained_lineage_ids)} lineages")
    print(f"  Retiring: {len(retired_lineage_ids)} lineages")
    print(f"  Champion promotions: {len(champion_updates)}")

    # === 4. Apply changes to lineage files ===
    for lid in retired_lineage_ids:
        lineage_path = LINEAGES_DIR / lid / "lineage.json"
        data = _read_json(lineage_path)
        if data:
            data["active"] = False
            data["retired_at"] = now_iso
            data["retirement_reason"] = "cleanup_zombie_prune"
            data["role"] = "retired"
            print(f"  {prefix}Retiring: {lid}")
            _write_json(lineage_path, data, dry_run)

    for lid in retained_lineage_ids:
        lineage_path = LINEAGES_DIR / lid / "lineage.json"
        data = _read_json(lineage_path)
        if not data:
            continue
        fam_id = data.get("family_id", "")
        new_champion = champion_updates.get(fam_id)

        if new_champion and lid == new_champion:
            data["role"] = "champion"
            data["iteration_status"] = "champion"
            bt_roi = _best_backtest_roi(fam_id)
            if bt_roi is not None:
                data["backtest_roi_pct"] = bt_roi
            print(f"  {prefix}Setting {lid} as CHAMPION")
            _write_json(lineage_path, data, dry_run)
        elif new_champion and lid != new_champion:
            # Determine paper vs shadow based on position
            fam_lineages = families.get(fam_id, [])
            active_sorted = sorted(
                [l for l in fam_lineages if l.get("lineage_id") in retained_lineage_ids and l.get("lineage_id") != new_champion],
                key=lambda l: -float(l.get("fitness_score", 0) or 0),
            )
            idx = next((i for i, l in enumerate(active_sorted) if l.get("lineage_id") == lid), 999)
            if idx < MAX_PAPER:
                data["role"] = "paper_challenger"
            else:
                data["role"] = "shadow_challenger"
            bt_roi = _best_backtest_roi(fam_id)
            if bt_roi is not None:
                data["backtest_roi_pct"] = bt_roi
            _write_json(lineage_path, data, dry_run)

    # === 5. Update family files ===
    for fam_dir in sorted(FAMILIES_DIR.iterdir()):
        family_path = fam_dir / "family.json"
        data = _read_json(family_path)
        if not data:
            continue
        fam_id = data.get("family_id", fam_dir.name)
        if fam_id in champion_updates:
            old = data.get("champion_lineage_id", "?")
            data["champion_lineage_id"] = champion_updates[fam_id]
            fam_lineages = families.get(fam_id, [])
            active_sorted = sorted(
                [l for l in fam_lineages if l.get("lineage_id") in retained_lineage_ids and l.get("lineage_id") != champion_updates[fam_id]],
                key=lambda l: -float(l.get("fitness_score", 0) or 0),
            )
            data["paper_challenger_ids"] = [l.get("lineage_id") for l in active_sorted[:MAX_PAPER]]
            data["shadow_challenger_ids"] = [l.get("lineage_id") for l in active_sorted[MAX_PAPER:MAX_PAPER + MAX_SHADOW]]
            print(f"  {prefix}Family {fam_id}: champion {old} -> {champion_updates[fam_id]}")
            _write_json(family_path, data, dry_run)

    # === 6. Remove stale portfolios ===
    print(f"\n=== Stale Portfolio Cleanup ===")
    for portfolio_id in STALE_PORTFOLIOS:
        portfolio_path = PORTFOLIOS_DIR / portfolio_id
        if portfolio_path.exists():
            print(f"  {prefix}Removing stale portfolio: {portfolio_id}")
            if not dry_run:
                shutil.rmtree(portfolio_path, ignore_errors=True)
        else:
            print(f"  {portfolio_id}: already gone")

    # === 7. Regenerate summary.json with only active lineages ===
    print(f"\n=== Regenerating summary.json ===")
    state = _read_json(STATE_PATH)
    new_lineages = []
    for lineage_dir in sorted(LINEAGES_DIR.iterdir()):
        lineage_path = lineage_dir / "lineage.json"
        if lineage_path.exists():
            data = _read_json(lineage_path)
            if data and data.get("active", True) and not data.get("retired_at"):
                new_lineages.append(data)

    if not dry_run:
        state["lineages"] = new_lineages
        state["cleanup_applied_at"] = now_iso
        _write_json(STATE_PATH, state, dry_run=False)
    print(f"  {prefix}summary.json: {len(new_lineages)} active lineages (was {len(state.get('lineages', []))})")

    print(f"\n{'DRY RUN COMPLETE' if dry_run else 'CLEANUP COMPLETE'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying them")
    args = parser.parse_args()
    cleanup(dry_run=args.dry_run)
