#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent

import sys

sys.path.insert(0, str(PROJECT_ROOT))

from factory.contracts import FactoryFamily, LineageRecord, PromotionStage, utc_now_iso  # noqa: E402
from factory.promotion import default_family_promotion_metadata  # noqa: E402
from factory.registry import FactoryRegistry  # noqa: E402
from factory.strategy_inventor import default_family_inventor_metadata  # noqa: E402


def _stage_rank(stage: str) -> int:
    order = {
        PromotionStage.APPROVED_LIVE.value: 0,
        PromotionStage.LIVE_READY.value: 1,
        PromotionStage.CANARY_READY.value: 2,
        PromotionStage.PAPER.value: 3,
        PromotionStage.SHADOW.value: 4,
        PromotionStage.STRESS.value: 5,
        PromotionStage.WALKFORWARD.value: 6,
        PromotionStage.GOLDFISH_RUN.value: 7,
        PromotionStage.DATA_CHECK.value: 8,
        PromotionStage.SPEC.value: 9,
        PromotionStage.IDEA.value: 10,
    }
    return order.get(str(stage or ""), 999)


def _evidence_score(lineage: LineageRecord) -> Tuple[int, float, float]:
    payload = lineage.to_dict()
    trade_count = int(
        payload.get("live_paper_trade_count")
        or payload.get("paper_trade_count")
        or payload.get("trade_count")
        or 0
    )
    roi = float(
        payload.get("live_paper_roi_pct")
        or payload.get("paper_roi_pct")
        or payload.get("monthly_roi_pct")
        or 0.0
    )
    fitness = float(payload.get("fitness_score", 0.0) or 0.0)
    return trade_count, roi, fitness


def _keeper_for_family(family: FactoryFamily, lineages: List[LineageRecord]) -> LineageRecord | None:
    active = [lineage for lineage in lineages if lineage.active]
    paper_champion = next(
        (
            lineage
            for lineage in active
            if lineage.lineage_id == family.champion_lineage_id and lineage.current_stage == PromotionStage.PAPER.value
        ),
        None,
    )
    if paper_champion is not None:
        return paper_champion
    current_champion = next((lineage for lineage in lineages if lineage.lineage_id == family.champion_lineage_id), None)
    if current_champion is not None:
        return current_champion
    if active:
        return sorted(
            active,
            key=lambda lineage: (
                _stage_rank(lineage.current_stage),
                -_evidence_score(lineage)[0],
                -_evidence_score(lineage)[1],
                -_evidence_score(lineage)[2],
            ),
        )[0]
    if lineages:
        return sorted(
            lineages,
            key=lambda lineage: (
                _stage_rank(lineage.current_stage),
                -_evidence_score(lineage)[0],
                -_evidence_score(lineage)[1],
                -_evidence_score(lineage)[2],
            ),
        )[0]
    return None


def _family_metadata(family_id: str, current: Dict[str, object]) -> Dict[str, object]:
    merged = dict(current or {})
    merged.update({k: v for k, v in default_family_inventor_metadata(family_id).items() if k not in merged})
    merged.update({k: v for k, v in default_family_promotion_metadata(family_id).items() if k not in merged})
    return merged


def cleanup_single_lineage_policy(registry: FactoryRegistry, *, dry_run: bool = True) -> List[Dict[str, object]]:
    changes: List[Dict[str, object]] = []
    now_iso = utc_now_iso()
    lineages_by_family: Dict[str, List[LineageRecord]] = {}
    for lineage in registry.lineages():
        lineages_by_family.setdefault(lineage.family_id, []).append(lineage)

    for family in registry.families():
        family_lineages = lineages_by_family.get(family.family_id, [])
        keeper = _keeper_for_family(family, family_lineages)
        if keeper is None:
            continue
        retired_ids: List[str] = []
        keeper_was_active = bool(
            keeper.active
            and not keeper.retired_at
            and not keeper.retirement_reason
            and str(keeper.iteration_status or "").strip().lower() != "retired"
        )
        for lineage in family_lineages:
            if lineage.lineage_id == keeper.lineage_id:
                lineage.active = keeper_was_active
                if keeper_was_active:
                    lineage.retired_at = None
                    lineage.retirement_reason = None
                lineage.role = "champion"
                if keeper_was_active:
                    lineage.iteration_status = "champion" if lineage.current_stage == PromotionStage.PAPER.value else str(lineage.iteration_status or "champion")
                if not dry_run:
                    registry.save_lineage(lineage)
                continue
            if lineage.active or not lineage.retired_at:
                lineage.active = False
                lineage.retired_at = now_iso
                lineage.retirement_reason = "single_lineage_policy_cleanup"
                lineage.iteration_status = "retired"
                retired_ids.append(lineage.lineage_id)
                if not dry_run:
                    registry.save_lineage(lineage)
        family.champion_lineage_id = keeper.lineage_id
        family.paper_challenger_ids = []
        family.shadow_challenger_ids = []
        family.retired_lineage_ids = sorted(set(list(family.retired_lineage_ids or []) + retired_ids))
        family.metadata = _family_metadata(family.family_id, family.metadata)
        if not dry_run:
            registry.save_family(family)
        changes.append(
            {
                "family_id": family.family_id,
                "kept_champion_lineage_id": keeper.lineage_id,
                "retired_lineage_ids": retired_ids,
            }
        )
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description="Retire extra family variants and keep one active champion per family.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    registry = FactoryRegistry(PROJECT_ROOT / "data" / "factory")
    changes = cleanup_single_lineage_policy(registry, dry_run=args.dry_run)
    for item in changes:
        print(f"{item['family_id']}: keep={item['kept_champion_lineage_id']} retire={','.join(item['retired_lineage_ids']) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
