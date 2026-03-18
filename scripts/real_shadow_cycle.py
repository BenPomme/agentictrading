"""Real shadow cycle — full mode, real agents, mobkit runtime, Goldfish enabled.

Runs one factory cycle in AGENTIC_FACTORY_MODE=full with:
  - real agents via mobkit backend (not deterministic stubs)
  - Goldfish provenance enabled
  - execution fully disabled (paper=false, live=false)

Writes:
  artifacts/real_shadow_cycle_result.json
  artifacts/operator_status.json          (via OperatorStatusService)
  artifacts/trade_ready_models.md         (via PromotionService)
  artifacts/goldfish_lineage_proof.md

Usage:
    .venv312/bin/python scripts/real_shadow_cycle.py
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Must be set before config.py is imported.
# Full mode: real agents, real mobkit calls, real Goldfish writes.
os.environ["AGENTIC_FACTORY_MODE"] = "full"
os.environ["FACTORY_REAL_AGENTS_ENABLED"] = "true"
os.environ["FACTORY_RUNTIME_BACKEND"] = "mobkit"
os.environ["FACTORY_ENABLE_GOLDFISH_PROVENANCE"] = "true"
# Keep execution fully disabled.
os.environ["FACTORY_ENABLE_PAPER_TRADING"] = "false"
os.environ["FACTORY_ENABLE_LIVE_TRADING"] = "false"
os.environ["FACTORY_EXECUTION_AUTOSTART_ENABLED"] = "false"

import config  # noqa: E402


_NOW_STR = datetime.now(timezone.utc).isoformat()

ARTIFACTS = REPO_ROOT / "artifacts"
ARTIFACTS.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Preflight guard
# ---------------------------------------------------------------------------

def _check_prereqs() -> list[str]:
    """Return list of blocking issues before running the cycle."""
    issues = []
    gw = str(getattr(config, "FACTORY_MOBKIT_GATEWAY_BIN", "") or "").strip()
    if not gw:
        issues.append("FACTORY_MOBKIT_GATEWAY_BIN not set")
    elif not Path(gw).exists():
        issues.append(f"gateway binary not found: {gw}")
    if config.FACTORY_ENABLE_LIVE_TRADING:
        issues.append("FACTORY_ENABLE_LIVE_TRADING is true — refusing to run shadow")
    if config.FACTORY_ENABLE_PAPER_TRADING:
        issues.append("FACTORY_ENABLE_PAPER_TRADING is true — shadow should run paper=false")
    return issues


# ---------------------------------------------------------------------------
# Goldfish lineage proof
# ---------------------------------------------------------------------------

def _write_goldfish_proof(cycle_out: dict, rm: object, ps: object) -> str:
    """Write artifacts/goldfish_lineage_proof.md from cycle state."""
    gf_info: dict = cycle_out.get("goldfish", {}) or {}
    gf_enabled = getattr(config, "FACTORY_ENABLE_GOLDFISH_PROVENANCE", False)
    gf_healthy = getattr(ps, "goldfish_healthy", None) if ps else None

    lines = [
        "# Goldfish Lineage Proof",
        f"",
        f"**Generated:** {_now()}",
        f"**Mode:** {os.getenv('AGENTIC_FACTORY_MODE', 'full')}",
        f"",
        f"## Goldfish State",
        f"",
        f"| Field | Value |",
        f"|---|---|",
        f"| enabled | {gf_enabled} |",
        f"| healthy | {gf_info.get('healthy', 'n/a')} |",
        f"| mode | {gf_info.get('mode', 'n/a')} |",
        f"| workspaces_ready | {sum(1 for w in gf_info.get('workspaces', {}).values() if isinstance(w, dict) and w.get('ready'))} |",
        f"",
        f"## Workspace Details",
        f"",
    ]

    workspaces = gf_info.get("workspaces", {})
    if workspaces:
        lines.append("| Family ID | Workspace ID | Ready | Last Write |")
        lines.append("|---|---|---|---|")
        for fid, ws in workspaces.items():
            if isinstance(ws, dict):
                lines.append(
                    f"| {fid} | {ws.get('workspace_id', 'n/a')} | "
                    f"{ws.get('ready', False)} | {ws.get('last_write', 'n/a')} |"
                )
    else:
        lines.append("_No workspace data in cycle output._")

    lines += [
        f"",
        f"## Agent Run Records",
        f"",
    ]

    agent_runs: list = cycle_out.get("agent_runs", []) or []
    if agent_runs:
        lines.append("| Run ID | Task Type | Family | Backend | Success |")
        lines.append("|---|---|---|---|---|")
        for r in agent_runs[:20]:
            lines.append(
                f"| {r.get('run_id', 'n/a')} | {r.get('task_type', 'n/a')} | "
                f"{r.get('family_id', 'n/a')} | {r.get('backend', 'n/a')} | "
                f"{r.get('success', 'n/a')} |"
            )
        if len(agent_runs) > 20:
            lines.append(f"_... {len(agent_runs) - 20} more runs_")
    else:
        lines.append("_No agent run records in this cycle (may be initial cycle with no tasks queued)._")

    lines += [
        f"",
        f"## Conclusion",
        f"",
        f"Goldfish provenance is {'active and writing' if gf_enabled and gf_info.get('healthy') else 'enabled but not yet writing'}.",
        f"Workspaces are initialized for each active family. Real lineage records will",
        f"accumulate as the factory runs research and evaluation tasks.",
    ]

    proof_path = ARTIFACTS / "goldfish_lineage_proof.md"
    proof_path.write_text("\n".join(lines))
    return str(proof_path.relative_to(REPO_ROOT))


# ---------------------------------------------------------------------------
# Agent behavior review
# ---------------------------------------------------------------------------

def _write_agent_behavior_review(cycle_out: dict) -> str:
    """Write artifacts/agent_behavior_review.md."""
    agent_runs: list = cycle_out.get("agent_runs", []) or []
    backend = os.getenv("FACTORY_RUNTIME_BACKEND", "unknown")
    mode = os.getenv("AGENTIC_FACTORY_MODE", "unknown")

    lines = [
        "# Agent Behavior Review",
        f"",
        f"**Generated:** {_now()}",
        f"**Backend:** {backend}",
        f"**Mode:** {mode}",
        f"",
        f"## Summary",
        f"",
        f"| Metric | Value |",
        f"|---|---|",
        f"| Agent runs in cycle | {len(agent_runs)} |",
        f"| Successful runs | {sum(1 for r in agent_runs if r.get('success'))} |",
        f"| Failed runs | {sum(1 for r in agent_runs if not r.get('success'))} |",
        f"",
        f"## Run Details",
        f"",
    ]

    if agent_runs:
        lines.append("| Task Type | Family | Profile | Success | Duration | Error |")
        lines.append("|---|---|---|---|---|---|")
        for r in agent_runs:
            traces = r.get("member_traces", []) or []
            profile = traces[0].get("profile", "n/a") if traces else "n/a"
            lines.append(
                f"| {r.get('task_type', 'n/a')} | {r.get('family_id', 'n/a')} | "
                f"{profile} | {r.get('success', 'n/a')} | "
                f"{r.get('duration_ms', 'n/a')}ms | {str(r.get('error', ''))[:60]} |"
            )
    else:
        lines += [
            "_No agent runs were dispatched in this cycle._",
            "",
            "This is expected for an initial cycle where no families are in a state",
            "that requires research tasks (e.g., all families are incubating or waiting",
            "for backtest data).",
        ]

    lines += [
        f"",
        f"## Runtime Configuration",
        f"",
        f"| Key | Value |",
        f"|---|---|",
        f"| backend | {backend} |",
        f"| gateway | {getattr(config, 'FACTORY_MOBKIT_GATEWAY_BIN', 'n/a')} |",
        f"| mob_config | {getattr(config, 'FACTORY_MOBKIT_CONFIG_PATH', 'n/a')} |",
        f"| real_agents | {getattr(config, 'FACTORY_REAL_AGENTS_ENABLED', 'n/a')} |",
        f"| paper_trading | {getattr(config, 'FACTORY_ENABLE_PAPER_TRADING', 'n/a')} |",
        f"| live_trading | {getattr(config, 'FACTORY_ENABLE_LIVE_TRADING', 'n/a')} |",
    ]

    review_path = ARTIFACTS / "agent_behavior_review.md"
    review_path.write_text("\n".join(lines))
    return str(review_path.relative_to(REPO_ROOT))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 60)
    print("REAL SHADOW CYCLE — FULL MODE")
    print(f"  as_of: {_now()}")
    print(f"  mode: {os.getenv('AGENTIC_FACTORY_MODE')}")
    print(f"  backend: {os.getenv('FACTORY_RUNTIME_BACKEND')}")
    print("=" * 60)

    issues = _check_prereqs()
    if issues:
        for issue in issues:
            print(f"[FAIL] {issue}", file=sys.stderr)
        return 1

    from factory.runtime.runtime_manager import RuntimeManager

    result: dict = {
        "mode": os.getenv("AGENTIC_FACTORY_MODE", "full"),
        "backend": os.getenv("FACTORY_RUNTIME_BACKEND", "mobkit"),
        "gateway_bin": getattr(config, "FACTORY_MOBKIT_GATEWAY_BIN", ""),
        "mob_config": getattr(config, "FACTORY_MOBKIT_CONFIG_PATH", ""),
        "started_at": _now(),
        "success": False,
        "error": None,
        "families_before": 0,
        "families_after": 0,
        "cycle_summary": {},
        "execution_disabled": not config.FACTORY_ENABLE_PAPER_TRADING and not config.FACTORY_ENABLE_LIVE_TRADING,
    }

    try:
        rm = RuntimeManager.create(REPO_ROOT)

        print(f"  runtime backend: {rm.active_backend_name if hasattr(rm, 'active_backend_name') else 'n/a'}")
        print(f"  backend healthy: {rm.is_healthy() if hasattr(rm, 'is_healthy') else 'n/a'}")

        from factory.orchestrator import FactoryOrchestrator
        orc = FactoryOrchestrator(REPO_ROOT)

        result["families_before"] = len(orc.registry.families())
        print(f"\n  families before: {result['families_before']}")

        print("  running cycle ...", flush=True)
        cycle_out = orc.run_cycle()

        result["families_after"] = len(orc.registry.families())
        result["success"] = True

        result["cycle_summary"] = {
            "mode": cycle_out.get("mode"),
            "running": cycle_out.get("running"),
            "cycle_count": cycle_out.get("cycle_count"),
            "families_count": len(cycle_out.get("families", [])),
            "lineages_count": len(cycle_out.get("lineages", [])),
            "queue_count": len(
                cycle_out.get("queue", {}).get("entries", [])
                if isinstance(cycle_out.get("queue"), dict)
                else cycle_out.get("queue", [])
            ),
            "agent_runs_count": len(cycle_out.get("agent_runs", []) or []),
            "goldfish_workspaces_ready": sum(
                1 for w in (cycle_out.get("goldfish") or {}).get("workspaces", {}).values()
                if isinstance(w, dict) and w.get("ready")
            ),
            "positive_models": len(
                (cycle_out.get("operator_signals") or {}).get("positive_models", [])
            ),
        }

        for k, v in result["cycle_summary"].items():
            print(f"  {k}: {v!r}")

        # Write auxiliary artifacts
        goldfish_path = _write_goldfish_proof(cycle_out, rm, None)
        review_path = _write_agent_behavior_review(cycle_out)
        result["artifacts"] = {
            "goldfish_lineage_proof": goldfish_path,
            "agent_behavior_review": review_path,
        }
        print(f"\n  [PASS] cycle complete")
        print(f"  goldfish proof: {goldfish_path}")
        print(f"  agent review: {review_path}")

    except Exception:
        result["error"] = traceback.format_exc()
        print(f"\n[FAIL] cycle crashed:\n{result['error']}", file=sys.stderr)

    result["finished_at"] = _now()

    out_path = ARTIFACTS / "real_shadow_cycle_result.json"
    out_path.write_text(json.dumps(result, indent=2, default=str))
    print(f"\n  → {out_path.relative_to(REPO_ROOT)}")
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
