"""RuntimeManager — selects and vends the active agent runtime backend.

Selection order (Task 06 defaults):
1. If FACTORY_RUNTIME_BACKEND=mobkit AND FACTORY_ENABLE_MOBKIT=true (default)
   → return MobkitRuntime (with injected CostGovernor)
2. If mobkit init fails AND FACTORY_FALLBACK_TO_LEGACY=true (default)
   → emit FALLBACK_ACTIVATED telemetry and return LegacyRuntime
3. If mobkit init fails AND FACTORY_FALLBACK_TO_LEGACY=false
   → raise RuntimeError (hard fail — no hidden degradation)
4. If FACTORY_RUNTIME_BACKEND=legacy explicitly
   → return LegacyRuntime with a deprecation warning

Emergency rollback config:
    FACTORY_RUNTIME_BACKEND=legacy
    FACTORY_ENABLE_GOLDFISH_PROVENANCE=false

Cost governance (Task 04):
RuntimeManager creates a CostGovernor and injects it into the active runtime.
The governor is also accessible via manager.governor for direct checks from
the orchestrator. When FACTORY_ENABLE_STRICT_BUDGETS=false (default), the
governor runs in observe-only mode and never blocks execution.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import config
from factory.governance import CostGovernor
from factory.runtime.legacy_runtime import LegacyRuntime
from factory.telemetry.run_logger import default_logger as _tel

logger = logging.getLogger(__name__)

BACKEND_LEGACY = "legacy"
BACKEND_MOBKIT = "mobkit"

_KNOWN_BACKENDS = {BACKEND_LEGACY, BACKEND_MOBKIT}


def _runtime_backend_setting() -> str:
    """Read FACTORY_RUNTIME_BACKEND from config, defaulting to 'mobkit' (Task 06)."""
    raw = str(getattr(config, "FACTORY_RUNTIME_BACKEND", BACKEND_MOBKIT) or "").strip().lower()
    return raw if raw in _KNOWN_BACKENDS else BACKEND_MOBKIT


def _mobkit_enabled() -> bool:
    """Read FACTORY_ENABLE_MOBKIT from config, defaulting to True (Task 06)."""
    return bool(getattr(config, "FACTORY_ENABLE_MOBKIT", True))


def _fallback_to_legacy_allowed() -> bool:
    """Read FACTORY_FALLBACK_TO_LEGACY from config, defaulting to True."""
    return bool(getattr(config, "FACTORY_FALLBACK_TO_LEGACY", True))


class RuntimeManager:
    """
    Factory/selector for agent runtime backends.

    Usage::

        runtime = RuntimeManager.create(project_root)
        result = runtime.generate_proposal(...)

    The returned object implements the AgentRuntime protocol.
    """

    def __init__(self, project_root: str | Path) -> None:
        self._project_root = Path(project_root)
        self._backend_name = _runtime_backend_setting()
        self._governor = CostGovernor.create()
        self._runtime = self._build_runtime()
        _tel.backend_selected(self._backend_name)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def create(cls, project_root: str | Path) -> "RuntimeManager":
        """Convenience constructor that mirrors FactoryOrchestrator init pattern."""
        return cls(project_root)

    @property
    def backend_name(self) -> str:
        """Name of the active backend, as it will appear in logs and envelopes."""
        return self._backend_name

    @property
    def governor(self) -> CostGovernor:
        """The cost governor for this runtime. Always present regardless of backend."""
        return self._governor

    @property
    def runtime(self):
        """The resolved runtime instance (LegacyRuntime or MobkitRuntime)."""
        return self._runtime

    def healthcheck(self) -> bool:
        """
        Quick sanity check on the active backend.
        Legacy runtime is always considered healthy (it fails at invocation time).
        MobkitRuntime delegates to MobkitOrchestratorBackend.healthcheck().
        """
        if self._backend_name == BACKEND_LEGACY:
            return True
        if self._backend_name == BACKEND_MOBKIT:
            try:
                return self._runtime.healthcheck()
            except Exception:
                logger.exception("RuntimeManager: mobkit healthcheck failed")
                return False
        return False

    # ------------------------------------------------------------------
    # Delegation — mirror AgentRuntime protocol for direct use
    # ------------------------------------------------------------------

    def __getattr__(self, name: str):
        """
        Delegate any AgentRuntime method calls directly to the underlying runtime.
        This lets orchestrator code call manager.generate_proposal(...)
        without needing to unwrap manager.runtime first.
        """
        inner = object.__getattribute__(self, "_runtime")
        return getattr(inner, name)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fallback_to_legacy(self, reason: str) -> LegacyRuntime:
        """
        Gracefully degrade to the legacy runtime, respecting FACTORY_FALLBACK_TO_LEGACY.

        Emits a FALLBACK_ACTIVATED telemetry event and logs at WARNING level.
        If FACTORY_FALLBACK_TO_LEGACY=false, raises RuntimeError instead.
        """
        if not _fallback_to_legacy_allowed():
            raise RuntimeError(
                f"RuntimeManager: backend {self._backend_name!r} unavailable and "
                f"FACTORY_FALLBACK_TO_LEGACY=false — refusing silent fallback. "
                f"Reason: {reason}"
            )
        logger.warning(
            "RuntimeManager: falling back to legacy runtime. Reason: %s", reason
        )
        _tel.fallback_activated(self._backend_name, BACKEND_LEGACY, reason=reason)
        self._backend_name = BACKEND_LEGACY
        return LegacyRuntime(self._project_root)

    def _build_runtime(self):
        requested = self._backend_name
        mobkit_flag = _mobkit_enabled()

        if requested == BACKEND_MOBKIT:
            if not mobkit_flag:
                return self._fallback_to_legacy("FACTORY_ENABLE_MOBKIT=false")
            try:
                from factory.runtime.mobkit_backend import MobkitRuntime
                rt = MobkitRuntime(self._project_root, governor=self._governor)
                gateway_bin = str(getattr(config, "FACTORY_MOBKIT_GATEWAY_BIN", "") or "")
                mob_cfg = str(getattr(config, "FACTORY_MOBKIT_CONFIG_PATH", "") or "")
                logger.info(
                    "RuntimeManager: using mobkit backend "
                    "(gateway=%s, mob_config=%s)",
                    gateway_bin or "<none>",
                    mob_cfg or "<none>",
                )
                return rt
            except Exception as exc:
                logger.exception("RuntimeManager: failed to initialize MobkitRuntime")
                return self._fallback_to_legacy(f"MobkitRuntime init failed: {exc}")

        if requested not in _KNOWN_BACKENDS:
            logger.error(
                "Unknown FACTORY_RUNTIME_BACKEND=%r; falling back to legacy runtime",
                requested,
            )
            return self._fallback_to_legacy(f"unknown backend: {requested!r}")

        # Explicit legacy selection — warn that legacy is now deprecated as the default
        logger.warning(
            "RuntimeManager: FACTORY_RUNTIME_BACKEND=legacy — "
            "legacy runtime is deprecated as default (Task 06 cutover); "
            "set FACTORY_RUNTIME_BACKEND=mobkit to use the new backend"
        )
        logger.debug("RuntimeManager: using backend=%s", self._backend_name)
        return LegacyRuntime(self._project_root)
