"""MobkitOrchestratorBackend — canonical mob/workflow orchestration adapter.

Architecture
------------
MobKit is an async-first orchestration runtime that runs a persistent
subprocess gateway (``mobkit-rpc`` binary).  This module provides a sync
wrapper around the async SDK so AgenticTrading's synchronous orchestrator
can call it normally.

Real API facts (meerkat-mobkit v0.4.13 — inspected 2026-03-16):
- ``MobKit.builder().gateway(bin).build()`` → ``MobKitRuntime`` (async ctx mgr)
- ``runtime.mob_handle()`` → ``MobHandle``
- ``handle.ensure_member(member_id, profile, **kwargs)`` → spawn/reuse agent
- ``handle.send(member_id, prompt)`` → dispatch work to agent
- ``handle.subscribe_agent(member_id)`` → ``AsyncIterator[AgentEvent]``
- ``AgentEvent.event`` is a typed event; watch for ``RunCompleted`` / ``RunFailed``
- ``handle.status()`` → ``StatusResult`` (health)
- No built-in ``run_structured_task`` / ``run_mob_workflow`` — those are our
  adapter abstractions over the lower-level member+message API.

Integration approach (Task 03):
- Availability: ``meerkat_mobkit`` importable + gateway binary file exists.
- Async bridge: dedicated daemon background-loop thread; all MobKit calls are
  dispatched via ``asyncio.run_coroutine_threadsafe``.
- One ``MobKitRuntime`` per backend lifetime (persistent connection).
- Workflow patterns: Lead → optional Reviewers → Lead synthesis.

Cost isolation hooks (forward-compat for Task 04):
- Every profile carries ``max_tokens_total`` and per-role ``max_tokens``.
- ``budget_hooks`` parameter is accepted by backend methods but not yet
  enforced; Task 04 will wire in the governance layer here.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import config
from factory.agent_runtime import AgentRunResult
from factory.telemetry.run_logger import default_logger as _tel
from factory.telemetry.trace_context import TraceContext
from factory.contracts import (
    EvaluationBundle,
    FactoryFamily,
    LearningMemoryEntry,
    LineageRecord,
    ResearchHypothesis,
    StrategyGenome,
)
from factory.runtime.runtime_contracts import (
    RuntimeBudgetDecision,
    RuntimeMemberTrace,
    RuntimeUsage,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

_MOBKIT_AVAILABLE: Optional[bool] = None


def _check_mobkit_available() -> bool:
    """Return True if the meerkat_mobkit package is importable (cached once)."""
    global _MOBKIT_AVAILABLE
    if _MOBKIT_AVAILABLE is None:
        try:
            import meerkat_mobkit  # noqa: F401  # type: ignore[import]
            _MOBKIT_AVAILABLE = True
            logger.debug("meerkat_mobkit package found")
        except ImportError:
            _MOBKIT_AVAILABLE = False
            logger.warning(
                "meerkat_mobkit package not installed — mobkit backend unavailable. "
                "Install meerkat-mobkit or keep FACTORY_ENABLE_MOBKIT=false."
            )
    return bool(_MOBKIT_AVAILABLE)


def _enabled_families() -> List[str]:
    raw = str(getattr(config, "FACTORY_AGENT_ENABLED_FAMILIES", "") or "").strip()
    return [item.strip() for item in raw.split(",") if item.strip()]


def _demo_family() -> str:
    return str(getattr(config, "FACTORY_AGENT_DEMO_FAMILY", "binance_funding_contrarian") or "").strip()


def _family_enabled(family_id: str) -> bool:
    if not bool(getattr(config, "FACTORY_REAL_AGENTS_ENABLED", True)):
        return False
    enabled = _enabled_families()
    if enabled:
        return family_id in enabled
    demo_family = _demo_family()
    return not demo_family or family_id == demo_family


def _post_eval_critique_enabled() -> bool:
    return bool(getattr(config, "FACTORY_AGENT_POST_EVAL_CRITIQUE_ENABLED", False))


def _resolve_log_dir(project_root: Path) -> Path:
    configured = str(getattr(config, "FACTORY_AGENT_LOG_DIR", "data/factory/agent_runs") or "").strip()
    if not configured or configured == "data/factory/agent_runs":
        factory_root = Path(getattr(config, "FACTORY_ROOT", "data/factory"))
        if not factory_root.is_absolute():
            factory_root = project_root / factory_root
        path = factory_root / "agent_runs"
    else:
        path = Path(configured)
        if not path.is_absolute():
            path = project_root / path
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MobkitBackendError(Exception):
    """Base error for the mobkit backend adapter."""


class MobkitUnavailableError(MobkitBackendError):
    """Raised when the backend cannot be initialized (missing binary or package)."""


class MobkitWorkflowError(MobkitBackendError):
    """Raised when a workflow fails after all retries."""


class MobkitSchemaError(MobkitBackendError):
    """Raised when schema validation of the workflow output fails."""


# ---------------------------------------------------------------------------
# Workflow profile definitions
# ---------------------------------------------------------------------------

# Maps our cost tiers to meerkat agent profile names.
# These profiles must be defined in the mob.toml or built-in to the gateway.
_TIER_TO_PROFILE: Dict[str, str] = {
    "tier1_cheap":    "cheap-reviewer",
    "tier2_standard": "standard-worker",
    "tier3_lead":     "lead-researcher",
    # Dedicated code profiles — routes code-generation tasks away from
    # thesis/research profiles so each role has a focused prompt surface.
    "tier_codegen":   "code-author",
    "tier_mutate":    "code-mutator",
}

_TIER_TO_MODEL_CLASS: Dict[str, str] = {
    "tier1_cheap":    "TASK_CHEAP",
    "tier2_standard": "TASK_STANDARD",
    "tier3_lead":     "TASK_EXPENSIVE",
    "tier_codegen":   "TASK_STANDARD",
    "tier_mutate":    "TASK_CHEAP",
}

# Per-task-type system prompt overrides.  Keys match factory task_type strings.
# Falling back to the generic instruction set when a key is not present.
_TASK_SYSTEM_PROMPTS: Dict[str, str] = {
    "proposal_generation": (
        "You are a quantitative strategy researcher. Generate differentiated, "
        "falsifiable, tradeable hypotheses grounded in statistical evidence. "
        "Draw inspiration broadly — physics, biology, control theory, information "
        "theory, game theory, network science — but convert cross-domain ideas into "
        "concrete market hypotheses with testable predictions. "
        "Ground ideas in actually reachable assets and venues provided in the context. "
        "Avoid generic low-value ideas unless highly specific and defensible. "
        "Output exactly one JSON object matching the schema."
    ),
    "post_eval_critique": (
        "You are a quantitative risk reviewer. Critically evaluate the provided backtest "
        "results for overfitting, data-snooping bias, and regime fragility. "
        "'No trades' is generally a poor outcome unless the strategy targets rare events "
        "with adequate sample justification. A strong result should aim toward roughly "
        "5% average monthly ROI while remaining realistic about execution costs. "
        "Output exactly one JSON object matching the schema."
    ),
    "model_design": (
        "You are a machine-learning engineer specialising in alpha research. "
        "Implement the researcher's proposed hypothesis as a concrete model — do not "
        "replace it with a different strategy. Design explicit feature engineering "
        "and training regime faithful to the original idea. "
        "Output exactly one JSON object matching the schema."
    ),
    "model_mutation": (
        "You are a parameter-space explorer. Propose targeted, minimal mutations to "
        "the provided model to improve its Sharpe ratio without increasing drawdown. "
        "Document exactly what changed and why. "
        "Output exactly one JSON object matching the schema."
    ),
    "tweak_suggestion": (
        "You are an execution-optimisation specialist. Suggest targeted parameter tweaks "
        "based on the provided performance diagnostics. "
        "Output exactly one JSON object matching the schema."
    ),
    "maintenance_diagnosis": (
        "You are a strategy health analyst. Diagnose the root cause of performance "
        "degradation from the provided metrics and propose a concrete remediation plan. "
        "Output exactly one JSON object matching the schema."
    ),
}


def _task_instructions(*, task_type: str, model_tier: str, schema: Dict[str, Any]) -> List[str]:
    """Build role-specific system instructions for a structured task."""
    task_prompt = _TASK_SYSTEM_PROMPTS.get(
        task_type,
        f"You are a {model_tier} analyst agent for task '{task_type}' in AgenticTrading.",
    )
    return [
        task_prompt,
        "Do not use tools. Do not communicate with peers. Do not discover peers.",
        "Return ONLY a valid JSON object — no prose, no markdown fences.",
        f"Required schema: {json.dumps(schema)}",
    ]


@dataclass
class MemberRoleSpec:
    """Configuration for one member inside a workflow."""
    role: str
    member_id_suffix: str
    instructions: List[str]
    model_tier: str        # tier1_cheap | tier2_standard | tier3_lead
    max_tokens: int
    is_lead: bool = False
    is_required: bool = True  # if False, workflow continues even if member fails


@dataclass
class WorkflowProfile:
    """Named workflow configuration for a factory task type."""
    name: str
    member_roles: List[MemberRoleSpec]
    timeout_seconds: int = 120
    schema_retry_limit: int = 1    # retries on JSON-parse failure
    is_mob: bool = True            # False → single-member structured task


def _build_workflow_profiles() -> Dict[str, WorkflowProfile]:
    """Define all named workflow profiles for AgenticTrading task types."""
    return {
        # ---- Proposal generation (mob: lead + cheap critic) ------------------
        "proposal_generation": WorkflowProfile(
            name="proposal_generation",
            member_roles=[
                MemberRoleSpec(
                    role="lead_researcher",
                    member_id_suffix="lead",
                    instructions=[
                        "You are the lead researcher generating a trading-strategy proposal.",
                        "Generate differentiated, falsifiable, tradeable ideas. Draw inspiration "
                        "broadly from science and mathematics — physics, biology, control theory, "
                        "information theory, game theory — but convert cross-domain ideas into "
                        "concrete market hypotheses, not decorative analogies.",
                        "Ground ideas in actually reachable assets/venues from the runtime context.",
                        "Produce a complete JSON proposal covering: hypothesis, market_regime, "
                        "validation_plan, complexity_estimate, cost_class.",
                    ],
                    model_tier="tier3_lead",
                    max_tokens=2048,
                    is_lead=True,
                    is_required=True,
                ),
                MemberRoleSpec(
                    role="cheap_critic",
                    member_id_suffix="critic",
                    instructions=[
                        "You are a reviewer providing one round of feedback on a trading-strategy proposal.",
                        "Surface important weaknesses but do not supersede the lead researcher's judgment.",
                        "Return a JSON object: {\"flags\": [\"<issue>\", ...], \"severity\": \"low|medium|high\"}.",
                        "Be concise and useful, not authoritarian.",
                    ],
                    model_tier="tier1_cheap",
                    max_tokens=512,
                    is_lead=False,
                    is_required=False,
                ),
            ],
            timeout_seconds=180,
            schema_retry_limit=1,
        ),

        # ---- Post-evaluation critique (mob: analyst + cheap skeptic) ---------
        "post_eval_critique": WorkflowProfile(
            name="post_eval_critique",
            member_roles=[
                MemberRoleSpec(
                    role="performance_analyst",
                    member_id_suffix="analyst",
                    instructions=[
                        "You analyze trading-strategy backtest results.",
                        "'No trades' is generally a poor result unless the strategy targets rare "
                        "events with adequate sample justification. A strong outcome should aim "
                        "toward roughly 5% average monthly ROI while remaining realistic.",
                        "Produce a JSON critique with keys: "
                        "decision (tweak|retire|promote|continue_backtest), confidence (0-1), "
                        "risk_flags ([str,...]), rationale (str), suggested_next_action (str).",
                    ],
                    model_tier="tier2_standard",
                    max_tokens=1500,
                    is_lead=True,
                    is_required=True,
                ),
                MemberRoleSpec(
                    role="overfitting_skeptic",
                    member_id_suffix="skeptic",
                    instructions=[
                        "You look for data-mining bias and overfitting in backtest results.",
                        "Be evidence-weighted: skeptical when justified, not reflexively negative.",
                        "Return JSON: {\"overfit_suspicion\": \"none|low|high\", "
                        "\"evidence\": [\"<item>\", ...]}.",
                    ],
                    model_tier="tier1_cheap",
                    max_tokens=512,
                    is_lead=False,
                    is_required=False,
                ),
            ],
            timeout_seconds=180,
            schema_retry_limit=1,
        ),

        # ---- Model design (mob: code author + static reviewer) ---------------
        "model_design": WorkflowProfile(
            name="model_design",
            member_roles=[
                MemberRoleSpec(
                    role="code_author",
                    member_id_suffix="author",
                    instructions=[
                        "You write complete Python trading-strategy modules following the factory conventions.",
                        "Implement the researcher's proposed hypothesis faithfully — do not replace it "
                        "with a different strategy.",
                        "Return JSON: {\"module_code\": \"<full python source>\", "
                        "\"class_name\": \"<ClassName>\", \"dependencies\": []}.",
                    ],
                    model_tier="tier_codegen",
                    max_tokens=4096,
                    is_lead=True,
                    is_required=True,
                ),
                MemberRoleSpec(
                    role="static_reviewer",
                    member_id_suffix="reviewer",
                    instructions=[
                        "Review the proposed Python module for correctness, bad practices, "
                        "or missing safety guards.",
                        "Be evidence-weighted: flag real issues, not stylistic preferences.",
                        "Return JSON: {\"approved\": true|false, \"issues\": [\"<item>\", ...]}.",
                    ],
                    model_tier="tier1_cheap",
                    max_tokens=512,
                    is_lead=False,
                    is_required=False,
                ),
            ],
            timeout_seconds=240,
            schema_retry_limit=1,
        ),

        # ---- Model mutation (single-member) ----------------------------------
        "model_mutation": WorkflowProfile(
            name="model_mutation",
            member_roles=[
                MemberRoleSpec(
                    role="code_mutator",
                    member_id_suffix="mutator",
                    instructions=[
                        "You mutate an existing Python trading-strategy module based on backtest feedback.",
                        "Document exactly what changed and why. Emit a new version identifier.",
                        "Return JSON: {\"module_code\": \"<full python source>\", "
                        "\"change_summary\": \"<what changed and why>\", \"version_tag\": \"<semver or hash>\"}.",
                    ],
                    model_tier="tier_mutate",
                    max_tokens=4096,
                    is_lead=True,
                    is_required=True,
                ),
            ],
            timeout_seconds=180,
            schema_retry_limit=1,
            is_mob=False,
        ),

        # ---- Tweak suggestion (single-member) --------------------------------
        "tweak_suggestion": WorkflowProfile(
            name="tweak_suggestion",
            member_roles=[
                MemberRoleSpec(
                    role="parameter_tuner",
                    member_id_suffix="tuner",
                    instructions=[
                        "You suggest parameter adjustments for an underperforming trading strategy.",
                        "Return JSON: {\"suggested_parameters\": {}, \"rationale\": \"<str>\"}.",
                    ],
                    model_tier="tier2_standard",
                    max_tokens=1024,
                    is_lead=True,
                    is_required=True,
                ),
            ],
            timeout_seconds=120,
            schema_retry_limit=1,
            is_mob=False,
        ),

        # ---- Maintenance / bug diagnosis (mob) --------------------------------
        "maintenance_diagnosis": WorkflowProfile(
            name="maintenance_diagnosis",
            member_roles=[
                MemberRoleSpec(
                    role="runtime_triage",
                    member_id_suffix="triage",
                    instructions=[
                        "You triage runtime failures in trading-strategy execution.",
                        "Classify root cause as: code_bug, data_issue, env_issue, or orchestration_issue.",
                        "Return JSON: {\"root_cause\": \"<cls>\", \"severity\": \"low|med|high|critical\", "
                        "\"remediation\": \"<str>\", \"retry_safe\": true|false}.",
                    ],
                    model_tier="tier2_standard",
                    max_tokens=1024,
                    is_lead=True,
                    is_required=True,
                ),
                MemberRoleSpec(
                    role="data_reviewer",
                    member_id_suffix="data",
                    instructions=[
                        "Review execution failure for data integrity issues (bad timestamps, nulls, venue errors).",
                        "Be evidence-weighted: flag real data problems, not hypothetical ones.",
                        "Return JSON: {\"data_issues\": [\"<item>\"], \"data_clean\": true|false}.",
                    ],
                    model_tier="tier1_cheap",
                    max_tokens=512,
                    is_lead=False,
                    is_required=False,
                ),
            ],
            timeout_seconds=120,
            schema_retry_limit=1,
        ),
    }


WORKFLOW_PROFILES: Dict[str, WorkflowProfile] = _build_workflow_profiles()


# ---------------------------------------------------------------------------
# Sync → Async bridge
# ---------------------------------------------------------------------------


class _BackgroundLoop:
    """Persistent asyncio event loop running in a daemon thread.

    Bridges the fully async MobKit SDK to synchronous callers.
    One loop lives per MobkitOrchestratorBackend instance.
    """

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="mobkit-event-loop",
        )
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run(self, coro: Any, *, timeout: Optional[float] = None) -> Any:
        """Submit coroutine and block until result or timeout."""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def close(self) -> None:
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)


# ---------------------------------------------------------------------------
# MobkitOrchestratorBackend
# ---------------------------------------------------------------------------


class MobkitOrchestratorBackend:
    """
    OrchestratorBackend implementation backed by meerkat-mobkit.

    Lifecycle:
    1. Construct (or use ``create(project_root)``).
    2. ``initialize()`` → starts background loop, connects to gateway.
    3. ``run_structured_task`` / ``run_mob_workflow`` → execute factory work.
    4. ``shutdown()`` → closes gateway connection and background loop.

    This backend is disabled by default (FACTORY_ENABLE_MOBKIT=false).
    RuntimeManager instantiates it only when the flag is set AND the
    gateway binary is configured.

    Cost isolation hooks:
    Every method accepts an optional ``budget_hooks`` parameter that is
    reserved for Task 04 (cost governance).  It is not yet enforced here.
    """

    BACKEND_NAME = "mobkit"

    def __init__(
        self,
        *,
        gateway_bin: str,
        mob_config_path: Optional[str] = None,
        timeout_seconds: int = 120,
    ) -> None:
        self._gateway_bin = gateway_bin
        self._mob_config_path = mob_config_path
        self._timeout = timeout_seconds

        self._loop: Optional[_BackgroundLoop] = None
        self._runtime: Any = None   # meerkat_mobkit.MobKitRuntime
        self._handle: Any = None    # meerkat_mobkit.MobHandle
        # Mob-stream futures: member_id → asyncio.Future[str]
        # Set before handle.send(); resolved by the mob watcher when RunCompleted arrives.
        self._pending: Dict[str, Any] = {}
        self._initialized = False
        self._init_error: Optional[str] = None

    # ------------------------------------------------------------------
    # Class factory
    # ------------------------------------------------------------------

    @classmethod
    def create(cls, project_root: str | Path) -> "MobkitOrchestratorBackend":
        """Build from config module settings."""
        gateway_bin = str(getattr(config, "FACTORY_MOBKIT_GATEWAY_BIN", "") or "").strip()
        if not gateway_bin:
            raise MobkitUnavailableError(
                "FACTORY_MOBKIT_GATEWAY_BIN is not set. "
                "Set it to the path of the mobkit-rpc binary."
            )
        mob_config = str(getattr(config, "FACTORY_MOBKIT_CONFIG_PATH", "") or "").strip() or None
        timeout = int(getattr(config, "FACTORY_MOBKIT_TIMEOUT_SECONDS", 120) or 120)
        return cls(
            gateway_bin=gateway_bin,
            mob_config_path=mob_config,
            timeout_seconds=timeout,
        )

    # ------------------------------------------------------------------
    # OrchestratorBackend protocol
    # ------------------------------------------------------------------

    @property
    def backend_name(self) -> str:
        return self.BACKEND_NAME

    def initialize(self) -> None:
        """Connect to the mobkit gateway.  Idempotent; raises on failure."""
        if self._initialized:
            return
        if not _check_mobkit_available():
            raise MobkitUnavailableError(
                "meerkat_mobkit package is not installed. "
                "Run: pip install meerkat-mobkit"
            )
        if not Path(self._gateway_bin).exists():
            raise MobkitUnavailableError(
                f"mobkit gateway binary not found: {self._gateway_bin!r}. "
                "Download mobkit-rpc and set FACTORY_MOBKIT_GATEWAY_BIN."
            )
        self._loop = _BackgroundLoop()
        try:
            self._loop.run(self._async_initialize(), timeout=30)
        except Exception as exc:
            self._init_error = str(exc)
            self._loop.close()
            self._loop = None
            raise MobkitUnavailableError(
                f"mobkit backend initialization failed: {exc}"
            ) from exc

    async def _async_initialize(self) -> None:
        from meerkat_mobkit import MobKit  # type: ignore[import]
        builder = MobKit.builder().gateway(self._gateway_bin)
        if self._mob_config_path:
            builder = builder.mob(self._mob_config_path)
        self._runtime = await builder.build()
        await self._runtime.connect()
        self._handle = self._runtime.mob_handle()
        self._initialized = True
        logger.info(
            "MobkitOrchestratorBackend: connected (gateway=%s)", self._gateway_bin
        )
        # Start background poll loop.
        # The rpc_gateway binary only dispatches SSE events when it receives
        # RPC traffic on stdin/stdout.  Periodic status() calls keep the Rust
        # Tokio event loop flushing agent completions to the SSE bridge.
        asyncio.ensure_future(self._poll_loop())
        # Start mob-stream watcher: resolves futures in _pending on RunCompleted.
        asyncio.ensure_future(self._mob_watcher())

    async def _poll_loop(self) -> None:
        """Keep the Rust gateway event loop active by polling every 0.8s."""
        while self._initialized and self._handle is not None:
            try:
                await self._handle.status()
            except Exception:
                pass
            await asyncio.sleep(0.8)

    async def _mob_watcher(self) -> None:
        """Subscribe to mob-wide events and resolve pending futures."""
        try:
            from meerkat_mobkit.events import RunCompleted, RunFailed  # type: ignore[import]
        except ImportError:
            return
        try:
            async for mob_event in self._handle.subscribe_mob():
                if not self._initialized:
                    break
                mid = mob_event.member_id
                ev = mob_event.event
                fut = self._pending.get(mid)
                if fut is None or fut.done():
                    continue
                if isinstance(ev, RunCompleted):
                    fut.set_result(str(ev.result))
                elif isinstance(ev, RunFailed):
                    fut.set_exception(
                        MobkitWorkflowError(f"Agent {mid!r} run failed: {ev.error}")
                    )
        except Exception as exc:
            logger.warning("MobkitBackend mob_watcher exited: %s", exc)

    def healthcheck(self) -> bool:
        """Return True if the mobkit gateway is reachable.  Triggers lazy init."""
        if not self._initialized:
            try:
                self.initialize()
            except MobkitUnavailableError as exc:
                logger.warning("MobkitOrchestratorBackend.healthcheck: %s", exc)
                return False
        if self._loop is None or self._handle is None:
            return False
        try:
            status = self._loop.run(self._handle.status(), timeout=10)
            return bool(getattr(status, "running", False))
        except Exception as exc:
            logger.warning("MobkitOrchestratorBackend: status check failed: %s", exc)
            return False

    def run_structured_task(
        self,
        *,
        task_type: str,
        prompt: str,
        schema: Dict[str, Any],
        model_tier: str,
        family_id: str,
        lineage_id: Optional[str],
        trace_id: str,
        max_tokens: Optional[int] = None,
        timeout_seconds: Optional[int] = None,
        budget_hooks: Optional[Any] = None,   # reserved for Task 04
    ) -> Dict[str, Any]:
        """Execute a single-member structured task.

        Returns a dict with keys:
          ``payload``      — validated JSON payload (dict)
          ``member_traces`` — list of per-member trace dicts
          ``backend``      — "mobkit"
        """
        self._require_initialized()
        assert self._loop is not None

        member_id = f"at-{task_type[:10]}-{trace_id[:8]}"
        profile = _TIER_TO_PROFILE.get(model_tier, "standard-worker")
        tok = max_tokens or 2048
        # Apply budget_hooks token ceiling if present.
        if budget_hooks is not None:
            tok_override = getattr(budget_hooks, "max_tokens_override", None)
            if tok_override is not None:
                tok = min(tok, tok_override)
        timeout = float(timeout_seconds or self._timeout)

        instructions = _task_instructions(task_type=task_type, model_tier=model_tier, schema=schema)
        try:
            return self._loop.run(
                self._async_single_member(
                    member_id=member_id,
                    profile=profile,
                    model_tier=model_tier,
                    instructions=instructions,
                    prompt=prompt,
                    schema=schema,
                    max_tokens=tok,
                    trace_id=trace_id,
                ),
                timeout=timeout,
            )
        except MobkitBackendError:
            raise
        except Exception as exc:
            raise MobkitWorkflowError(
                f"run_structured_task({task_type!r}) failed: {exc}"
            ) from exc

    def run_mob_workflow(
        self,
        *,
        workflow_name: str,
        role_definitions: List[Dict[str, Any]],
        shared_context: Dict[str, Any],
        output_schema: Dict[str, Any],
        trace_id: str,
        family_id: str,
        lineage_id: Optional[str],
        timeout_seconds: Optional[int] = None,
        budget_hooks: Optional[Any] = None,   # reserved for Task 04
    ) -> Dict[str, Any]:
        """Execute a named multi-member mob workflow.

        Returns a dict with keys:
          ``payload``      — final validated JSON payload
          ``member_traces`` — list of per-member trace dicts
          ``backend``      — "mobkit"
        """
        self._require_initialized()
        assert self._loop is not None

        profile = WORKFLOW_PROFILES.get(workflow_name)
        if profile is None:
            raise MobkitWorkflowError(
                f"Unknown workflow_name={workflow_name!r}. "
                f"Available: {sorted(WORKFLOW_PROFILES)}"
            )

        timeout = float(timeout_seconds or profile.timeout_seconds)
        try:
            return self._loop.run(
                self._async_mob_workflow(
                    profile=profile,
                    shared_context=shared_context,
                    output_schema=output_schema,
                    trace_id=trace_id,
                    family_id=family_id,
                    lineage_id=lineage_id,
                    budget_hooks=budget_hooks,
                ),
                timeout=timeout,
            )
        except MobkitBackendError:
            raise
        except Exception as exc:
            raise MobkitWorkflowError(
                f"run_mob_workflow({workflow_name!r}) failed: {exc}"
            ) from exc

    def cancel_run(self, run_id: str) -> None:
        """Best-effort cooperative cancellation."""
        logger.debug("MobkitOrchestratorBackend.cancel_run(%s) best-effort", run_id)

    def shutdown(self) -> None:
        """Shut down gateway connection and background loop."""
        if self._runtime and self._loop and self._initialized:
            try:
                self._loop.run(self._runtime.shutdown(), timeout=10)
            except Exception as exc:
                logger.warning("MobkitOrchestratorBackend.shutdown error: %s", exc)
        if self._loop:
            self._loop.close()
            self._loop = None
        self._initialized = False
        logger.info("MobkitOrchestratorBackend: shut down")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_initialized(self) -> None:
        """Initialize lazily; raise MobkitUnavailableError if it fails."""
        if not self._initialized:
            self.initialize()

    async def _collect_run_result(
        self,
        member_id: str,
        *,
        timeout: float,
        pending: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Collect RunCompleted for one member from a shared mob-stream future dict.

        The rpc_gateway Rust binary only dispatches SSE events when it
        receives RPC traffic on its stdin/stdout transport.  Callers must
        ensure a background poll task (e.g. periodic handle.status()) is
        running while this coroutine waits.

        ``pending`` maps member_id → asyncio.Future[str]; callers create the
        future before calling ``handle.send()``.  If not provided, falls back
        to subscribe_agent (legacy path, requires external polling).
        """
        if pending is not None and member_id in pending:
            fut = pending[member_id]
            try:
                return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
            except asyncio.TimeoutError:
                raise MobkitWorkflowError(
                    f"Agent {member_id!r} timed out after {timeout:.0f}s"
                )

        # Legacy path: direct per-agent subscribe_agent (requires external poll).
        try:
            from meerkat_mobkit.events import RunCompleted, RunFailed  # type: ignore[import]
        except ImportError as exc:
            raise MobkitUnavailableError(
                "meerkat_mobkit.events not available"
            ) from exc

        async def _collect() -> str:
            async for agent_event in self._handle.subscribe_agent(member_id):
                ev = agent_event.event
                if isinstance(ev, RunCompleted):
                    return str(ev.result)
                if isinstance(ev, RunFailed):
                    raise MobkitWorkflowError(
                        f"Agent {member_id!r} run failed: {ev.error}"
                    )
            raise MobkitWorkflowError(
                f"Agent {member_id!r} event stream ended without RunCompleted"
            )

        try:
            return await asyncio.wait_for(_collect(), timeout=timeout)
        except asyncio.TimeoutError:
            raise MobkitWorkflowError(
                f"Agent {member_id!r} timed out after {timeout:.0f}s"
            )

    async def _async_single_member(
        self,
        *,
        member_id: str,
        profile: str,
        model_tier: str = "tier2_standard",
        instructions: List[str],
        prompt: str,
        schema: Dict[str, Any],
        max_tokens: int,
        trace_id: str,
    ) -> Dict[str, Any]:
        """Spawn one agent, send prompt, collect and validate result."""
        # Spawn (or reuse) the member with role-specific instructions.
        await self._handle.ensure_member(
            member_id,
            profile,
            additional_instructions=instructions,
        )
        # Register future before send so mob_watcher can resolve it.
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[member_id] = fut
        # Dispatch work.
        await self._handle.send(member_id, prompt)
        # Collect result via mob-stream future (poll_loop keeps events flowing).
        result_text = await self._collect_run_result(
            member_id, timeout=float(max_tokens * 0.05 + 30), pending=self._pending
        )
        # Parse and validate.
        payload = _parse_json_output(result_text, member_id)
        return {
            "payload": payload,
            "member_traces": [
                {
                    "member_id": member_id,
                    "role": profile,
                    "model": model_tier,
                    "profile": profile,
                    "success": True,
                    "usage": None,
                }
            ],
            "backend": self.BACKEND_NAME,
        }

    async def _async_mob_workflow(
        self,
        *,
        profile: WorkflowProfile,
        shared_context: Dict[str, Any],
        output_schema: Dict[str, Any],
        trace_id: str,
        family_id: str,
        lineage_id: Optional[str],
        budget_hooks: Optional[Any] = None,  # BudgetHooks | None
    ) -> Dict[str, Any]:
        """Execute a multi-member workflow: Lead → Reviewers → Lead synthesis."""
        member_traces: List[Dict[str, Any]] = []
        lead_role = next(
            (r for r in profile.member_roles if r.is_lead),
            profile.member_roles[0],
        )

        # ---- Apply budget_hooks constraints ---------------------------
        _removed_roles: set = set()
        _force_single = False
        _lead_max_tokens = lead_role.max_tokens
        if budget_hooks is not None:
            removed = getattr(budget_hooks, "removed_member_roles", [])
            _removed_roles = set(removed)
            _force_single = getattr(budget_hooks, "force_single_task", False)
            tok_override = getattr(budget_hooks, "max_tokens_override", None)
            if tok_override is not None:
                _lead_max_tokens = min(_lead_max_tokens, tok_override)

        reviewer_roles = [
            r for r in profile.member_roles
            if not r.is_lead and r.role not in _removed_roles
        ]

        # Force single task: skip reviewers entirely.
        if _force_single:
            reviewer_roles = []

        context_text = json.dumps(shared_context, default=str, indent=2)
        lead_id = f"at-{profile.name[:8]}-{trace_id[:6]}-{lead_role.member_id_suffix}"

        # ---- Step 1: Lead produces initial draft ---------------------------
        lead_prompt = (
            f"Context:\n{context_text}\n\n"
            "Produce the required structured output.\n"
            f"Output schema: {json.dumps(output_schema)}"
        )
        await self._handle.ensure_member(
            lead_id,
            _TIER_TO_PROFILE.get(lead_role.model_tier, "standard-worker"),
            additional_instructions=lead_role.instructions,
        )
        _lead_fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[lead_id] = _lead_fut
        await self._handle.send(lead_id, lead_prompt)
        lead_draft = await self._collect_run_result(
            lead_id, timeout=float(_lead_max_tokens * 0.05 + 60), pending=self._pending
        )
        lead_profile = _TIER_TO_PROFILE.get(lead_role.model_tier, "standard-worker")
        member_traces.append(
            {
                "member_id": lead_id,
                "role": lead_role.role,
                "model": lead_role.model_tier,
                "profile": lead_profile,
                "success": True,
                "usage": None,
            }
        )

        # ---- Step 2: Optional reviewers critique the draft -----------------
        review_texts: List[str] = []
        for reviewer in reviewer_roles:
            rev_id = f"at-{profile.name[:8]}-{trace_id[:6]}-{reviewer.member_id_suffix}"
            try:
                review_prompt = (
                    f"Original context:\n{context_text}\n\n"
                    f"Lead draft:\n{lead_draft}\n\n"
                    "Provide your structured review."
                )
                await self._handle.ensure_member(
                    rev_id,
                    _TIER_TO_PROFILE.get(reviewer.model_tier, "cheap-reviewer"),
                    additional_instructions=reviewer.instructions,
                )
                _rev_fut: asyncio.Future = asyncio.get_event_loop().create_future()
                self._pending[rev_id] = _rev_fut
                await self._handle.send(rev_id, review_prompt)
                review_text = await self._collect_run_result(
                    rev_id, timeout=float(reviewer.max_tokens * 0.05 + 30), pending=self._pending
                )
                rev_profile = _TIER_TO_PROFILE.get(reviewer.model_tier, "cheap-reviewer")
                review_texts.append(f"[{reviewer.role}]: {review_text}")
                member_traces.append(
                    {
                        "member_id": rev_id,
                        "role": reviewer.role,
                        "model": reviewer.model_tier,
                        "profile": rev_profile,
                        "success": True,
                        "usage": None,
                    }
                )
            except Exception as exc:
                # Non-required reviewers: log and continue.
                if reviewer.is_required:
                    raise
                logger.warning(
                    "MobkitBackend: non-required reviewer %s failed: %s", reviewer.role, exc
                )
                rev_profile = _TIER_TO_PROFILE.get(reviewer.model_tier, "cheap-reviewer")
                member_traces.append(
                    {
                        "member_id": rev_id,
                        "role": reviewer.role,
                        "model": reviewer.model_tier,
                        "profile": rev_profile,
                        "success": False,
                        "fallback_reason": str(exc),
                        "usage": None,
                    }
                )

        # ---- Step 3: Lead synthesizes final output -------------------------
        if review_texts:
            synthesis_prompt = (
                f"Your draft:\n{lead_draft}\n\n"
                "Reviewer feedback:\n"
                + "\n".join(review_texts)
                + f"\n\nSynthesize a final JSON response.\n"
                  f"Schema: {json.dumps(output_schema)}"
            )
            _synth_fut: asyncio.Future = asyncio.get_event_loop().create_future()
            self._pending[lead_id] = _synth_fut
            await self._handle.send(lead_id, synthesis_prompt)
            final_text = await self._collect_run_result(
                lead_id, timeout=float(_lead_max_tokens * 0.05 + 60), pending=self._pending
            )
        else:
            final_text = lead_draft

        # ---- Parse and validate -------------------------------------------
        payload = _parse_json_output(final_text, lead_id)
        return {
            "payload": payload,
            "member_traces": member_traces,
            "backend": self.BACKEND_NAME,
        }


