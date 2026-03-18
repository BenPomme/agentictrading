"""Forced agent exercise — dispatches real mobkit agent tasks for validation.

Exercises the three primary factory runtime methods:
  1. generate_proposal (mob workflow: lead-researcher + cheap-reviewer)
  2. critique_post_evaluation (mob workflow: standard-worker + cheap-reviewer)
  3. design_model (single member: code-author)

All tasks go through the actual MobkitOrchestratorBackend, not mocks.
Execution remains disabled (paper=false, live=false).

Outputs: artifacts/forced_agent_exercise_result.json

Usage:
    .venv312/bin/python scripts/forced_agent_exercise.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Environment must be set BEFORE config import.
os.environ["AGENTIC_FACTORY_MODE"] = "full"
os.environ["FACTORY_REAL_AGENTS_ENABLED"] = "true"
os.environ["FACTORY_RUNTIME_BACKEND"] = "mobkit"
os.environ["FACTORY_ENABLE_GOLDFISH_PROVENANCE"] = "true"
# Safety: execution stays disabled.
os.environ["FACTORY_ENABLE_PAPER_TRADING"] = "false"
os.environ["FACTORY_ENABLE_LIVE_TRADING"] = "false"
os.environ["FACTORY_EXECUTION_AUTOSTART_ENABLED"] = "false"

import config  # noqa: E402

ARTIFACTS = REPO_ROOT / "artifacts"
ARTIFACTS.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trace_id() -> str:
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Synthetic context for each task type
# ---------------------------------------------------------------------------

PROPOSAL_CONTEXT = {
    "family_id": "exercise_funding_contraction",
    "asset_class": "crypto_perpetuals",
    "venue": "binance_futures",
    "data_available": [
        "8h funding rate history (90 days)",
        "open interest by exchange",
        "liquidation cascades (last 30 days)",
    ],
    "objective": "Find a statistically exploitable pattern in funding rate mean-reversion after extreme contraction events.",
}

PROPOSAL_SCHEMA = {
    "hypothesis": "string — falsifiable thesis",
    "entry_signal": "string — precise trigger",
    "exit_signal": "string — precise exit condition",
    "expected_edge_bps": "number — estimated edge in basis points",
    "max_drawdown_pct": "number — worst expected drawdown %",
    "confidence": "number 0-1",
    "data_requirements": "list of strings",
}

CRITIQUE_CONTEXT = {
    "family_id": "exercise_funding_contraction",
    "lineage_id": "exercise_funding_contraction:challenger:1",
    "hypothesis": "Funding rate snap-back after >0.1% 8h rate predicts 40bps reversion within 3 funding intervals.",
    "backtest_summary": {
        "period": "2024-06-01 to 2025-12-31",
        "trades": 127,
        "win_rate": 0.63,
        "sharpe": 1.42,
        "max_drawdown_pct": 6.8,
        "monthly_roi_pct": 4.2,
        "best_month_pct": 11.3,
        "worst_month_pct": -3.1,
    },
    "stress_test": {
        "regime_breaks": 2,
        "max_consecutive_losses": 7,
        "recovery_days_avg": 4.2,
    },
}

CRITIQUE_SCHEMA = {
    "overall_assessment": "string — pass / conditional_pass / fail",
    "overfitting_risk": "string — low / medium / high",
    "regime_fragility": "string — low / medium / high",
    "issues": "list of {severity: string, description: string}",
    "recommendation": "string — promote / rework / retire",
}

DESIGN_CONTEXT = {
    "family_id": "exercise_funding_contraction",
    "hypothesis": "Funding rate snap-back after >0.1% 8h rate predicts 40bps reversion within 3 funding intervals.",
    "target_instrument": "BTCUSDT perpetual",
    "data_fields": [
        "funding_rate_8h",
        "open_interest_usd",
        "mark_price",
        "liquidation_volume_24h",
    ],
    "constraints": [
        "Must be deterministic (no random state)",
        "Must implement BaseStrategy interface with generate_signal(row) -> Signal",
        "Must include feature engineering in a features() method",
    ],
}

DESIGN_SCHEMA = {
    "module_code": "string — complete Python module source",
    "class_name": "string — strategy class name",
    "dependencies": "list of strings — pip packages required",
}


# ---------------------------------------------------------------------------
# Exercise runner
# ---------------------------------------------------------------------------

def run_exercise() -> dict:
    """Run all three task types through the real mobkit backend."""
    from factory.runtime.mobkit_backend import MobkitOrchestratorBackend

    results: list[dict] = []
    overall_start = time.monotonic()

    # --- Initialize backend ---
    print("  Initializing MobkitOrchestratorBackend...", flush=True)
    backend = MobkitOrchestratorBackend.create(REPO_ROOT)
    backend.initialize()
    print(f"  Backend healthy: {backend.healthcheck()}")

    # --- Task 1: generate_proposal (mob workflow) ---
    print("\n  [1/3] generate_proposal ...", end="", flush=True)
    t0 = time.monotonic()
    task1: dict = {
        "task_type": "proposal_generation",
        "runtime_method": "run_mob_workflow",
        "workflow_name": "proposal_generation",
        "success": False,
        "error": None,
        "output_snippet": None,
        "member_traces": [],
        "duration_ms": 0,
        "started_at": _now(),
    }
    try:
        r = backend.run_mob_workflow(
            workflow_name="proposal_generation",
            role_definitions=[],
            shared_context=PROPOSAL_CONTEXT,
            output_schema=PROPOSAL_SCHEMA,
            trace_id=_trace_id(),
            family_id="exercise_funding_contraction",
            lineage_id="exercise_funding_contraction:challenger:1",
            timeout_seconds=180,
        )
        task1["success"] = True
        task1["output_snippet"] = json.dumps(r.get("payload", {}), default=str)[:500]
        task1["member_traces"] = r.get("member_traces", [])
    except Exception as exc:
        task1["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    task1["duration_ms"] = int((time.monotonic() - t0) * 1000)
    task1["finished_at"] = _now()
    status1 = "OK" if task1["success"] else f"FAIL({(task1['error'] or '?')[:60]})"
    print(f" {status1} ({task1['duration_ms']}ms)")
    results.append(task1)

    # --- Task 2: critique_post_evaluation (mob workflow) ---
    print("  [2/3] critique_post_evaluation ...", end="", flush=True)
    t0 = time.monotonic()
    task2: dict = {
        "task_type": "critique_post_evaluation",
        "runtime_method": "run_mob_workflow",
        "workflow_name": "post_eval_critique",
        "success": False,
        "error": None,
        "output_snippet": None,
        "member_traces": [],
        "duration_ms": 0,
        "started_at": _now(),
    }
    try:
        r = backend.run_mob_workflow(
            workflow_name="post_eval_critique",
            role_definitions=[],
            shared_context=CRITIQUE_CONTEXT,
            output_schema=CRITIQUE_SCHEMA,
            trace_id=_trace_id(),
            family_id="exercise_funding_contraction",
            lineage_id="exercise_funding_contraction:challenger:1",
            timeout_seconds=120,
        )
        task2["success"] = True
        task2["output_snippet"] = json.dumps(r.get("payload", {}), default=str)[:500]
        task2["member_traces"] = r.get("member_traces", [])
    except Exception as exc:
        task2["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    task2["duration_ms"] = int((time.monotonic() - t0) * 1000)
    task2["finished_at"] = _now()
    status2 = "OK" if task2["success"] else f"FAIL({(task2['error'] or '?')[:60]})"
    print(f" {status2} ({task2['duration_ms']}ms)")
    results.append(task2)

    # --- Task 3: design_model (single member, code-author) ---
    print("  [3/3] design_model ...", end="", flush=True)
    t0 = time.monotonic()
    task3: dict = {
        "task_type": "model_design",
        "runtime_method": "run_structured_task",
        "workflow_name": "model_design",
        "success": False,
        "error": None,
        "output_snippet": None,
        "member_traces": [],
        "duration_ms": 0,
        "started_at": _now(),
    }
    try:
        prompt = (
            f"Design a trading strategy model.\n\n"
            f"Context:\n{json.dumps(DESIGN_CONTEXT, indent=2)}\n\n"
            f"Output schema:\n{json.dumps(DESIGN_SCHEMA, indent=2)}"
        )
        r = backend.run_structured_task(
            task_type="model_design",
            prompt=prompt,
            schema=DESIGN_SCHEMA,
            model_tier="tier_codegen",
            family_id="exercise_funding_contraction",
            lineage_id="exercise_funding_contraction:challenger:1",
            trace_id=_trace_id(),
            max_tokens=4096,
            timeout_seconds=120,
        )
        task3["success"] = True
        # Truncate module_code in snippet for readability
        payload = r.get("payload", {})
        snippet_payload = dict(payload)
        if "module_code" in snippet_payload:
            code = snippet_payload["module_code"]
            snippet_payload["module_code"] = code[:200] + "..." if len(code) > 200 else code
        task3["output_snippet"] = json.dumps(snippet_payload, default=str)[:500]
        task3["member_traces"] = r.get("member_traces", [])
    except Exception as exc:
        task3["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    task3["duration_ms"] = int((time.monotonic() - t0) * 1000)
    task3["finished_at"] = _now()
    status3 = "OK" if task3["success"] else f"FAIL({(task3['error'] or '?')[:60]})"
    print(f" {status3} ({task3['duration_ms']}ms)")
    results.append(task3)

    total_ms = int((time.monotonic() - overall_start) * 1000)
    passed = sum(1 for r in results if r["success"])

    # --- Goldfish lineage: write provenance for each completed task, then verify readback ---
    goldfish_proof: dict = {"enabled": False, "records_written": 0, "records_read_back": 0, "thoughts": []}
    try:
        from factory.provenance.goldfish_client import GoldfishClient
        gf = GoldfishClient(REPO_ROOT)
        if gf.healthcheck():
            goldfish_proof["enabled"] = True
            ws_id = "exercise_funding_contraction"
            gf.ensure_workspace(workspace_id=ws_id, thesis="Forced agent exercise workspace")

            for r in results:
                if not r["success"]:
                    continue
                run_id = f"exercise:{r['task_type']}:{r.get('started_at', 'unknown')}"
                gf.create_run(workspace_id=ws_id, run_id=run_id, metadata={
                    "task_type": r["task_type"],
                    "runtime_method": r["runtime_method"],
                    "duration_ms": r["duration_ms"],
                })
                gf.finalize_run(workspace_id=ws_id, run_id=run_id, result={
                    "success": r["success"],
                    "output_snippet": (r.get("output_snippet") or "")[:200],
                    "member_traces": r.get("member_traces", []),
                })
                goldfish_proof["records_written"] += 1

            # Verify readback: log_thought writes to audit trail, read via list_thoughts
            thoughts = gf.list_thoughts(workspace_id=ws_id, limit=20)
            goldfish_proof["records_read_back"] = len(thoughts)
            for t in thoughts[:5]:
                goldfish_proof["thoughts"].append({
                    "timestamp": t.get("timestamp", "?"),
                    "snippet": str(t.get("thought", ""))[:150],
                })
            print(f"\n  Goldfish: wrote {goldfish_proof['records_written']}, "
                  f"read back {goldfish_proof['records_read_back']} thoughts")
    except Exception as exc:
        goldfish_proof["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        print(f"\n  Goldfish error: {goldfish_proof['error']}")

    return {
        "as_of": _now(),
        "backend": "mobkit",
        "gateway_bin": getattr(config, "FACTORY_MOBKIT_GATEWAY_BIN", ""),
        "mob_config": getattr(config, "FACTORY_MOBKIT_CONFIG_PATH", ""),
        "execution_disabled": True,
        "tasks_run": len(results),
        "tasks_passed": passed,
        "tasks_failed": len(results) - passed,
        "total_duration_ms": total_ms,
        "results": results,
        "goldfish": goldfish_proof,
    }


def main() -> int:
    gw = str(getattr(config, "FACTORY_MOBKIT_GATEWAY_BIN", "") or "").strip()
    if not gw or not Path(gw).exists():
        print(f"[FAIL] gateway binary not available: {gw!r}", file=sys.stderr)
        return 1

    print("=" * 60)
    print("FORCED AGENT EXERCISE — REAL MOBKIT DISPATCH")
    print(f"  as_of: {_now()}")
    print(f"  backend: mobkit")
    print(f"  gateway: {gw}")
    print("=" * 60)

    try:
        output = run_exercise()
    except Exception:
        print(f"\n[FAIL] exercise crashed:\n{traceback.format_exc()}", file=sys.stderr)
        return 1

    out_path = ARTIFACTS / "forced_agent_exercise_result.json"
    out_path.write_text(json.dumps(output, indent=2, default=str))

    print(f"\n  Passed: {output['tasks_passed']}/{output['tasks_run']}")
    print(f"  Total: {output['total_duration_ms']}ms")
    print(f"  → {out_path.relative_to(REPO_ROOT)}")
    return 0 if output["tasks_failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
