"""Correlation ID container for tracing a factory cycle end-to-end.

A TraceContext is created once per factory cycle (or per significant
unit of work) and threaded through runtime, governance, and provenance
layers so that all emitted events share the same identifiers.

Correlation IDs:
- cycle_id      : one logical factory research cycle (family + iteration)
- trace_id      : per-task trace (may differ from cycle_id for sub-tasks)
- family_id     : strategy family being worked on
- lineage_id    : specific lineage being mutated/evaluated (optional)
- runtime_run_id: backend-level run identifier (set after task dispatch)
- goldfish_record_id: provenance record in Goldfish (set after write)
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Any, Dict, Optional


def _new_id(prefix: str) -> str:
    """Generate a compact prefixed UUID fragment."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class TraceContext:
    """
    Immutable snapshot of correlation identifiers for one unit of work.

    Prefer ``TraceContext.create(family_id=...)`` for fresh contexts.
    Use ``with_run`` / ``with_goldfish`` to build evolved copies without
    mutating the original.
    """

    cycle_id: str
    trace_id: str
    family_id: str
    lineage_id: Optional[str] = None
    runtime_run_id: Optional[str] = None
    goldfish_record_id: Optional[str] = None

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        *,
        family_id: str,
        lineage_id: Optional[str] = None,
        cycle_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> "TraceContext":
        """Generate a new TraceContext with fresh cycle and trace IDs."""
        return cls(
            cycle_id=cycle_id or _new_id("cyc"),
            trace_id=trace_id or _new_id("trc"),
            family_id=family_id,
            lineage_id=lineage_id,
        )

    # ------------------------------------------------------------------
    # Derived copies (frozen dataclass — use replace())
    # ------------------------------------------------------------------

    def with_lineage(self, lineage_id: str) -> "TraceContext":
        """Return a copy with lineage_id set."""
        return replace(self, lineage_id=lineage_id)

    def with_run(self, runtime_run_id: str) -> "TraceContext":
        """Return a copy with runtime_run_id set (after task dispatch)."""
        return replace(self, runtime_run_id=runtime_run_id)

    def with_goldfish(self, goldfish_record_id: str) -> "TraceContext":
        """Return a copy with goldfish_record_id set (after provenance write)."""
        return replace(self, goldfish_record_id=goldfish_record_id)

    def with_trace(self, trace_id: str) -> "TraceContext":
        """Return a copy with a fresh per-task trace_id."""
        return replace(self, trace_id=trace_id)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "trace_id": self.trace_id,
            "family_id": self.family_id,
            "lineage_id": self.lineage_id,
            "runtime_run_id": self.runtime_run_id,
            "goldfish_record_id": self.goldfish_record_id,
        }