# ---------------------------------------------------------------------------
# JSON output helpers
# ---------------------------------------------------------------------------


def _extract_tokens(result: Dict[str, Any]) -> int:
    """Sum token counts from member_traces in a backend result dict."""
    total = 0
    for trace in result.get("member_traces", []):
        usage = trace.get("usage") or {}
        if isinstance(usage, dict):
            total += usage.get("total_tokens", 0)
    return total


def _parse_json_output(text: str, member_id: str) -> Dict[str, Any]:
    """Parse JSON from agent text output, stripping markdown fences and comms noise."""
    text = text.strip()
    # Strip markdown code fences.
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
        return {"result": result}
    except json.JSONDecodeError:
        # Mob comms tool calls may be prepended to the real output:
        #   {"tool":"peers","params":{}}{"tool":"peers","params":{}}{...real json...}
        # Find the last top-level JSON object which is typically the agent's actual response.
        last_obj = None
        depth = 0
        start = -1
        for i, ch in enumerate(text):
            if ch == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start >= 0:
                    candidate = text[start:i + 1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, dict) and parsed.get("tool") not in ("peers", "send_message"):
                            last_obj = parsed
                    except json.JSONDecodeError:
                        pass
                    start = -1
        if last_obj is not None:
            return last_obj
        raise MobkitSchemaError(
            f"Agent {member_id!r} returned non-JSON output. "
            f"Raw (first 300 chars): {text[:300]!r}"
        )


