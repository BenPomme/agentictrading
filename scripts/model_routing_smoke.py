"""Model routing smoke validation for MobKit profiles.

Key architectural discovery (2026-03-17):
  The rpc_gateway Rust binary dispatches agent events only when it receives
  RPC calls via its stdin/stdout transport. Without periodic RPC activity,
  agent completions sit buffered in the Rust async runtime and never reach
  the SSE stream. The fix: maintain a background poll task (handle.status()
  every 1s) while collecting events via subscribe_mob().

Profiles validated:
  Direct: standard-worker, lead-researcher, code-author, cheap-reviewer, code-mutator
  Workflow: cheap-reviewer as reviewer in Lead(lead-researcher)+Reviewer mob

Outputs: artifacts/model_routing_smoke.json

Usage:
    .venv312/bin/python scripts/model_routing_smoke.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("FACTORY_RUNTIME_BACKEND", "mobkit")
os.environ.setdefault("FACTORY_ENABLE_PAPER_TRADING", "false")
os.environ.setdefault("FACTORY_ENABLE_LIVE_TRADING", "false")
os.environ.setdefault("AGENTIC_FACTORY_MODE", "full")
os.environ.setdefault("FACTORY_REAL_AGENTS_ENABLED", "true")

import config  # noqa: E402

DIRECT_PROFILES = [
    "cheap-reviewer",
    "standard-worker",
    "lead-researcher",
    "code-author",
    "code-mutator",
]

PROFILE_PROMPTS: dict[str, str] = {
    "cheap-reviewer": 'Reply ONLY with this JSON: {"approved": true, "issues": []}',
    "standard-worker": 'Reply ONLY with this JSON: {"result": "ok"}',
    "lead-researcher": 'Reply ONLY with this JSON: {"thesis": "test", "confidence": 0.9}',
    "code-author": 'Reply ONLY with this JSON: {"module_code": "# smoke", "class_name": "Smoke"}',
    "code-mutator": 'Reply ONLY with this JSON: {"module_code": "# mutated"}',
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def run_smoke(gateway_bin: str, mob_config_path: str) -> dict:
    from meerkat_mobkit import MobKit  # type: ignore[import]
    from meerkat_mobkit.events import RunCompleted, RunFailed  # type: ignore[import]

    print(f"  gateway: {gateway_bin}")
    print(f"  mob_config: {mob_config_path}")

    builder = MobKit.builder().gateway(gateway_bin)
    if mob_config_path and Path(mob_config_path).exists():
        builder = builder.mob(mob_config_path)

    runtime = await builder.build()
    handle = runtime.mob_handle()

    try:
        caps = await handle.capabilities()
        print(f"  capabilities: profiles={getattr(caps, 'profiles', '?')}")
    except Exception as exc:
        print(f"  capabilities check failed: {exc}")

    # -----------------------------------------------------------------------
    # Background poll task: keeps the Rust event loop flushing events
    # Without this, agent completions sit buffered until the next RPC call.
    # -----------------------------------------------------------------------
    async def _poll_loop() -> None:
        while True:
            try:
                await handle.status()
            except Exception:
                pass
            await asyncio.sleep(0.8)

    poll_task = asyncio.create_task(_poll_loop())

    # -----------------------------------------------------------------------
    # Subscribe to mob-wide stream: all events from all members arrive here.
    # Using subscribe_mob() avoids per-agent SSE connection management.
    # -----------------------------------------------------------------------
    pending: dict[str, asyncio.Future] = {}   # member_id → Future[str | Exception]
    mob_events_by_member: dict[str, list[str]] = {}

    async def _mob_watcher() -> None:
        async for mob_event in handle.subscribe_mob():
            mid = mob_event.member_id
            ev = mob_event.event
            mob_events_by_member.setdefault(mid, []).append(type(ev).__name__)
            if isinstance(ev, RunCompleted) and mid in pending and not pending[mid].done():
                pending[mid].set_result(str(ev.result))
            elif isinstance(ev, RunFailed) and mid in pending and not pending[mid].done():
                pending[mid].set_exception(RuntimeError(f"RunFailed: {ev.error}"))

    mob_task = asyncio.create_task(_mob_watcher())

    # Allow the SSE subscription and poll loop to establish before sending
    # agent work. The rpc_gateway needs a few poll cycles to stabilize the
    # event dispatch bridge.
    await asyncio.sleep(2.0)

    # -----------------------------------------------------------------------
    # Direct profile tests — all members created upfront, then awaited
    # -----------------------------------------------------------------------
    direct_results = []
    DIRECT_TIMEOUT = 90.0  # seconds per profile
    INSTRUCTIONS_COMMON = [
        "Do not use tools. Do not initiate communication.",
        "Reply ONLY with the exact JSON object you are told to return. No prose.",
    ]

    async def _test_profile(profile: str) -> dict:
        prompt = PROFILE_PROMPTS.get(profile, 'Reply ONLY: {"ok": true}')
        member_id = f"smoke-{profile}-{int(time.time())}"
        result: dict = {
            "profile": profile,
            "test_mode": "direct",
            "member_id": member_id,
            "backend": "mobkit",
            "provider": "mobkit/rpc_gateway",
            "model": None,
            "ensure_member_ok": False,
            "send_ok": False,
            "collect_ok": False,
            "success": False,
            "output_snippet": None,
            "error": None,
            "duration_ms": 0,
            "started_at": _now(),
        }
        t0 = time.monotonic()

        try:
            snap = await handle.ensure_member(
                member_id,
                profile,
                additional_instructions=INSTRUCTIONS_COMMON,
            )
            result["ensure_member_ok"] = True
            if hasattr(snap, "profile_name"):
                result["model"] = f"mobkit/{snap.profile_name}"
            elif hasattr(snap, "model"):
                result["model"] = snap.model

            fut: asyncio.Future[str] = asyncio.get_event_loop().create_future()
            pending[member_id] = fut

            await handle.send(member_id, prompt)
            result["send_ok"] = True

            raw = await asyncio.wait_for(asyncio.shield(fut), timeout=DIRECT_TIMEOUT)
            result["collect_ok"] = True
            result["output_snippet"] = raw[:300] if raw else "(empty)"
            result["success"] = True

        except asyncio.TimeoutError:
            result["error"] = f"timeout waiting for RunCompleted after {DIRECT_TIMEOUT:.0f}s"
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"

        result["duration_ms"] = int((time.monotonic() - t0) * 1000)
        result["finished_at"] = _now()
        return result

    for profile in DIRECT_PROFILES:
        print(f"  [direct] {profile} ...", end="", flush=True)
        r = await _test_profile(profile)
        status = "OK" if r["success"] else f"FAIL({(r['error'] or '?')[:60]})"
        print(f" {status} ({r['duration_ms']}ms)")
        direct_results.append(r)

    # -----------------------------------------------------------------------
    # Mob workflow test: cheap-reviewer as reviewer in Lead+Reviewer pattern
    # -----------------------------------------------------------------------
    print("  [mob_workflow] lead-researcher → cheap-reviewer → synthesis ...", end="", flush=True)
    workflow_result: dict = {
        "workflow": "proposal_generation_mini",
        "test_mode": "mob_workflow",
        "backend": "mobkit",
        "lead_profile": "lead-researcher",
        "reviewer_profile": "cheap-reviewer",
        "lead_ok": False,
        "reviewer_ok": False,
        "synthesis_ok": False,
        "success": False,
        "output_snippet": None,
        "error": None,
        "duration_ms": 0,
        "started_at": _now(),
    }
    wf_t0 = time.monotonic()
    trace_id = str(int(time.time()))
    lead_id = f"smoke-mob-lead-{trace_id}"
    rev_id = f"smoke-mob-rev-{trace_id}"

    try:
        # Register futures for both members
        lead_fut: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        pending[lead_id] = lead_fut

        await handle.ensure_member(
            lead_id, "lead-researcher",
            additional_instructions=[
                "You are a lead researcher generating a strategy proposal.",
                'Reply ONLY: {"hypothesis": "test", "confidence": 0.8}',
            ],
        )
        await handle.send(lead_id, 'Produce: {"hypothesis": "test", "confidence": 0.8}')
        lead_draft = await asyncio.wait_for(asyncio.shield(lead_fut), timeout=60.0)
        workflow_result["lead_ok"] = True

        # Now register reviewer future and reset lead future for synthesis
        rev_fut: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        pending[rev_id] = rev_fut
        lead_fut2: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        pending[lead_id] = lead_fut2

        await handle.ensure_member(
            rev_id, "cheap-reviewer",
            additional_instructions=[
                "You are a cheap reviewer approving proposals.",
                'Reply ONLY: {"approved": true, "issues": []}',
            ],
        )
        await handle.send(
            rev_id,
            f'Lead draft:\n{lead_draft[:100]}\n\nReview: {{"approved": true, "issues": []}}',
        )
        review_text = await asyncio.wait_for(asyncio.shield(rev_fut), timeout=90.0)
        workflow_result["reviewer_ok"] = True

        await handle.send(
            lead_id,
            f'Reviewer: {review_text[:100]}\n\nFinal: {{"hypothesis": "test", "confidence": 0.9, "approved": true}}',
        )
        final_text = await asyncio.wait_for(asyncio.shield(lead_fut2), timeout=60.0)
        workflow_result["synthesis_ok"] = True
        workflow_result["output_snippet"] = final_text[:300]
        workflow_result["success"] = True

    except asyncio.TimeoutError:
        workflow_result["error"] = "timeout in mob workflow"
    except Exception as exc:
        workflow_result["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"

    workflow_result["duration_ms"] = int((time.monotonic() - wf_t0) * 1000)
    workflow_result["finished_at"] = _now()
    wf_status = "OK" if workflow_result["success"] else f"FAIL({(workflow_result['error'] or '?')[:60]})"
    print(f" {wf_status} ({workflow_result['duration_ms']}ms)")

    poll_task.cancel()
    mob_task.cancel()
    try:
        await poll_task
    except (asyncio.CancelledError, Exception):
        pass
    try:
        await mob_task
    except (asyncio.CancelledError, Exception):
        pass

    await runtime.shutdown()

    passed_direct = sum(1 for r in direct_results if r["success"])
    return {
        "direct_results": direct_results,
        "workflow_results": [workflow_result],
        "passed_direct": passed_direct,
        "failed_direct": len(direct_results) - passed_direct,
        "workflow_ok": workflow_result["success"],
    }


def main() -> int:
    gateway_bin = str(getattr(config, "FACTORY_MOBKIT_GATEWAY_BIN", "") or "").strip()
    mob_config_raw = str(getattr(config, "FACTORY_MOBKIT_CONFIG_PATH", "") or "").strip()
    mob_config_path = str(REPO_ROOT / mob_config_raw) if mob_config_raw else ""

    if not gateway_bin:
        print("[FAIL] FACTORY_MOBKIT_GATEWAY_BIN not set", file=sys.stderr)
        return 1
    if not Path(gateway_bin).exists():
        print(f"[FAIL] gateway binary not found: {gateway_bin}", file=sys.stderr)
        return 1

    print("=" * 60)
    print("MODEL ROUTING SMOKE VALIDATION")
    print(f"  as_of: {_now()}")
    print("=" * 60)

    try:
        results = asyncio.run(run_smoke(gateway_bin, mob_config_path))
    except Exception:
        print(f"[FAIL] smoke run crashed:\n{traceback.format_exc()}", file=sys.stderr)
        return 1

    passed_direct = results["passed_direct"]
    failed_direct = results["failed_direct"]
    workflow_ok = results["workflow_ok"]

    output = {
        "as_of": _now(),
        "backend": "mobkit",
        "gateway_bin": gateway_bin,
        "mob_config_path": mob_config_path,
        "polling_note": (
            "rpc_gateway dispatches events only when it receives RPC calls. "
            "A background poll task (handle.status() every 0.8s) is required "
            "to keep events flowing via the SSE bridge."
        ),
        "direct_profiles_tested": len(results["direct_results"]),
        "direct_passed": passed_direct,
        "direct_failed": failed_direct,
        "mob_workflow_ok": workflow_ok,
        "overall_success": failed_direct == 0 and workflow_ok,
        "direct_results": results["direct_results"],
        "workflow_results": results["workflow_results"],
        "model_mapping": {
            "cheap-reviewer": "gpt-5.2",
            "standard-worker": "gpt-5.2",
            "lead-researcher": "gpt-5.2",
            "code-author": "gpt-5.2",
            "code-mutator": "gpt-5.2",
        },
    }

    out_path = REPO_ROOT / "artifacts" / "model_routing_smoke.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nDirect: {passed_direct} passed / {failed_direct} failed")
    print(f"Mob workflow: {'OK' if workflow_ok else 'FAIL'}")
    print(f"  → {out_path.relative_to(REPO_ROOT)}")
    return 0 if (failed_direct == 0 and workflow_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
