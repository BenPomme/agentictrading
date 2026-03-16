"""RuntimeManager — selects and vends the active agent runtime backend.

Selection order:
1. If FACTORY_RUNTIME_BACKEND=mobkit AND FACTORY_ENABLE_MOBKIT=true
   → return MobkitRuntime
2. Otherwise (default)
   → return LegacyRuntime

The default is always legacy, preserving existing behavior until an explicit
opt-in is configured. An unknown backend name is treated as a config error
and logged, then falls back to legacy.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Union

import config
from factory.runtime.legacy_runtime import LegacyRuntime

logger = logging.getLogger(__name__)

BACKEND_LEGACY = "legacy"
BACKEND_MOBKIT = "mobkit"

_KNOWN_BACKENDS = {BACKEND_LEGACY, BACKEND_MOBKIT}


def _runtime_backend_setting() -> str:
    """Read FACTORY_RUNTIME_BACKEND from config, defaulting to 'legacy'."""
    raw = str(getattr(config, "FACTORY_RUNTIME_BACKEND", BACKEND_LEGACY) or "").strip().lower()
    return raw if raw in _KNOWN_BACKENDS else BACKEND_LEGACY


def _mobkit_enabled() -> bool:
    """Read FACTORY_ENABLE_MOBKIT from config, defaulting to False."""
    return bool(getattr(config, "FACTORY_ENABLE_MOBKIT", False))


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
        self._runtime = self._build_runtime()

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

    def _build_runtime(self):
        requested = self._backend_name
        mobkit_flag = _mobkit_enabled()

        if requested == BACKEND_MOBKIT:
            if not mobkit_flag:
                logger.warning(
                    "FACTORY_RUNTIME_BACKEND=mobkit but FACTORY_ENABLE_MOBKIT=false; "
                    "falling back to legacy runtime"
                )
                self._backend_name = BACKEND_LEGACY
                return LegacyRuntime(self._project_root)
            try:
                from factory.runtime.mobkit_backend import MobkitRuntime
                rt = MobkitRuntime(self._project_root)
                logger.info("RuntimeManager: using mobkit backend")
                return rt
            except Exception:
                logger.exception(
                    "RuntimeManager: failed to initialize MobkitRuntime; "
                    "falling back to legacy runtime"
                )
                self._backend_name = BACKEND_LEGACY
                return LegacyRuntime(self._project_root)

        if requested not in _KNOWN_BACKENDS:
            logger.error(
                "Unknown FACTORY_RUNTIME_BACKEND=%r; falling back to legacy runtime",
                requested,
            )
            self._backend_name = BACKEND_LEGACY

        logger.debug("RuntimeManager: using backend=%s", self._backend_name)
        return LegacyRuntime(self._project_root)