def _make_run_result(
    *,
    task_type: str,
    backend_result: Dict[str, Any],
    family_id: str,
    lineage_id: Optional[str],
    started_at: datetime,
    success: bool = True,
    error: Optional[str] = None,
    fallback_used: bool = False,
    model_class: str = "TASK_STANDARD",
) -> AgentRunResult:
    """Map a backend result dict to AgentRunResult."""
    now = datetime.now(timezone.utc)
    duration_ms = max(0, int((now - started_at).total_seconds() * 1000))
    payload = backend_result.get("payload", {})
    traces = backend_result.get("member_traces", [])
    roles = [t.get("role", "") for t in traces if t.get("role")]
    # Surface the lead member's profile name for trace visibility.
    lead_trace = next((t for t in traces if t.get("role") and "lead" in t.get("role", "")), None)
    if lead_trace is None and traces:
        lead_trace = traces[0]
    model_label = (
        f"mobkit/{lead_trace['profile']}" if lead_trace and lead_trace.get("profile")
        else "mobkit"
    )
    return AgentRunResult(
        run_id=str(uuid.uuid4()),
        task_type=task_type,
        model_class=model_class,
        provider="mobkit",
        model=model_label,
        reasoning_effort="standard",
        success=success,
        fallback_used=fallback_used,
        family_id=family_id,
        lineage_id=lineage_id,
        duration_ms=duration_ms,
        result_payload=payload if success else {},
        raw_text=json.dumps(payload, default=str) if success else "",
        error=error,
        multi_agent_requested=len(roles) > 1,
        multi_agent_roles=roles,
    )


