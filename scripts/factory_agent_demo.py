#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))
os.chdir(project_root)

try:
    from dotenv import load_dotenv

    load_dotenv(project_root / ".env", override=True)
except ImportError:
    pass

import config  # noqa: E402
from factory.agent_runtime import recent_agent_runs  # noqa: E402
from factory.orchestrator import FactoryOrchestrator  # noqa: E402


def _family_summary(state: dict, family_id: str) -> dict:
    family_rows = [row for row in (state.get("lineages") or []) if row.get("family_id") == family_id]
    real_rows = [row for row in family_rows if str(row.get("hypothesis_origin") or "").startswith("real_agent_")]
    return {
        "family_id": family_id,
        "lineage_count": len(family_rows),
        "real_agent_lineage_count": len(real_rows),
        "real_agent_lineages": [
            {
                "lineage_id": row.get("lineage_id"),
                "label": row.get("label"),
                "hypothesis_origin": row.get("hypothesis_origin"),
                "latest_agent_decision": row.get("latest_agent_decision") or {},
                "proposal_agent": row.get("proposal_agent") or {},
                "stage": row.get("current_stage"),
                "fitness_score": row.get("fitness_score"),
                "monthly_roi_pct": row.get("monthly_roi_pct"),
            }
            for row in real_rows
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a real-agent demo cycle for one family and print proof artifacts.")
    parser.add_argument("--family", default=str(getattr(config, "FACTORY_AGENT_DEMO_FAMILY", "binance_funding_contrarian")))
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--factory-root")
    parser.add_argument("--goldfish-root")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    temp_root: Path | None = None
    if args.factory_root:
        factory_root = Path(args.factory_root).expanduser().resolve()
    else:
        temp_root = Path(tempfile.mkdtemp(prefix="agentictrading-demo."))
        factory_root = temp_root / "factory"
    if args.goldfish_root:
        goldfish_root = Path(args.goldfish_root).expanduser().resolve()
    else:
        base_root = temp_root if temp_root is not None else factory_root.parent
        goldfish_root = base_root / "goldfish"

    setattr(config, "FACTORY_ROOT", str(factory_root))
    setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    setattr(config, "FACTORY_REAL_AGENTS_ENABLED", True)
    setattr(config, "FACTORY_AGENT_DEMO_FAMILY", str(args.family))
    setattr(config, "FACTORY_AGENT_LOG_DIR", "data/factory/agent_runs")

    orchestrator = FactoryOrchestrator(project_root)
    state = {}
    for _ in range(max(1, int(args.cycles))):
        state = orchestrator.run_cycle()

    family_summary = _family_summary(state, str(args.family))
    payload = {
        "factory_root": str(factory_root),
        "goldfish_root": str(goldfish_root),
        "cycle_count": int(state.get("cycle_count", 0) or 0),
        "research_summary": dict(state.get("research_summary") or {}),
        "family_summary": family_summary,
        "recent_agent_runs": [
            row for row in recent_agent_runs(project_root, limit=20) if row.get("family_id") == str(args.family)
        ][:6],
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"factory_root={payload['factory_root']}")
        print(f"goldfish_root={payload['goldfish_root']}")
        print(f"family={args.family} cycles={payload['cycle_count']}")
        print(f"real_agent_lineages={family_summary['real_agent_lineage_count']} / {family_summary['lineage_count']}")
        for row in family_summary["real_agent_lineages"]:
            print(
                "  - {lineage_id} stage={stage} provider={provider} model={model}".format(
                    lineage_id=row.get("lineage_id"),
                    stage=row.get("stage"),
                    provider=(row.get("proposal_agent") or {}).get("provider", "n/a"),
                    model=(row.get("proposal_agent") or {}).get("model", "n/a"),
                )
            )
        print("recent_agent_runs:")
        for row in payload["recent_agent_runs"]:
            print(
                "  - {task_type} {provider}/{model} success={success} fallback={fallback_used} artifact={artifact_path}".format(
                    **row,
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
