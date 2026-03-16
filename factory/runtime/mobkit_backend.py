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
}

_TIER_TO_MODEL_CLASS: Dict[str, str] = {
    "tier1_cheap":    "TASK_CHEAP",
    "tier2_standard": "TASK_STANDARD",
    "tier3_lead":     "TASK_EXPENSIVE",
}


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
                        "You are a critical reviewer of a trading-strategy proposal.",
                        "Return a JSON object: {\"flags\": [\"<issue>\", ...], \"severity\": \"low|medium|high\"}.",
                        "Be concise. Focus on logical flaws, curve-fitting risk, and unrealistic assumptions.",
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
                        "Produce a JSON critique with keys: "
                        "decision (tweak|retire|promote), confidence (0-1), "
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
                        "Return JSON: {\"module_code\": \"<full python source>\", "
                        "\"class_name\": \"<ClassName>\", \"dependencies\": []}.",
                    ],
                    model_tier="tier2_standard",
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
                        "Return JSON: {\"module_code\": \"<full python source>\"}.",
                    ],
                    model_tier="tier2_standard",
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
        timeout = float(timeout_seconds or self._timeout)

        try:
            return self._loop.run(
                self._async_single_member(
                    member_id=member_id,
                    profile=profile,
                    instructions=[
                        f"You are a {model_tier} agent for task '{task_type}' in AgenticTrading.",
                        "Return ONLY valid JSON matching the schema below.",
                        f"Schema: {json.dumps(schema)}",
                    ],
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

    async def _collect_run_result(self, member_id: str, *, timeout: float) -> str:
        """Stream agent events until RunCompleted, then return result text."""
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
        # Dispatch work.
        await self._handle.send(member_id, prompt)
        # Collect result.
        result_text = await self._collect_run_result(
            member_id, timeout=float(max_tokens * 0.05 + 30)
        )
        # Parse and validate.
        payload = _parse_json_output(result_text, member_id)
        return {
            "payload": payload,
            "member_traces": [
                {
                    "member_id": member_id,
                    "role": profile,
                    "model": None,
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
    ) -> Dict[str, Any]:
        """Execute a multi-member workflow: Lead → Reviewers → Lead synthesis."""
        member_traces: List[Dict[str, Any]] = []
        lead_role = next(
            (r for r in profile.member_roles if r.is_lead),
            profile.member_roles[0],
        )
        reviewer_roles = [r for r in profile.member_roles if not r.is_lead]

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
        await self._handle.send(lead_id, lead_prompt)
        lead_draft = await self._collect_run_result(
            lead_id, timeout=float(lead_role.max_tokens * 0.05 + 60)
        )
        member_traces.append(
            {
                "member_id": lead_id,
                "role": lead_role.role,
                "model": lead_role.model_tier,
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
                await self._handle.send(rev_id, review_prompt)
                review_text = await self._collect_run_result(
                    rev_id, timeout=float(reviewer.max_tokens * 0.05 + 30)
                )
                review_texts.append(f"[{reviewer.role}]: {review_text}")
                member_traces.append(
                    {
                        "member_id": rev_id,
                        "role": reviewer.role,
                        "model": reviewer.model_tier,
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
                member_traces.append(
                    {
                        "member_id": rev_id,
                        "role": reviewer.role,
                        "model": reviewer.model_tier,
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
            await self._handle.send(lead_id, synthesis_prompt)
            final_text = await self._collect_run_result(
                lead_id, timeout=float(lead_role.max_tokens * 0.05 + 60)
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


def _parse_json_output(text: str, member_id: str) -> Dict[str, Any]:
    """Parse JSON from agent text output, stripping markdown fences if present."""
    text = text.strip()
    # Strip markdown code fences.
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
        # If not a dict, wrap it.
        return {"result": result}
    except json.JSONDecodeError as exc:
        raise MobkitSchemaError(
            f"Agent {member_id!r} returned non-JSON output. "
            f"Error: {exc}. Raw (first 300 chars): {text[:300]!r}"
        ) from exc


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
    return AgentRunResult(
        run_id=str(uuid.uuid4()),
        task_type=task_type,
        model_class=model_class,
        provider="mobkit",
        model="mobkit",
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
    ) -> None:
        self._project_root = Path(project_root)
        if backend is None:
            backend = MobkitOrchestratorBackend.create(project_root)
        self._backend = backend

    @property
    def backend_name(self) -> str:
        return self.BACKEND_NAME

    def healthcheck(self) -> bool:
        """Delegate healthcheck to the underlying OrchestratorBackend."""
        return self._backend.healthcheck()

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
                budget_hooks=budget_hooks,
            )
            return _make_run_result(
                task_type=task_type,
                backend_result=result,
                family_id=family_id,
                lineage_id=lineage_id,
                started_at=started_at,
                success=True,
                model_class=model_class,
            )
        except MobkitWorkflowError as exc:
            logger.error("MobkitRuntime.%s failed: %s", task_type, exc)
            return _make_run_result(
                task_type=task_type,
                backend_result={},
                family_id=family_id,
                lineage_id=lineage_id,
                started_at=started_at,
                success=False,
                error=str(exc),
            )

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
                budget_hooks=budget_hooks,
            )
            return _make_run_result(
                task_type=task_type,
                backend_result=result,
                family_id=family_id,
                lineage_id=lineage_id,
                started_at=started_at,
                success=True,
                model_class=model_class,
            )
        except MobkitWorkflowError as exc:
            logger.error("MobkitRuntime.%s failed: %s", task_type, exc)
            return _make_run_result(
                task_type=task_type,
                backend_result={},
                family_id=family_id,
                lineage_id=lineage_id,
                started_at=started_at,
                success=False,
                error=str(exc),
            )

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
            model_tier="tier2_standard",
        )