# ---------------------------------------------------------------------------
# MobkitRuntime — implements AgentRuntime using MobkitOrchestratorBackend
# ---------------------------------------------------------------------------


class MobkitRuntime:
    """
    AgentRuntime implementation backed by MobkitOrchestratorBackend.

    Maps each factory task type to a named workflow profile and delegates
    to the backend for execution.  Results are wrapped in AgentRunResult
    to preserve backward compatibility with orchestrator result handling.
    """

    BACKEND_NAME = "mobkit"

    def __init__(
        self,
        project_root: str | Path,
        backend: Optional[MobkitOrchestratorBackend] = None,
        governor: Optional[Any] = None,  # factory.governance.CostGovernor (lazy import avoids cycle)
    ) -> None:
        self._project_root = Path(project_root)
        self._log_dir = _resolve_log_dir(self._project_root)
        if backend is None:
            backend = MobkitOrchestratorBackend.create(project_root)
        self._backend = backend
        self._governor = governor  # CostGovernor | None

    @property
    def backend_name(self) -> str:
        return self.BACKEND_NAME

    def healthcheck(self) -> bool:
        """Delegate healthcheck to the underlying OrchestratorBackend."""
        return self._backend.healthcheck()

    def _write_run_artifact(self, result: AgentRunResult, *, prompt_payload: Optional[Dict[str, Any]] = None) -> AgentRunResult:
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "run_id": result.run_id,
            "task_type": result.task_type,
            "model_class": result.model_class,
            "provider": result.provider,
            "model": result.model,
            "reasoning_effort": result.reasoning_effort,
            "family_id": result.family_id,
            "lineage_id": result.lineage_id,
            "success": result.success,
            "fallback_used": result.fallback_used,
            "duration_ms": result.duration_ms,
            "prompt_payload": dict(prompt_payload or {}),
            "result_payload": result.result_payload,
            "error": result.error,
            "attempted_providers": list(result.attempted_providers),
            "raw_text": result.raw_text,
            "multi_agent_requested": result.multi_agent_requested,
            "multi_agent_roles": list(result.multi_agent_roles),
        }
        path = self._log_dir / f"{result.run_id}.json"
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        result.artifact_path = str(path)
        return result

    # ------------------------------------------------------------------
    # Internal: run a named workflow and convert to AgentRunResult
    # ------------------------------------------------------------------

    def _run_mob(
        self,
        *,
        workflow_name: str,
        task_type: str,
        shared_context: Dict[str, Any],
        output_schema: Dict[str, Any],
        family_id: str,
        lineage_id: Optional[str],
        trace_id: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        model_class: str = "TASK_STANDARD",
        budget_hooks: Optional[Any] = None,
    ) -> Optional[AgentRunResult]:
        started_at = datetime.now(timezone.utc)
        tid = trace_id or str(uuid.uuid4())
        _trace_ctx = TraceContext.create(
            family_id=family_id, lineage_id=lineage_id, trace_id=tid
        )

        # ---- Budget gate (Task 04) ------------------------------------
        profile = WORKFLOW_PROFILES.get(workflow_name)
        reviewer_roles = (
            [r.role for r in profile.member_roles if not r.is_lead]
            if profile else []
        )
        planned_tokens = profile.member_roles[0].max_tokens if profile else 2048
        _tel.workflow_planned(
            task_type, self.BACKEND_NAME,
            trace_ctx=_trace_ctx, planned_tokens=planned_tokens, is_mob=True,
        )
        hooks = self._resolve_budget_hooks(
            budget_hooks=budget_hooks,
            family_id=family_id,
            lineage_id=lineage_id,
            task_type=task_type,
            planned_tokens=planned_tokens,
            is_mob=True,
            reviewer_roles=reviewer_roles,
        )
        if hooks is not None and getattr(hooks, "downgrade_decision", None) is not None:
            dd = hooks.downgrade_decision
            if getattr(dd, "stopped", False) and getattr(hooks, "strict", False):
                # strict hard stop — return None (skip this task)
                logger.warning("MobkitRuntime._run_mob: hard stop for %s/%s", task_type, family_id)
                _tel.workflow_failed(
                    task_type, self.BACKEND_NAME,
                    trace_ctx=_trace_ctx, reason="budget_hard_stop",
                )
                return None
            # Emit downgrade event if any constraint is active.
            if getattr(hooks, "has_constraints", lambda: False)():
                _tel.downgrade_applied(
                    task_type,
                    trace_ctx=_trace_ctx,
                    scope=getattr(dd, "scope", None),
                    reason=getattr(dd, "reason", None),
                    action=getattr(dd, "action", None),
                    usage_ratio=getattr(dd, "usage_ratio", None),
                )

        _tel.workflow_started(task_type, self.BACKEND_NAME, trace_ctx=_trace_ctx)
        try:
            result = self._backend.run_mob_workflow(
                workflow_name=workflow_name,
                role_definitions=[],   # profiles drive roles; field kept for interface parity
                shared_context=shared_context,
                output_schema=output_schema,
                trace_id=tid,
                family_id=family_id,
                lineage_id=lineage_id,
                timeout_seconds=timeout_seconds,
                budget_hooks=hooks,
            )
            # ---- Record actual usage ----------------------------------
            tokens = _extract_tokens(result)
            self._record_governor_usage(
                family_id=family_id, lineage_id=lineage_id,
                task_type=task_type, tokens=tokens, success=True,
            )
            duration_ms = max(0, int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000))
            _tel.workflow_finished(
                task_type, self.BACKEND_NAME,
                trace_ctx=_trace_ctx, tokens=tokens, duration_ms=duration_ms,
            )
            return self._write_run_artifact(_make_run_result(
                task_type=task_type,
                backend_result=result,
                family_id=family_id,
                lineage_id=lineage_id,
                started_at=started_at,
                success=True,
                model_class=model_class,
            ), prompt_payload=shared_context)
        except MobkitWorkflowError as exc:
            self._record_governor_usage(
                family_id=family_id, lineage_id=lineage_id,
                task_type=task_type, tokens=0, success=False,
            )
            logger.error("MobkitRuntime.%s failed: %s", task_type, exc)
            _tel.workflow_failed(
                task_type, self.BACKEND_NAME,
                trace_ctx=_trace_ctx, reason=str(exc),
            )
            return self._write_run_artifact(_make_run_result(
                task_type=task_type,
                backend_result={},
                family_id=family_id,
                lineage_id=lineage_id,
                started_at=started_at,
                success=False,
                error=str(exc),
            ), prompt_payload=shared_context)

    def _run_single(
        self,
        *,
        workflow_name: str,
        task_type: str,
        prompt: str,
        schema: Dict[str, Any],
        family_id: str,
        lineage_id: Optional[str],
        model_tier: str = "tier2_standard",
        trace_id: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        budget_hooks: Optional[Any] = None,
    ) -> Optional[AgentRunResult]:
        started_at = datetime.now(timezone.utc)
        tid = trace_id or str(uuid.uuid4())
        model_class = _TIER_TO_MODEL_CLASS.get(model_tier, "TASK_STANDARD")
        _trace_ctx = TraceContext.create(
            family_id=family_id, lineage_id=lineage_id, trace_id=tid
        )

        # ---- Budget gate (Task 04) ------------------------------------
        _tel.workflow_planned(
            task_type, self.BACKEND_NAME,
            trace_ctx=_trace_ctx, planned_tokens=2048, is_mob=False,
        )
        hooks = self._resolve_budget_hooks(
            budget_hooks=budget_hooks,
            family_id=family_id,
            lineage_id=lineage_id,
            task_type=task_type,
            planned_tokens=2048,
            is_mob=False,
            reviewer_roles=[],
        )
        if hooks is not None and getattr(hooks, "downgrade_decision", None) is not None:
            dd = hooks.downgrade_decision
            if getattr(dd, "stopped", False) and getattr(hooks, "strict", False):
                logger.warning("MobkitRuntime._run_single: hard stop for %s/%s", task_type, family_id)
                _tel.workflow_failed(
                    task_type, self.BACKEND_NAME,
                    trace_ctx=_trace_ctx, reason="budget_hard_stop",
                )
                return None
            if getattr(hooks, "has_constraints", lambda: False)():
                _tel.downgrade_applied(
                    task_type,
                    trace_ctx=_trace_ctx,
                    scope=getattr(dd, "scope", None),
                    reason=getattr(dd, "reason", None),
                    action=getattr(dd, "action", None),
                    usage_ratio=getattr(dd, "usage_ratio", None),
                )

        _tel.workflow_started(task_type, self.BACKEND_NAME, trace_ctx=_trace_ctx)
        try:
            result = self._backend.run_structured_task(
                task_type=task_type,
                prompt=prompt,
                schema=schema,
                model_tier=model_tier,
                family_id=family_id,
                lineage_id=lineage_id,
                trace_id=tid,
                timeout_seconds=timeout_seconds,
                budget_hooks=hooks,
            )
            tokens = _extract_tokens(result)
            self._record_governor_usage(
                family_id=family_id, lineage_id=lineage_id,
                task_type=task_type, tokens=tokens, success=True,
            )
            duration_ms = max(0, int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000))
            _tel.workflow_finished(
                task_type, self.BACKEND_NAME,
                trace_ctx=_trace_ctx, tokens=tokens, duration_ms=duration_ms,
            )
            return self._write_run_artifact(_make_run_result(
                task_type=task_type,
                backend_result=result,
                family_id=family_id,
                lineage_id=lineage_id,
                started_at=started_at,
                success=True,
                model_class=model_class,
            ), prompt_payload={"prompt": prompt, "schema": schema})
        except MobkitWorkflowError as exc:
            self._record_governor_usage(
                family_id=family_id, lineage_id=lineage_id,
                task_type=task_type, tokens=0, success=False,
            )
            logger.error("MobkitRuntime.%s failed: %s", task_type, exc)
            _tel.workflow_failed(
                task_type, self.BACKEND_NAME,
                trace_ctx=_trace_ctx, reason=str(exc),
            )
            return self._write_run_artifact(_make_run_result(
                task_type=task_type,
                backend_result={},
                family_id=family_id,
                lineage_id=lineage_id,
                started_at=started_at,
                success=False,
                error=str(exc),
            ), prompt_payload={"prompt": prompt, "schema": schema})

    # ------------------------------------------------------------------
    # Governance helpers
    # ------------------------------------------------------------------

    def _resolve_budget_hooks(
        self,
        *,
        budget_hooks: Optional[Any],
        family_id: str,
        lineage_id: Optional[str],
        task_type: str,
        planned_tokens: int,
        is_mob: bool,
        reviewer_roles: List[str],
    ) -> Optional[Any]:
        """Ask governor for BudgetHooks; fall back to caller-supplied hooks."""
        if self._governor is not None:
            try:
                return self._governor.check_and_plan(
                    family_id=family_id,
                    lineage_id=lineage_id,
                    task_type=task_type,
                    planned_tokens=planned_tokens,
                    is_mob=is_mob,
                    reviewer_roles=reviewer_roles,
                )
            except Exception as exc:  # GovernorStopError or unexpected
                from factory.governance import GovernorStopError
                if isinstance(exc, GovernorStopError):
                    raise
                logger.warning(
                    "MobkitRuntime: governor.check_and_plan error (ignored): %s", exc
                )
        return budget_hooks  # caller-supplied passthrough

    def _record_governor_usage(
        self,
        *,
        family_id: str,
        lineage_id: Optional[str],
        task_type: str,
        tokens: int,
        success: bool,
    ) -> None:
        if self._governor is not None:
            try:
                self._governor.record_usage(
                    family_id=family_id,
                    lineage_id=lineage_id,
                    task_type=task_type,
                    tokens=tokens,
                    success=success,
                )
            except Exception as exc:
                logger.warning("MobkitRuntime: governor.record_usage error (ignored): %s", exc)

    # ------------------------------------------------------------------
    # AgentRuntime protocol — 8 business-level methods
    # ------------------------------------------------------------------

    def generate_proposal(
        self,
        *,
        family: FactoryFamily,
        champion_hypothesis: Optional[ResearchHypothesis],
        champion_genome: StrategyGenome,
        learning_memory: Sequence[LearningMemoryEntry],
        execution_evidence: Optional[Dict[str, Any]],
        cycle_count: int,
        proposal_index: int,
        desired_creation_kind: str = "mutation",
        idea_candidates: Optional[Sequence[Dict[str, Any]]] = None,
        dna_summary: Optional[str] = None,
    ) -> Optional[AgentRunResult]:
        context: Dict[str, Any] = {
            "family_id": family.family_id,
            "thesis": family.thesis,
            "desired_creation_kind": desired_creation_kind,
            "cycle_count": cycle_count,
            "proposal_index": proposal_index,
            "champion_genome_id": champion_genome.genome_id,
            "champion_parameters": champion_genome.parameters,
            "recent_learning": [m.summary for m in list(learning_memory)[-3:]],
            "execution_evidence_summary": execution_evidence or {},
            "idea_candidates": list(idea_candidates or [])[:3],
        }
        if dna_summary:
            context["lineage_dna"] = dna_summary
        schema = {
            "hypothesis": "string",
            "market_regime": "string",
            "parameter_changes": "object",
            "validation_plan": "string",
            "complexity_estimate": "string",
            "rationale": "string",
        }
        return self._run_mob(
            workflow_name="proposal_generation",
            task_type="generate_proposal",
            shared_context=context,
            output_schema=schema,
            family_id=family.family_id,
            lineage_id=None,
            model_class="TASK_EXPENSIVE",
        )

    def generate_family_proposal(
        self,
        *,
        idea: Dict[str, Any],
        existing_family_ids: Sequence[str],
        cycle_count: int,
        proposal_index: int,
        research_portfolio_id: str,
        active_incubation_count: int = 0,
    ) -> Optional[AgentRunResult]:
        context: Dict[str, Any] = {
            "idea": idea,
            "existing_family_count": len(list(existing_family_ids)),
            "research_portfolio_id": research_portfolio_id,
            "cycle_count": cycle_count,
            "active_incubation_count": active_incubation_count,
        }
        schema = {
            "family_id": "string",
            "thesis": "string",
            "target_venues": "array",
            "hypothesis": "string",
            "expected_regime": "string",
            "initial_parameters": "object",
        }
        return self._run_mob(
            workflow_name="proposal_generation",
            task_type="generate_family_proposal",
            shared_context=context,
            output_schema=schema,
            family_id=research_portfolio_id,
            lineage_id=None,
            model_class="TASK_EXPENSIVE",
        )

    def suggest_tweak(
        self,
        *,
        lineage: LineageRecord,
        hypothesis: Optional[ResearchHypothesis],
        genome: StrategyGenome,
        row: Dict[str, Any],
        learning_memory: Sequence[LearningMemoryEntry],
        execution_evidence: Optional[Dict[str, Any]],
    ) -> Optional[AgentRunResult]:
        prompt = (
            f"Lineage {lineage.lineage_id!r} (family={lineage.family_id!r}, "
            f"stage={lineage.current_stage!r}) needs a parameter tweak.\n"
            f"Current parameters: {json.dumps(genome.parameters, default=str)}\n"
            f"Backtest row: {json.dumps(row, default=str)}\n"
            f"Recent lessons: {[m.summary for m in list(learning_memory)[-2:]]}\n"
            "Suggest specific parameter changes to improve performance."
        )
        schema = {
            "suggested_parameters": "object",
            "rationale": "string",
        }
        return self._run_single(
            workflow_name="tweak_suggestion",
            task_type="suggest_tweak",
            prompt=prompt,
            schema=schema,
            family_id=lineage.family_id,
            lineage_id=lineage.lineage_id,
            model_tier="tier2_standard",
        )

    def critique_post_evaluation(
        self,
        *,
        family: FactoryFamily,
        lineage: LineageRecord,
        genome: Optional[StrategyGenome],
        latest_bundle: Optional[EvaluationBundle],
        learning_memory: Sequence[LearningMemoryEntry],
        execution_evidence: Optional[Dict[str, Any]],
        review_context: Optional[Dict[str, Any]] = None,
        force: bool = False,
    ) -> Optional[AgentRunResult]:
        if (not force and not _post_eval_critique_enabled()) or not _family_enabled(family.family_id):
            return None
        bundle_summary: Dict[str, Any] = {}
        if latest_bundle:
            bundle_summary = {
                "evaluation_id": latest_bundle.evaluation_id,
                "stage": latest_bundle.stage,
                "monthly_roi_pct": latest_bundle.monthly_roi_pct,
                "max_drawdown_pct": latest_bundle.max_drawdown_pct,
                "fitness_score": latest_bundle.fitness_score,
                "trade_count": latest_bundle.trade_count,
                "hard_vetoes": list(latest_bundle.hard_vetoes),
                "notes": list(latest_bundle.notes),
            }
        context: Dict[str, Any] = {
            "family_id": family.family_id,
            "lineage_id": lineage.lineage_id,
            "stage": lineage.current_stage,
            "latest_bundle": bundle_summary,
            "recent_learning": [m.summary for m in list(learning_memory)[-3:]],
            "review_context": review_context or {},
        }
        schema = {
            "decision": "tweak|retire|promote",
            "confidence": "float 0-1",
            "risk_flags": "array of strings",
            "rationale": "string",
            "suggested_next_action": "string",
        }
        return self._run_mob(
            workflow_name="post_eval_critique",
            task_type="critique_post_evaluation",
            shared_context=context,
            output_schema=schema,
            family_id=family.family_id,
            lineage_id=lineage.lineage_id,
            model_class="TASK_STANDARD",
        )

    def diagnose_bug(
        self,
        *,
        family: FactoryFamily,
        lineage: LineageRecord,
        genome: Optional[StrategyGenome],
        latest_bundle: Optional[EvaluationBundle],
        execution_evidence: Optional[Dict[str, Any]],
        debug_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[AgentRunResult]:
        context: Dict[str, Any] = {
            "family_id": family.family_id,
            "lineage_id": lineage.lineage_id,
            "execution_evidence": execution_evidence or {},
            "debug_context": debug_context or {},
        }
        schema = {
            "root_cause": "code_bug|data_issue|env_issue|orchestration_issue",
            "severity": "low|med|high|critical",
            "remediation": "string",
            "retry_safe": "boolean",
        }
        return self._run_mob(
            workflow_name="maintenance_diagnosis",
            task_type="diagnose_bug",
            shared_context=context,
            output_schema=schema,
            family_id=family.family_id,
            lineage_id=lineage.lineage_id,
            model_class="TASK_STANDARD",
        )

    def resolve_maintenance_item(
        self,
        *,
        family: FactoryFamily,
        lineage: LineageRecord,
        genome: Optional[StrategyGenome],
        latest_bundle: Optional[EvaluationBundle],
        learning_memory: Sequence[LearningMemoryEntry],
        execution_evidence: Optional[Dict[str, Any]],
        maintenance_request: Dict[str, Any],
        review_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[AgentRunResult]:
        context: Dict[str, Any] = {
            "family_id": family.family_id,
            "lineage_id": lineage.lineage_id,
            "maintenance_request": maintenance_request,
            "execution_evidence": execution_evidence or {},
            "recent_learning": [m.summary for m in list(learning_memory)[-2:]],
        }
        schema = {
            "resolution_action": "string",
            "code_changes_required": "boolean",
            "parameter_changes": "object",
            "notes": "string",
        }
        return self._run_mob(
            workflow_name="maintenance_diagnosis",
            task_type="resolve_maintenance_item",
            shared_context=context,
            output_schema=schema,
            family_id=family.family_id,
            lineage_id=lineage.lineage_id,
            model_class="TASK_STANDARD",
        )

    def design_model(
        self,
        *,
        idea: Dict[str, Any],
        family_id: str,
        target_venues: Sequence[str],
        thesis: str,
        cycle_count: int,
    ) -> Optional[AgentRunResult]:
        context: Dict[str, Any] = {
            "idea": idea,
            "family_id": family_id,
            "target_venues": list(target_venues),
            "thesis": thesis,
            "cycle_count": cycle_count,
        }
        schema = {
            "module_code": "string (full python source)",
            "class_name": "string",
            "dependencies": "array of strings",
        }
        return self._run_mob(
            workflow_name="model_design",
            task_type="design_model",
            shared_context=context,
            output_schema=schema,
            family_id=family_id,
            lineage_id=None,
            model_class="TASK_STANDARD",
        )

    def mutate_model(
        self,
        *,
        family_id: str,
        lineage_id: str,
        current_model_code: str,
        class_name: str,
        backtest_results: Dict[str, Any],
        thesis: str,
        tweak_count: int = 0,
    ) -> Optional[AgentRunResult]:
        prompt = (
            f"Mutate the following Python trading-strategy class to improve performance.\n\n"
            f"Class name: {class_name}\n"
            f"Thesis: {thesis}\n"
            f"Tweak count so far: {tweak_count}\n"
            f"Backtest results: {json.dumps(backtest_results, default=str)}\n\n"
            f"Current module code:\n```python\n{current_model_code}\n```\n\n"
            "Return JSON: {\"module_code\": \"<complete updated module>\"}"
        )
        schema = {"module_code": "string"}
        return self._run_single(
            workflow_name="model_mutation",
            task_type="mutate_model",
            prompt=prompt,
            schema=schema,
            family_id=family_id,
            lineage_id=lineage_id,
            model_tier="tier_mutate",
        )
