from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from factory.family_classifier import family_runtime_venue, load_family_config
from factory.model_sandbox import sandbox_load_and_test

if TYPE_CHECKING:
    from factory.contracts import LineageRecord
    from factory.registry import FactoryRegistry


_STOCK_RUNTIME_VENUES = {"yahoo", "yahoo_stocks", "alpaca", "alpaca_stocks"}


@dataclass(frozen=True)
class RuntimeAdmissionResult:
    admitted: bool
    runner_kind: str
    reason: str | None = None
    model_code_path: str | None = None
    model_class_name: str | None = None


def assess_lineage_runtime_admission(
    project_root: Path,
    registry: "FactoryRegistry",
    lineage: "LineageRecord",
) -> RuntimeAdmissionResult:
    family_cfg = load_family_config(project_root, lineage.family_id)
    runtime_venue = str(family_runtime_venue(family_cfg) or "").strip().lower()
    venues = {
        str(item).strip().lower()
        for item in (family_cfg.get("target_venues") or lineage.target_venues or [])
        if str(item).strip()
    }

    genome = registry.load_genome(lineage.lineage_id)
    params = dict(getattr(genome, "parameters", {}) or {})
    model_code_path = str(params.get("model_code_path") or "").strip()
    model_class_name = str(params.get("model_class_name") or "").strip()

    if model_code_path and model_class_name:
        code_path = Path(model_code_path)
        if not code_path.is_absolute():
            code_path = project_root / code_path
        if not code_path.exists():
            return RuntimeAdmissionResult(
                admitted=False,
                runner_kind="dynamic_model",
                reason=f"runtime_model_path_missing:{code_path}",
                model_code_path=str(code_path),
                model_class_name=model_class_name,
            )
        _, errors = sandbox_load_and_test(code_path, model_class_name)
        if errors:
            return RuntimeAdmissionResult(
                admitted=False,
                runner_kind="dynamic_model",
                reason=f"runtime_model_invalid:{errors[0]}",
                model_code_path=str(code_path),
                model_class_name=model_class_name,
            )
        return RuntimeAdmissionResult(
            admitted=True,
            runner_kind="dynamic_model",
            model_code_path=str(code_path),
            model_class_name=model_class_name,
        )

    if "binance" in venues:
        return RuntimeAdmissionResult(admitted=True, runner_kind="funding_builtin")

    if venues.intersection(_STOCK_RUNTIME_VENUES) or runtime_venue in _STOCK_RUNTIME_VENUES:
        return RuntimeAdmissionResult(
            admitted=False,
            runner_kind="stock_hmm_fallback",
            reason="runtime_model_missing:stock_fallback_requires_explicit_runtime_model",
        )

    return RuntimeAdmissionResult(
        admitted=False,
        runner_kind="generic_fallback",
        reason="runtime_model_missing:no_runtime_safe_runner",
    )
