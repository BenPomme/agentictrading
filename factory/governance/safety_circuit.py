"""Circuit breakers — global and family scope.

A circuit breaker trips when a hard threshold is crossed.
Once tripped, it blocks all new work in its scope until reset.

States:
- CLOSED: normal operation
- OPEN:   tripped — blocks new work

Trips are persisted in-memory for the process lifetime.
Resets require explicit calls (operator action or policy reset).
"""
from __future__ import annotations

import enum
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class CircuitState(enum.Enum):
    CLOSED = "closed"   # normal
    OPEN = "open"       # tripped


# ---------------------------------------------------------------------------
# Trip record
# ---------------------------------------------------------------------------

@dataclass
class CircuitEvent:
    """One circuit trip or reset event."""
    timestamp: datetime
    scope: str            # "global" | "family"
    scope_id: str         # "" for global, family_id for family
    state: CircuitState
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "scope": self.scope,
            "scope_id": self.scope_id,
            "state": self.state.value,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

SCOPE_GLOBAL = "global"
SCOPE_FAMILY = "family"


class CircuitBreaker:
    """
    Thread-safe circuit breaker for global and per-family scopes.

    Usage::

        cb = CircuitBreaker()
        if cb.is_tripped_global():
            raise GovernorStopError("global circuit open")
        if cb.is_tripped_family("fam-001"):
            raise GovernorStopError("family circuit open")

        cb.trip_family("fam-001", "daily budget exceeded")
    """

    def __init__(self) -> None:
        self._global_open: bool = False
        self._global_reason: str = ""
        self._family_open: Dict[str, str] = {}   # family_id → reason
        self._events: List[CircuitEvent] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def is_tripped_global(self) -> bool:
        with self._lock:
            return self._global_open

    def is_tripped_family(self, family_id: str) -> bool:
        with self._lock:
            return family_id in self._family_open

    def state_global(self) -> CircuitState:
        return CircuitState.OPEN if self.is_tripped_global() else CircuitState.CLOSED

    def state_family(self, family_id: str) -> CircuitState:
        return CircuitState.OPEN if self.is_tripped_family(family_id) else CircuitState.CLOSED

    # ------------------------------------------------------------------
    # Trip
    # ------------------------------------------------------------------

    def trip_global(self, reason: str) -> None:
        with self._lock:
            if self._global_open:
                return  # already tripped
            self._global_open = True
            self._global_reason = reason
            self._events.append(CircuitEvent(
                timestamp=_utcnow(),
                scope=SCOPE_GLOBAL,
                scope_id="",
                state=CircuitState.OPEN,
                reason=reason,
            ))
        logger.error("CircuitBreaker: GLOBAL circuit OPEN — %s", reason)

    def trip_family(self, family_id: str, reason: str) -> None:
        with self._lock:
            if family_id in self._family_open:
                return  # already tripped
            self._family_open[family_id] = reason
            self._events.append(CircuitEvent(
                timestamp=_utcnow(),
                scope=SCOPE_FAMILY,
                scope_id=family_id,
                state=CircuitState.OPEN,
                reason=reason,
            ))
        logger.error("CircuitBreaker: FAMILY %r circuit OPEN — %s", family_id, reason)

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def reset_global(self) -> None:
        with self._lock:
            self._global_open = False
            self._global_reason = ""
            self._events.append(CircuitEvent(
                timestamp=_utcnow(),
                scope=SCOPE_GLOBAL,
                scope_id="",
                state=CircuitState.CLOSED,
                reason="manual reset",
            ))
        logger.info("CircuitBreaker: GLOBAL circuit reset to CLOSED")

    def reset_family(self, family_id: str) -> None:
        with self._lock:
            self._family_open.pop(family_id, None)
            self._events.append(CircuitEvent(
                timestamp=_utcnow(),
                scope=SCOPE_FAMILY,
                scope_id=family_id,
                state=CircuitState.CLOSED,
                reason="manual reset",
            ))
        logger.info("CircuitBreaker: FAMILY %r circuit reset to CLOSED", family_id)

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def state_summary(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "global": {
                    "state": CircuitState.OPEN.value if self._global_open else CircuitState.CLOSED.value,
                    "reason": self._global_reason or None,
                },
                "open_families": dict(self._family_open),
                "recent_events": [e.to_dict() for e in self._events[-20:]],
            }

    def recent_events(self, limit: int = 20) -> List[CircuitEvent]:
        with self._lock:
            return list(self._events[-limit:])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
