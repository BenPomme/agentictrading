"""lineage_projection — local projection cache for Goldfish record references.

Stores the mapping  lineage_id → Goldfish record_id  so that:
- dashboards can display "record exists in Goldfish" without hitting the daemon
- the local registry can serve as fallback when provenance is degraded
- rollback is safe because local registry remains authoritative until cutover

The projection is intentionally thin: it only stores reference pointers,
not full record payloads.  The authoritative data lives in Goldfish.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from factory.contracts import utc_now_iso


@dataclass
class ProvenanceRef:
    """
    Local projection pointer to an authoritative Goldfish record.

    Stored in the registry's provenance_refs directory.
    Allows dashboards / operators to see that a record exists
    without querying the Goldfish daemon.
    """
    lineage_id: str
    family_id: str
    workspace_id: str          # Goldfish workspace (== family_id)
    goldfish_record_id: str    # Authoritative record ID in Goldfish
    evaluation_id: str
    cycle_id: str
    stage: str
    created_at: str = field(default_factory=utc_now_iso)
    # degraded=True means the write was attempted but goldfish was unavailable
    degraded: bool = False
    degraded_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lineage_id": self.lineage_id,
            "family_id": self.family_id,
            "workspace_id": self.workspace_id,
            "goldfish_record_id": self.goldfish_record_id,
            "evaluation_id": self.evaluation_id,
            "cycle_id": self.cycle_id,
            "stage": self.stage,
            "created_at": self.created_at,
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ProvenanceRef":
        return cls(
            lineage_id=str(d.get("lineage_id", "")),
            family_id=str(d.get("family_id", "")),
            workspace_id=str(d.get("workspace_id", "")),
            goldfish_record_id=str(d.get("goldfish_record_id", "")),
            evaluation_id=str(d.get("evaluation_id", "")),
            cycle_id=str(d.get("cycle_id", "")),
            stage=str(d.get("stage", "")),
            created_at=str(d.get("created_at", utc_now_iso())),
            degraded=bool(d.get("degraded", False)),
            degraded_reason=d.get("degraded_reason"),
        )

    @staticmethod
    def make_degraded(
        *,
        lineage_id: str,
        family_id: str,
        evaluation_id: str,
        cycle_id: str,
        stage: str,
        reason: str,
    ) -> "ProvenanceRef":
        """Create a degraded ref when Goldfish is unreachable."""
        return ProvenanceRef(
            lineage_id=lineage_id,
            family_id=family_id,
            workspace_id=family_id,
            goldfish_record_id="",
            evaluation_id=evaluation_id,
            cycle_id=cycle_id,
            stage=stage,
            degraded=True,
            degraded_reason=reason,
        )


class LineageProjectionStore:
    """
    Thread-safe store for ProvenanceRef records on local filesystem.

    Kept separate from FactoryRegistry to keep the projection cache
    cleanly decoupled from core registry logic.
    Path: <factory_root>/provenance_refs/<lineage_id>/latest.json
    """

    def __init__(self, factory_root: Path) -> None:
        self._root = factory_root / "provenance_refs"
        self._lock = threading.Lock()
        self._root.mkdir(parents=True, exist_ok=True)

    def save(self, ref: ProvenanceRef) -> None:
        with self._lock:
            lineage_dir = self._root / ref.lineage_id
            lineage_dir.mkdir(parents=True, exist_ok=True)
            path = lineage_dir / "latest.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(ref.to_dict(), indent=2), encoding="utf-8")
            tmp.replace(path)

    def load(self, lineage_id: str) -> Optional[ProvenanceRef]:
        path = self._root / lineage_id / "latest.json"
        if not path.exists():
            return None
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            return ProvenanceRef.from_dict(d)
        except Exception:
            return None

    def all_refs(self) -> list[ProvenanceRef]:
        refs = []
        for path in sorted(self._root.glob("*/latest.json")):
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
                refs.append(ProvenanceRef.from_dict(d))
            except Exception:
                continue
        return refs
