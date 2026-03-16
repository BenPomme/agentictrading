"""GoldfishClient — adapter wrapping the lukacf/goldfish provenance backend.

Design principles:
- Goldfish is an MCP server (daemon + unix socket).  The integration path is
  ``DaemonConnection.call_tool(tool_name, params)`` from goldfish.mcp_proxy.
- If the goldfish package (or its daemon module) is not installed, every method
  raises GoldfishUnavailableError.  This is an explicit, operator-visible error,
  not a silent no-op.
- ProvenanceService wraps GoldfishClient and applies the feature-flag gate:
  when FACTORY_ENABLE_GOLDFISH_PROVENANCE=false, all calls are no-ops.
- Tests can inject a mock GoldfishClient via ProvenanceService.

REAL API NOTES (Task 02 reconciliation, 2026-03-16):
  Goldfish exposes tools via its daemon (unix socket HTTP).  Key mappings:
    create_workspace(name, goal, reason)          ← ensure_workspace()
    log_thought(thought, workspace, run_id=None)  ← create_run() + finalize_run()
    finalize_run(record_or_run_id, results)       ← unused (needs a live Goldfish run)
    tag_record(ref, tag)                          ← tag_record() (single tag per call)
    inspect_record(ref, workspace=None, ...)      ← inspect_record()
    list_history(workspace, ...)                  ← list_history()
    initialize_project(project_name, project_root)← ensure_project()

  Because AgenticTrading runs its own evaluations (backtests) and does NOT
  submit training jobs to Goldfish, ``create_run`` and ``finalize_run`` are
  mapped to ``log_thought`` calls that record structured provenance in the
  Goldfish workspace audit trail.

Rollback: set FACTORY_ENABLE_GOLDFISH_PROVENANCE=false.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class GoldfishError(Exception):
    """Base class for all provenance layer errors."""


class GoldfishUnavailableError(GoldfishError):
    """Raised when the goldfish library or daemon is not reachable."""


class GoldfishWriteError(GoldfishError):
    """Raised when a write to goldfish fails after retries."""


class GoldfishRecordNotFoundError(GoldfishError):
    """Raised when an expected record cannot be located."""


# ---------------------------------------------------------------------------
# Availability check (cached once per process)
# ---------------------------------------------------------------------------

_GOLDFISH_AVAILABLE: Optional[bool] = None
_goldfish_module: Any = None


def _check_goldfish_available() -> bool:
    """Return True if the goldfish package and its daemon module are importable."""
    global _GOLDFISH_AVAILABLE, _goldfish_module
    if _GOLDFISH_AVAILABLE is None:
        try:
            import goldfish as _gf  # type: ignore[import]
            # Verify the daemon/proxy modules are present (real Goldfish install).
            from goldfish.mcp_proxy import DaemonConnection  # noqa: F401  # type: ignore[import]
            from goldfish.daemon import get_socket_path  # noqa: F401  # type: ignore[import]
            _goldfish_module = _gf
            _GOLDFISH_AVAILABLE = True
            logger.debug("goldfish library found: %s", getattr(_gf, "__version__", "unknown"))
        except ImportError:
            _GOLDFISH_AVAILABLE = False
            logger.warning(
                "goldfish library not installed. Provenance writes will raise "
                "GoldfishUnavailableError. Install lukacf/goldfish to enable."
            )
    return bool(_GOLDFISH_AVAILABLE)


def _require_goldfish() -> Any:
    """Return the goldfish module or raise GoldfishUnavailableError."""
    if not _check_goldfish_available():
        raise GoldfishUnavailableError(
            "The goldfish Python package is not installed. "
            "Either install lukacf/goldfish or set FACTORY_ENABLE_GOLDFISH_PROVENANCE=false "
            "to run in degraded-provenance mode."
        )
    return _goldfish_module


# ---------------------------------------------------------------------------
# GoldfishClient
# ---------------------------------------------------------------------------


class GoldfishClient:
    """
    Adapter for the lukacf/goldfish experiment provenance backend.

    Connects to the Goldfish daemon via Unix socket (``DaemonConnection``) and
    calls MCP tools by name.  If goldfish is not installed, every method raises
    GoldfishUnavailableError so callers can see the failure explicitly.

    The adapter keeps our own method names stable (``create_run``,
    ``finalize_run``, ...) while mapping them to the real Goldfish tool API:

      ensure_workspace   → create_workspace(name, goal, reason)
      create_run         → log_thought(thought=RUN_CREATED …, workspace)
      finalize_run       → log_thought(thought=RUN_FINALIZED …, workspace)
      tag_record         → tag_record(ref, tag) — one call per tag
      inspect_record     → inspect_record(ref, workspace)
      list_history       → list_history(workspace, limit)
      log_thought        → log_thought(thought, workspace)
      ensure_project     → initialize_project(project_name, project_root)
      healthcheck        → daemon /health endpoint
    """

    def __init__(self, project_root: str | Path) -> None:
        self._project_root = Path(project_root)
        self._goldfish_root = self._resolve_goldfish_root()
        self._conn: Any = None  # lazy; set on first _get_conn() call
        # Probe availability at construction time so startup logs capture the state.
        _check_goldfish_available()

    def _resolve_goldfish_root(self) -> Path:
        raw = str(getattr(config, "FACTORY_GOLDFISH_PROJECT_ROOT", "") or "").strip()
        if raw:
            p = Path(raw)
            return p if p.is_absolute() else self._project_root / p
        fallback = str(getattr(config, "FACTORY_GOLDFISH_ROOT", "research/goldfish") or "")
        p = Path(fallback)
        return p if p.is_absolute() else self._project_root / p

    def _get_conn(self) -> Any:
        """Return a live DaemonConnection, raising GoldfishUnavailableError if unavailable."""
        _require_goldfish()  # raises if not installed
        if self._conn is None:
            try:
                from goldfish.mcp_proxy import DaemonConnection  # type: ignore[import]
                from goldfish.daemon import get_socket_path  # type: ignore[import]
                socket_path = get_socket_path(self._goldfish_root)
                self._conn = DaemonConnection(socket_path, self._goldfish_root)
            except Exception as exc:
                raise GoldfishUnavailableError(
                    f"Cannot connect to Goldfish daemon at {self._goldfish_root}: {exc}"
                ) from exc
        return self._conn

    def _call_tool(self, tool_name: str, **params: Any) -> Any:
        """Call a Goldfish MCP tool via the daemon connection."""
        conn = self._get_conn()
        return conn.call_tool(tool_name, params)

    # ------------------------------------------------------------------
    # Project / daemon lifecycle
    # ------------------------------------------------------------------

    def ensure_project(self) -> None:
        """Initialize the goldfish project in the configured root directory."""
        try:
            self._call_tool(
                "initialize_project",
                project_name=self._goldfish_root.name,
                project_root=str(self._goldfish_root),
            )
        except GoldfishUnavailableError:
            raise
        except Exception as exc:
            raise GoldfishWriteError(f"ensure_project failed: {exc}") from exc

    def ensure_daemon(self) -> None:
        """Start or verify the goldfish daemon is running."""
        try:
            self._get_conn()  # DaemonConnection constructor spawns daemon if needed
        except GoldfishUnavailableError:
            raise
        except Exception as exc:
            raise GoldfishUnavailableError(f"ensure_daemon failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Workspace management
    # ------------------------------------------------------------------

    def ensure_workspace(
        self,
        *,
        workspace_id: str,
        thesis: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create or retrieve a Goldfish workspace for a strategy family."""
        try:
            result = self._call_tool(
                "create_workspace",
                name=workspace_id,
                goal=thesis,
                reason=f"AgenticTrading strategy family workspace: {workspace_id}",
            )
            return dict(result or {})
        except GoldfishUnavailableError:
            raise
        except Exception as exc:
            raise GoldfishWriteError(f"ensure_workspace({workspace_id!r}) failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def create_run(
        self,
        *,
        workspace_id: str,
        run_id: str,
        metadata: Dict[str, Any],
    ) -> str:
        """
        Record the start of an evaluation run in Goldfish.

        Goldfish does not have a standalone ``create_run`` provenance tool —
        its ``run()`` tool submits actual compute jobs.  Instead we record the
        run metadata as a structured ``log_thought`` in the workspace.

        Returns ``run_id`` unchanged so callers can use it as a stable key.
        """
        try:
            thought = f"RUN_CREATED run_id={run_id}\n{json.dumps(metadata, default=str)}"
            self._call_tool("log_thought", thought=thought, workspace=workspace_id)
            return run_id
        except GoldfishUnavailableError:
            raise
        except Exception as exc:
            raise GoldfishWriteError(f"create_run({run_id!r}) failed: {exc}") from exc

    def finalize_run(
        self,
        *,
        run_id: str,
        workspace_id: str,
        result: Dict[str, Any],
        tags: Optional[List[str]] = None,
    ) -> str:
        """
        Record the finalized result of an evaluation run in Goldfish.

        Maps to a structured ``log_thought`` (see create_run note above).
        Returns ``run_id`` as the record_id for downstream reference.
        """
        try:
            thought = f"RUN_FINALIZED run_id={run_id}\n{json.dumps(result, default=str)}"
            self._call_tool("log_thought", thought=thought, workspace=workspace_id)
            if tags:
                for tag in tags:
                    try:
                        self._call_tool("tag_record", ref=run_id, tag=tag)
                    except Exception:
                        pass  # Tags are best-effort; don't fail finalization for a tag error
            return run_id
        except GoldfishUnavailableError:
            raise
        except Exception as exc:
            raise GoldfishWriteError(f"finalize_run({run_id!r}) failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Record inspection
    # ------------------------------------------------------------------

    def inspect_record(self, *, record_id: str, workspace_id: str) -> Dict[str, Any]:
        """Fetch a record by ID."""
        try:
            result = self._call_tool("inspect_record", ref=record_id, workspace=workspace_id)
            return dict(result or {})
        except GoldfishUnavailableError:
            raise
        except Exception as exc:
            raise GoldfishRecordNotFoundError(f"inspect_record({record_id!r}) failed: {exc}") from exc

    def list_history(
        self,
        *,
        workspace_id: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Return recent records for a workspace."""
        try:
            result = self._call_tool("list_history", workspace=workspace_id, limit=limit)
            return list(result or [])
        except GoldfishUnavailableError:
            raise
        except Exception as exc:
            raise GoldfishError(f"list_history({workspace_id!r}) failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Tags and notes
    # ------------------------------------------------------------------

    def tag_record(
        self,
        *,
        record_id: str,
        workspace_id: str,
        tags: List[str],
    ) -> None:
        """Attach tags to a record.  Calls tag_record once per tag (real API is single-tag)."""
        try:
            for tag in tags:
                self._call_tool("tag_record", ref=record_id, tag=tag)
        except GoldfishUnavailableError:
            raise
        except Exception as exc:
            raise GoldfishWriteError(f"tag_record({record_id!r}) failed: {exc}") from exc

    def log_thought(
        self,
        *,
        workspace_id: str,
        thought: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log a durable research note/thought into a workspace."""
        try:
            full_thought = thought
            if metadata:
                full_thought += "\n" + json.dumps(metadata, default=str)
            self._call_tool("log_thought", thought=full_thought, workspace=workspace_id)
        except GoldfishUnavailableError:
            raise
        except Exception as exc:
            raise GoldfishWriteError(f"log_thought failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Healthcheck
    # ------------------------------------------------------------------

    def healthcheck(self) -> bool:
        """Return True if goldfish is available and daemon is reachable."""
        if not _check_goldfish_available():
            return False
        try:
            conn = self._get_conn()
            conn._health_check()
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# NullGoldfishClient — no-op stub used when provenance is disabled
# ---------------------------------------------------------------------------


class NullGoldfishClient:
    """
    No-op provenance client used when FACTORY_ENABLE_GOLDFISH_PROVENANCE=false.

    Every method logs at DEBUG level and returns empty/stub responses.
    This allows the factory to run without provenance while retaining
    the same call surface as GoldfishClient.
    """

    def ensure_project(self) -> None:
        logger.debug("NullGoldfishClient: ensure_project (provenance disabled)")

    def ensure_daemon(self) -> None:
        logger.debug("NullGoldfishClient: ensure_daemon (provenance disabled)")

    def ensure_workspace(self, *, workspace_id: str, thesis: str, metadata=None) -> Dict[str, Any]:
        logger.debug("NullGoldfishClient: ensure_workspace(%s) (provenance disabled)", workspace_id)
        return {"workspace_id": workspace_id, "active": False, "provenance_disabled": True}

    def create_run(self, *, workspace_id: str, run_id: str, metadata: Dict[str, Any]) -> str:
        logger.debug("NullGoldfishClient: create_run(%s) (provenance disabled)", run_id)
        return run_id

    def finalize_run(self, *, run_id: str, workspace_id: str, result: Dict[str, Any], tags=None) -> str:
        logger.debug("NullGoldfishClient: finalize_run(%s) (provenance disabled)", run_id)
        return run_id

    def inspect_record(self, *, record_id: str, workspace_id: str) -> Dict[str, Any]:
        return {}

    def list_history(self, *, workspace_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        return []

    def tag_record(self, *, record_id: str, workspace_id: str, tags: List[str]) -> None:
        logger.debug("NullGoldfishClient: tag_record(%s, %s) (provenance disabled)", record_id, tags)

    def log_thought(self, *, workspace_id: str, thought: str, metadata=None) -> None:
        logger.debug("NullGoldfishClient: log_thought (provenance disabled)")

    def healthcheck(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# ProvenanceService — the high-level facade used by the orchestrator
# ---------------------------------------------------------------------------


class ProvenanceService:
    """
    High-level provenance service consumed by the factory orchestrator.

    Wraps GoldfishClient and applies:
    - feature-flag gating (FACTORY_ENABLE_GOLDFISH_PROVENANCE)
    - error surfacing policy (fail vs warn based on config)
    - correlation ID propagation
    - projection cache updates (via registry callbacks)

    Usage::

        svc = ProvenanceService.create(project_root)
        svc.record_evaluation(bundle, lineage, cycle_id=cycle_id)
        svc.record_retirement(lineage, memory, ...)
        svc.record_promotion(lineage, stage, ...)
        svc.record_learning_note(memory)
    """

    def __init__(
        self,
        client: "GoldfishClient | NullGoldfishClient",
        *,
        enabled: bool,
        fail_on_error: bool = False,
    ) -> None:
        self._client = client
        self._enabled = enabled
        self._fail_on_error = fail_on_error

    @classmethod
    def create(cls, project_root: str | Path) -> "ProvenanceService":
        """Factory constructor respecting current config flags."""
        enabled = bool(getattr(config, "FACTORY_ENABLE_GOLDFISH_PROVENANCE", False))
        fail_on_error = not bool(getattr(config, "FACTORY_FALLBACK_TO_LEGACY", True))
        if enabled:
            client: GoldfishClient | NullGoldfishClient = GoldfishClient(project_root)
        else:
            client = NullGoldfishClient()
        return cls(client, enabled=enabled, fail_on_error=fail_on_error)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def healthcheck(self) -> bool:
        return self._client.healthcheck()

    # ------------------------------------------------------------------
    # Domain-level provenance methods
    # ------------------------------------------------------------------

    def ensure_family_workspace(self, *, family_id: str, thesis: str) -> Dict[str, Any]:
        """Ensure Goldfish workspace exists for a family."""
        return self._call(
            "ensure_workspace",
            workspace_id=family_id,
            thesis=thesis,
        )

    def record_evaluation(
        self,
        *,
        workspace_id: str,
        run_id: str,
        lineage_id: str,
        family_id: str,
        cycle_id: str,
        evaluation_payload: Dict[str, Any],
        correlation: Dict[str, Any],
    ) -> Optional[str]:
        """
        Record one evaluation cycle in Goldfish provenance.
        Returns the record_id (== run_id), or None if provenance is disabled or errored.
        """
        if not self._enabled:
            return None
        metadata = {
            "lineage_id": lineage_id,
            "family_id": family_id,
            "cycle_id": cycle_id,
            **correlation,
        }
        created = self._call("create_run", workspace_id=workspace_id, run_id=run_id, metadata=metadata)
        # If create_run failed and was suppressed, stop here — don't attempt finalize.
        if created is None and not self._fail_on_error:
            return None
        record_id = self._call(
            "finalize_run",
            run_id=run_id,
            workspace_id=workspace_id,
            result=evaluation_payload,
            tags=["evaluation"],
        )
        return record_id

    def record_retirement(
        self,
        *,
        workspace_id: str,
        record_id: str,
        lineage_id: str,
        family_id: str,
        reason: str,
        cost_summary: Dict[str, Any],
        best_metrics: Dict[str, Any],
        lessons: List[str],
    ) -> None:
        """Tag a record as retired and log the retirement rationale as a thought."""
        if not self._enabled:
            return
        self._call(
            "tag_record",
            record_id=record_id,
            workspace_id=workspace_id,
            tags=["retired"],
        )
        thought = (
            f"RETIREMENT [{lineage_id}]: {reason}\n"
            f"Best metrics: {best_metrics}\n"
            f"Lessons: {'; '.join(lessons)}"
        )
        self._call("log_thought", workspace_id=workspace_id, thought=thought, metadata={
            "event": "retirement",
            "lineage_id": lineage_id,
            "family_id": family_id,
            "reason": reason,
            "cost_summary": cost_summary,
        })

    def record_promotion(
        self,
        *,
        workspace_id: str,
        record_id: str,
        lineage_id: str,
        family_id: str,
        from_stage: str,
        to_stage: str,
        decision: Dict[str, Any],
    ) -> None:
        """Tag a record as promoted and log the promotion decision."""
        if not self._enabled:
            return
        self._call(
            "tag_record",
            record_id=record_id,
            workspace_id=workspace_id,
            tags=[f"promoted_to_{to_stage}"],
        )
        thought = (
            f"PROMOTION [{lineage_id}]: {from_stage} → {to_stage}\n"
            f"Decision: {decision.get('reasons', [])}"
        )
        self._call("log_thought", workspace_id=workspace_id, thought=thought, metadata={
            "event": "promotion",
            "lineage_id": lineage_id,
            "family_id": family_id,
            "from_stage": from_stage,
            "to_stage": to_stage,
        })

    def record_learning_note(
        self,
        *,
        workspace_id: str,
        lineage_id: str,
        family_id: str,
        outcome: str,
        summary: str,
        domains: List[str],
        recommendations: List[str],
    ) -> None:
        """Log a learning memory entry as a Goldfish thought."""
        if not self._enabled:
            return
        thought = (
            f"LEARNING [{family_id}/{lineage_id}]: {outcome}\n"
            f"{summary}\n"
            f"Domains: {', '.join(domains)}\n"
            f"Recommendations: {'; '.join(recommendations)}"
        )
        self._call("log_thought", workspace_id=workspace_id, thought=thought, metadata={
            "event": "learning_note",
            "lineage_id": lineage_id,
            "family_id": family_id,
            "outcome": outcome,
            "domains": domains,
        })

    # ------------------------------------------------------------------
    # Internal error handling
    # ------------------------------------------------------------------

    def _call(self, method: str, **kwargs: Any) -> Any:
        """
        Call a GoldfishClient method with uniform error handling.

        If fail_on_error=True → re-raises GoldfishError (visible operator signal).
        If fail_on_error=False → logs warning and returns None.
        """
        fn = getattr(self._client, method, None)
        if fn is None:
            raise AttributeError(f"GoldfishClient has no method {method!r}")
        try:
            return fn(**kwargs)
        except GoldfishUnavailableError:
            msg = (
                "Goldfish provenance is enabled (FACTORY_ENABLE_GOLDFISH_PROVENANCE=true) "
                "but the goldfish library is not installed or daemon is unreachable. "
                "Install lukacf/goldfish or set FACTORY_ENABLE_GOLDFISH_PROVENANCE=false."
            )
            logger.error(msg)
            if self._fail_on_error:
                raise
            return None
        except GoldfishError as exc:
            logger.error("Goldfish provenance write failed [%s]: %s", method, exc)
            if self._fail_on_error:
                raise
            return None
        except Exception as exc:
            logger.error("Unexpected goldfish error [%s]: %s", method, exc)
            if self._fail_on_error:
                raise GoldfishWriteError(f"Unexpected error in {method}: {exc}") from exc
            return None
