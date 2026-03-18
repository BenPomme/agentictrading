#!/usr/bin/env python3
"""One-time lineage queue cleanup: keep top N per family, retire the rest.

For each family:
- Keep the top N lineages by fitness_score (or ROI) among non-protected candidates.
- Keep any lineage in paper/shadow/canary_ready/live_ready/approved_live stage.
- Only consider lineages older than --max-age-days for retirement.
- Retire all other active walkforward lineages.

Usage:
    .venv312/bin/python scripts/cleanup_lineage_queue.py                         # dry-run (default)
    .venv312/bin/python scripts/cleanup_lineage_queue.py --execute               # apply retirements
    .venv312/bin/python scripts/cleanup_lineage_queue.py --keep-per-family 5     # keep top 5
    .venv312/bin/python scripts/cleanup_lineage_queue.py --max-age-days 14       # only retire if >14 days old
    .venv312/bin/python scripts/cleanup_lineage_queue.py --family my_family_id   # single family
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from factory.registry import FactoryRegistry  # noqa: E402


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_old_enough(lin: dict, cutoff: datetime) -> bool:
    """Return True if the lineage is old enough to be eligible for retirement."""
    for key in ("created_at", "updated_at"):
        raw = lin.get(key)
        if raw:
            try:
                ts = datetime.fromisoformat(raw)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                return ts <= cutoff
            except ValueError:
                continue
    # No timestamp found — treat as old (eligible)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cleanup lineage queue — keep top N per family.")
    parser.add_argument("--execute", action="store_true", help="Apply retirements (default: dry-run)")
    parser.add_argument("--data-root", default="data/factory", help="Factory data root")
    parser.add_argument(
        "--keep-per-family",
        type=int,
        default=3,
        metavar="N",
        help="Keep top N non-paper/shadow candidates per family (default: 3)",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=7,
        metavar="DAYS",
        help="Only retire lineages older than this many days (default: 7)",
    )
    parser.add_argument(
        "--family",
        default=None,
        metavar="FAMILY_ID",
        help="If specified, only clean up this family",
    )
    args = parser.parse_args(argv)

    data_root = PROJECT_ROOT / args.data_root
    registry = FactoryRegistry(data_root)

    # Load all lineages
    state_path = data_root / "state" / "summary.json"
    if not state_path.exists():
        print(f"State file not found: {state_path}")
        return 1

    state = json.loads(state_path.read_text())
    lineages = state.get("lineages") or []

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.max_age_days)

    # Group active lineages by family
    by_family: dict[str, list[dict]] = defaultdict(list)
    for lin in lineages:
        if lin.get("active"):
            fid = lin.get("family_id", "unknown")
            if args.family is not None and fid != args.family:
                continue
            by_family[fid].append(lin)

    to_retire: list[dict] = []
    to_keep: list[dict] = []

    for family_id, family_lineages in sorted(by_family.items()):
        # Separate protected (paper/shadow/etc.) from candidates
        protected = []
        candidates = []
        for lin in family_lineages:
            stage = str(lin.get("current_stage") or "")
            if stage in {"paper", "shadow", "canary_ready", "live_ready", "approved_live"}:
                protected.append(lin)
            else:
                candidates.append(lin)

        # Sort candidates by fitness_score / roi_pct descending
        candidates.sort(
            key=lambda l: (
                l.get("fitness_score") or l.get("roi_pct") or 0,
                l.get("trade_count") or 0,
            ),
            reverse=True,
        )

        # Keep top N; retire the rest (subject to age gate)
        keep_n = args.keep_per_family
        for i, lin in enumerate(candidates):
            if i < keep_n:
                to_keep.append(lin)
            else:
                # Only retire if the lineage is old enough
                if _is_old_enough(lin, cutoff):
                    to_retire.append(lin)
                else:
                    to_keep.append(lin)

        for lin in protected:
            to_keep.append(lin)

    total_active = sum(len(v) for v in by_family.values())

    print(f"Families:       {len(by_family)}")
    print(f"Active total:   {total_active}")
    print(f"Keep per family:{args.keep_per_family}")
    print(f"Min age (days): {args.max_age_days}")
    if args.family:
        print(f"Filter family:  {args.family}")
    print(f"To keep:        {len(to_keep)}")
    print(f"To retire:      {len(to_retire)}")
    print()

    if to_retire:
        print("=== RETIREMENTS ===")
        for lin in sorted(to_retire, key=lambda l: l.get("family_id", "")):
            roi = lin.get("roi_pct") or 0
            stage = lin.get("current_stage") or "?"
            print(f"  RETIRE  {lin['lineage_id']:<60s}  stage={stage:<12s}  roi={roi:+.1f}%")
        print()

    if to_keep:
        print("=== KEEPING ===")
        for lin in sorted(to_keep, key=lambda l: l.get("family_id", "")):
            roi = lin.get("roi_pct") or 0
            stage = lin.get("current_stage") or "?"
            reason = "paper/shadow" if stage in {"paper", "shadow", "canary_ready", "live_ready", "approved_live"} else "top-N"
            print(f"  KEEP    {lin['lineage_id']:<60s}  stage={stage:<12s}  roi={roi:+.1f}%  ({reason})")
        print()

    if not args.execute:
        kept_after = total_active - len(to_retire)
        print(f"Total active after cleanup: {kept_after} (from {total_active})")
        print()
        print("[DRY RUN] No changes applied. Pass --execute to retire lineages.")
        return 0

    # Apply retirements
    retired_count = 0
    for lin in to_retire:
        lineage_id = lin["lineage_id"]
        try:
            record = registry.load_lineage(lineage_id)
            if record and record.active:
                record.active = False
                record.iteration_status = "retired"
                record.retirement_reason = "queue_cleanup"
                record.updated_at = _utc_now_iso()
                registry.save_lineage(record)
                retired_count += 1
        except Exception as exc:
            print(f"  ERROR retiring {lineage_id}: {exc}")

    print(f"[EXECUTED] Retired {retired_count}/{len(to_retire)} lineages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
