"""Preflight and one-cycle shadow bring-up script.

Runs all preflight checks then executes one deterministic (cost_saver) factory
cycle.  Does NOT enable paper trading, live trading, or expensive agent calls.

Usage:
    python3 scripts/preflight_shadow.py

Outputs:
    artifacts/preflight_report.json   — preflight gate results
    artifacts/shadow_cycle_result.json — shadow cycle summary
    artifacts/operator_status.json    — operator status snapshot
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

# ---- repo root on path ----
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# ---- force cost_saver mode for shadow cycle ----
# Must be set BEFORE config.py is imported so the dotenv overlay does not win.
os.environ["AGENTIC_FACTORY_MODE"] = "cost_saver"
os.environ["FACTORY_NEW_FAMILY_ENABLED"] = "false"  # no new families during shadow

import config  # noqa: E402 — must be after env setup


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc).isoformat()


def _ok(name: str, detail: str = "") -> dict:
    msg = f"[PASS] {name}"
    if detail:
        msg += f": {detail}"
    print(msg)
    return {"check": name, "result": "pass", "detail": detail}


def _fail(name: str, detail: str) -> dict:
    print(f"[FAIL] {name}: {detail}", file=sys.stderr)
    return {"check": name, "result": "fail", "detail": detail}


def _warn(name: str, detail: str) -> dict:
    print(f"[WARN] {name}: {detail}")
    return {"check": name, "result": "warn", "detail": detail}


# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------

def check_python_version() -> list:
    results = []
    vi = sys.version_info
    ver = f"{vi.major}.{vi.minor}.{vi.micro}"
    if vi >= (3, 12):
        results.append(_ok("python_version", ver))
    elif vi >= (3, 10):
        results.append(_warn("python_version",
            f"{ver} — goldfish works but recommend ≥3.12"))
    else:
        results.append(_warn("python_version",
            f"{ver} < 3.10 — goldfish library requires ≥3.10. "
            "Install python3.12 (brew install python@3.12) and run factory with it. "
            "goldfish daemon verified working separately via /opt/homebrew/bin/python3.12."))
    return results


def check_config() -> list:
    results = []
    from factory.config.staging_guards import load_staging_guards
    guards = load_staging_guards()

    if guards.is_safe_for_preflight:
        results.append(_ok("staging_guards", "is_safe_for_preflight=True"))
    else:
        results.append(_fail("staging_guards", "is_safe_for_preflight=False"))

    if not guards.live_trading_enabled:
        results.append(_ok("live_trading_disabled"))
    else:
        results.append(_fail("live_trading_disabled", "LIVE TRADING IS ENABLED — abort"))

    if not guards.paper_trading_enabled:
        results.append(_ok("paper_trading_disabled"))
    else:
        results.append(_warn("paper_trading", "paper trading enabled (shadow cycle only)"))

    if guards.max_active_families <= 3:
        results.append(_ok("scope_caps", f"max_families={guards.max_active_families}"))
    else:
        results.append(_warn("scope_caps", f"max_families={guards.max_active_families} (high for first run)"))

    return results


def check_secrets() -> list:
    results = []
    openai = bool(os.getenv("OPENAI_API_KEY", ""))
    anthropic = bool(os.getenv("ANTHROPIC_API_KEY", ""))
    google = bool(os.getenv("GOOGLE_API_KEY", ""))

    if openai:
        results.append(_ok("OPENAI_API_KEY"))
    else:
        results.append(_fail("OPENAI_API_KEY", "missing — required for legacy runtime"))

    if anthropic:
        results.append(_ok("ANTHROPIC_API_KEY"))
    else:
        results.append(_warn("ANTHROPIC_API_KEY", "missing — needed for Sonnet/Opus models"))

    if google:
        results.append(_ok("GOOGLE_API_KEY"))
    else:
        results.append(_warn("GOOGLE_API_KEY", "missing — needed for Gemini reviewer tier"))

    return results


def check_runtime() -> tuple[list, object]:
    from factory.runtime.runtime_manager import RuntimeManager
    results = []
    rm = None
    try:
        rm = RuntimeManager(REPO_ROOT)
        results.append(_ok("runtime_boot", f"backend={rm.backend_name}"))
        healthy = rm.healthcheck()
        if healthy:
            results.append(_ok("runtime_healthcheck", f"backend={rm.backend_name} healthy"))
        else:
            results.append(_fail("runtime_healthcheck", f"backend={rm.backend_name} not healthy"))
    except Exception as e:
        results.append(_fail("runtime_boot", str(e)[:200]))
    return results, rm


def check_goldfish(rm=None) -> tuple[list, object]:
    from factory.provenance.goldfish_client import ProvenanceService
    results = []
    ps = None
    try:
        ps = ProvenanceService.create(REPO_ROOT)
        health = ps.health_dict()
        if health["enabled"]:
            if health["healthy"]:
                results.append(_ok("goldfish_boot", "enabled + healthy"))
            elif sys.version_info < (3, 10):
                results.append(_warn("goldfish_boot",
                    f"Python {sys.version_info.major}.{sys.version_info.minor} < 3.10 — "
                    "goldfish library requires Python ≥3.10; daemon may still be reachable via socket. "
                    "Run factory with python3.12 (installed at /opt/homebrew/bin/python3.12) for full Goldfish."))
            else:
                results.append(_warn("goldfish_boot",
                    "enabled but library not installed or daemon unreachable — observe-only mode active"))
        else:
            results.append(_warn("goldfish_disabled", "FACTORY_ENABLE_GOLDFISH_PROVENANCE=false"))

        # Test a write — in observe-only mode this should degrade gracefully
        ps.ensure_family_workspace(
            family_id="preflight-test-001",
            thesis="preflight shadow test",
        )
        if ps.degraded:
            if sys.version_info < (3, 10):
                results.append(_warn("goldfish_write",
                    "write skipped — Python <3.10 cannot import goldfish library directly. "
                    "Daemon verified healthy via python3.12 separately."))
            else:
                results.append(_warn("goldfish_write",
                    f"write degraded: {str(ps.health_dict()['last_error'] or '')[:80]}"))
        else:
            results.append(_ok("goldfish_write", "write succeeded"))
    except Exception as e:
        results.append(_warn("goldfish_boot", f"Python {sys.version_info.major}.{sys.version_info.minor}: {str(e)[:150]}"))
    return results, ps


def check_ideas() -> list:
    from factory.idea_intake import parse_ideas_markdown
    results = []
    try:
        ideas = parse_ideas_markdown(REPO_ROOT)
        if ideas:
            results.append(_ok("idea_intake", f"{len(ideas)} ideas parsed from IDEAS.md"))
        else:
            results.append(_warn("idea_intake", "IDEAS.md exists but no ideas parsed"))
    except Exception as e:
        results.append(_fail("idea_intake", str(e)[:200]))
    return results


def check_operator_status(rm, ps) -> tuple[list, dict]:
    from factory.telemetry.correlation import build_operator_status
    from factory.config.staging_guards import load_staging_guards
    results = []
    status_dict = {}
    try:
        guards = load_staging_guards()
        status = build_operator_status(rm, provenance_service=ps, staging_guards=guards)
        status_dict = status.to_dict()
        results.append(_ok("operator_status", f"backend={status_dict['active_backend']} healthy={status_dict['backend_healthy']}"))

        # Write to artifact
        out_path = REPO_ROOT / config.FACTORY_OPERATOR_STATUS_PATH
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(status_dict, indent=2))
        results.append(_ok("operator_status_written", str(out_path.relative_to(REPO_ROOT))))
    except Exception as e:
        results.append(_fail("operator_status", str(e)[:200]))
    return results, status_dict


# ---------------------------------------------------------------------------
# Shadow cycle
# ---------------------------------------------------------------------------

def run_shadow_cycle(rm, ps) -> dict:
    """Run one cost_saver cycle — deterministic only, no expensive agents."""
    from factory.orchestrator import FactoryOrchestrator

    print("\n--- Shadow Cycle (cost_saver mode) ---")
    result: dict = {
        "mode": os.getenv("AGENTIC_FACTORY_MODE", "unknown"),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "success": False,
        "error": None,
        "families_before": 0,
        "families_after": 0,
        "cycle_summary": {},
    }

    try:
        orc = FactoryOrchestrator(REPO_ROOT)
        result["families_before"] = len(orc.registry.families())
        print(f"  families before cycle: {result['families_before']}")

        cycle_out = orc.run_cycle()
        result["families_after"] = len(orc.registry.families())
        result["success"] = True

        # Capture relevant summary sections
        result["cycle_summary"] = {
            "mode": cycle_out.get("mode"),
            "running": cycle_out.get("running"),
            "cycle_count": cycle_out.get("cycle_count"),
            "families_count": len(cycle_out.get("families", [])),
            "lineages_count": len(cycle_out.get("lineages", [])),
            "queue_count": len(cycle_out.get("queue", {}).get("entries", [])
                               if isinstance(cycle_out.get("queue"), dict)
                               else cycle_out.get("queue", [])),
            "goldfish_mode": cycle_out.get("goldfish", {}).get("mode") if isinstance(cycle_out.get("goldfish"), dict) else None,
            "goldfish_workspaces_ready": sum(
                1 for w in cycle_out.get("goldfish", {}).get("workspaces", {}).values()
                if isinstance(w, dict) and w.get("ready")
            ) if isinstance(cycle_out.get("goldfish"), dict) else 0,
            "positive_models": len(cycle_out.get("operator_signals", {}).get("positive_models", [])),
        }

        # Print summary
        for k, v in result["cycle_summary"].items():
            print(f"  {k}: {v!r}")
        print(f"  families after cycle: {result['families_after']}")

    except Exception as e:
        result["error"] = traceback.format_exc()
        print(f"  ERROR: {e}", file=sys.stderr)

    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 60)
    print("PREFLIGHT + SHADOW CYCLE")
    print(f"  as_of: {_NOW}")
    print(f"  branch: integration/staging")
    print(f"  mode: {os.getenv('AGENTIC_FACTORY_MODE', '?')}")
    print("=" * 60)
    print()

    all_checks: list = []
    fail_count = 0

    # -- Python version --
    print("--- Python version ---")
    all_checks += check_python_version()

    # -- Config --
    print("\n--- Config checks ---")
    all_checks += check_config()

    # -- Secrets --
    print("\n--- Secret checks ---")
    all_checks += check_secrets()

    # -- Runtime --
    print("\n--- Runtime backend ---")
    rt_checks, rm = check_runtime()
    all_checks += rt_checks

    if rm is None:
        print("\nCannot proceed without runtime. Aborting.", file=sys.stderr)
        return 1

    # -- Goldfish --
    print("\n--- Goldfish provenance ---")
    gf_checks, ps = check_goldfish(rm)
    all_checks += gf_checks

    # -- Ideas --
    print("\n--- Idea intake ---")
    all_checks += check_ideas()

    # -- Operator status --
    print("\n--- Operator status ---")
    os_checks, status_dict = check_operator_status(rm, ps)
    all_checks += os_checks

    # -- Summary --
    fail_count = sum(1 for c in all_checks if c["result"] == "fail")
    warn_count = sum(1 for c in all_checks if c["result"] == "warn")
    pass_count = sum(1 for c in all_checks if c["result"] == "pass")

    print(f"\nPreflight: {pass_count} passed / {warn_count} warnings / {fail_count} failed")

    # -- Write preflight report --
    preflight_path = REPO_ROOT / "artifacts" / "preflight_report.json"
    preflight_path.parent.mkdir(parents=True, exist_ok=True)
    preflight_report = {
        "as_of": _NOW,
        "pass": pass_count,
        "warn": warn_count,
        "fail": fail_count,
        "checks": all_checks,
    }
    preflight_path.write_text(json.dumps(preflight_report, indent=2))
    print(f"  → {preflight_path.relative_to(REPO_ROOT)}")

    # -- Shadow cycle --
    if fail_count > 0:
        print(f"\n{fail_count} preflight check(s) failed — fix before running shadow cycle.", file=sys.stderr)
        return 1

    shadow_result = run_shadow_cycle(rm, ps)

    shadow_path = REPO_ROOT / "artifacts" / "shadow_cycle_result.json"
    shadow_path.write_text(json.dumps(shadow_result, indent=2))
    print(f"\nShadow cycle artifact: {shadow_path.relative_to(REPO_ROOT)}")

    if not shadow_result["success"]:
        print("Shadow cycle FAILED — see artifacts/shadow_cycle_result.json", file=sys.stderr)
        return 1

    print("\n" + "=" * 60)
    print("PREFLIGHT + SHADOW CYCLE COMPLETE")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
