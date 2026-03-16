"""Thread-safe budget ledger — tracks planned and actual usage.

The ledger is in-memory and process-scoped. It persists for the lifetime
of the factory process and is reset on restart.  A maximum-entry cap
prevents unbounded growth in long-running processes.

Scope constants: SCOPE_GLOBAL | SCOPE_FAMILY | SCOPE_LINEAGE | SCOPE_TASK | SCOPE_MEMBER
Event constants:  EVENT_PLANNED | EVENT_ACTUAL | EVENT_DOWNGRADE | EVENT_STOP | EVENT_FALLBACK
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCOPE_GLOBAL = "global"
SCOPE_FAMILY = "family"
SCOPE_LINEAGE = "lineage"
SCOPE_TASK = "task"
SCOPE_MEMBER = "member"

EVENT_PLANNED = "planned"
EVENT_ACTUAL = "actual"
EVENT_DOWNGRADE = "downgrade"
EVENT_STOP = "stop"
EVENT_FALLBACK = "fallback"
EVENT_CIRCUIT_TRIP = "circuit_trip"


# ---------------------------------------------------------------------------
# Data contracts
# ---------------------------------------------------------------------------

@dataclass
class LedgerEntry:
    """One recorded budget event."""
    timestamp: datetime
    scope: str
    scope_id: str           # e.g. "global", family_id, lineage_id
    event_type: str
    task_type: str
    tokens: int = 0
    estimated_cost_usd: float = 0.0
    downgrade_reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "scope": self.scope,
            "scope_id": self.scope_id,
            "event_type": self.event_type,
            "task_type": self.task_type,
            "tokens": self.tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "downgrade_reason": self.downgrade_reason,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

class BudgetLedger:
    """
    Thread-safe in-memory ledger for the cost governance layer.

    Usage::

        ledger = BudgetLedger()
        ledger.record(LedgerEntry(
            timestamp=utcnow(), scope=SCOPE_FAMILY, scope_id="fam-001",
            event_type=EVENT_ACTUAL, task_type="generate_proposal",
            tokens=1500, estimated_cost_usd=0.005,
        ))
        tokens, usd = ledger.get_daily_usage(SCOPE_FAMILY, "fam-001")
    """

    def __init__(self, max_entries: int = 10_000) -> None:
        self._entries: List[LedgerEntry] = []
        self._lock = threading.Lock()
        self._max_entries = max_entries

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def record(self, entry: LedgerEntry) -> None:
        """Append one ledger entry, evicting oldest if at capacity."""
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self._max_entries:
                # Evict oldest 10 % to avoid repeated single-eviction overhead.
                evict = max(1, self._max_entries // 10)
                del self._entries[:evict]

    def record_planned(
        self,
        *,
        scope: str,
        scope_id: str,
        task_type: str,
        tokens: int,
        estimated_cost_usd: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.record(LedgerEntry(
            timestamp=_utcnow(),
            scope=scope,
            scope_id=scope_id,
            event_type=EVENT_PLANNED,
            task_type=task_type,
            tokens=tokens,
            estimated_cost_usd=estimated_cost_usd,
            metadata=metadata or {},
        ))

    def record_actual(
        self,
        *,
        scope: str,
        scope_id: str,
        task_type: str,
        tokens: int,
        estimated_cost_usd: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.record(LedgerEntry(
            timestamp=_utcnow(),
            scope=scope,
            scope_id=scope_id,
            event_type=EVENT_ACTUAL,
            task_type=task_type,
            tokens=tokens,
            estimated_cost_usd=estimated_cost_usd,
            metadata=metadata or {},
        ))

    def record_downgrade(
        self,
        *,
        scope: str,
        scope_id: str,
        task_type: str,
        reason: str,
        prior_tier: Optional[str] = None,
        new_tier: Optional[str] = None,
    ) -> None:
        self.record(LedgerEntry(
            timestamp=_utcnow(),
            scope=scope,
            scope_id=scope_id,
            event_type=EVENT_DOWNGRADE,
            task_type=task_type,
            downgrade_reason=reason,
            metadata={
                "prior_tier": prior_tier,
                "new_tier": new_tier,
            },
        ))

    def record_stop(
        self,
        *,
        scope: str,
        scope_id: str,
        task_type: str,
        reason: str,
    ) -> None:
        self.record(LedgerEntry(
            timestamp=_utcnow(),
            scope=scope,
            scope_id=scope_id,
            event_type=EVENT_STOP,
            task_type=task_type,
            downgrade_reason=reason,
        ))

    def record_circuit_trip(
        self,
        *,
        scope: str,
        scope_id: str,
        reason: str,
    ) -> None:
        self.record(LedgerEntry(
            timestamp=_utcnow(),
            scope=scope,
            scope_id=scope_id,
            event_type=EVENT_CIRCUIT_TRIP,
            task_type="circuit_breaker",
            downgrade_reason=reason,
        ))

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_usage_since(
        self,
        scope: str,
        scope_id: str,
        since: datetime,
    ) -> Tuple[int, float]:
        """Return (total_tokens, total_cost_usd) for ACTUAL events in scope since `since`."""
        with self._lock:
            entries = list(self._entries)

        tokens = 0
        cost = 0.0
        for e in entries:
            if (
                e.scope == scope
                and e.scope_id == scope_id
                and e.event_type == EVENT_ACTUAL
                and e.timestamp >= since
            ):
                tokens += e.tokens
                cost += e.estimated_cost_usd
        return tokens, cost

    def get_daily_usage(self, scope: str, scope_id: str) -> Tuple[int, float]:
        """Return (tokens, usd) for today UTC for ACTUAL events."""
        today = _today_utc()
        return self.get_usage_since(scope, scope_id, today)

    def count_events_since(
        self,
        scope: str,
        scope_id: str,
        event_type: str,
        since: datetime,
    ) -> int:
        """Count events of a given type in a scope since `since`."""
        with self._lock:
            entries = list(self._entries)
        return sum(
            1 for e in entries
            if e.scope == scope
            and e.scope_id == scope_id
            and e.event_type == event_type
            and e.timestamp >= since
        )

    def recent_entries(self, limit: int = 50) -> List[LedgerEntry]:
        """Return the most recent `limit` entries."""
        with self._lock:
            return list(self._entries[-limit:])

    def snapshot(self) -> Dict[str, Any]:
        """Summary snapshot for operator visibility."""
        with self._lock:
            entries = list(self._entries)

        # Aggregate daily actual usage by scope_id.
        today = _today_utc()
        actuals: Dict[str, Dict[str, float]] = {}
        downgrade_count = 0
        stop_count = 0
        circuit_count = 0

        for e in entries:
            if e.timestamp < today:
                continue
            if e.event_type == EVENT_ACTUAL:
                key = f"{e.scope}:{e.scope_id}"
                if key not in actuals:
                    actuals[key] = {"tokens": 0, "usd": 0.0}
                actuals[key]["tokens"] += e.tokens
                actuals[key]["usd"] += e.estimated_cost_usd
            elif e.event_type == EVENT_DOWNGRADE:
                downgrade_count += 1
            elif e.event_type == EVENT_STOP:
                stop_count += 1
            elif e.event_type == EVENT_CIRCUIT_TRIP:
                circuit_count += 1

        return {
            "today_actuals": actuals,
            "downgrade_events_today": downgrade_count,
            "stop_events_today": stop_count,
            "circuit_trip_events_today": circuit_count,
            "total_entries": len(entries),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _today_utc() -> datetime:
    """Midnight UTC today."""
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)
