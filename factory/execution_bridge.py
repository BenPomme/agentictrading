from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Tuple

import config
from factory.execution_targets import resolve_target_portfolio
from factory.manifests import candidate_context_refs_for_portfolio
from factory.runtime_execution import RuntimeProcessManager, get_runtime_portfolio_spec
from factory.runtime_mode import current_agentic_factory_runtime_mode


class FactoryExecutionBridge:
    """Keeps the paper execution plane aligned with active factory families."""

    def __init__(self, process_manager: RuntimeProcessManager | None = None) -> None:
        self._process_manager = process_manager or RuntimeProcessManager()
        self._auto_start = bool(getattr(config, "FACTORY_EXECUTION_AUTOSTART_ENABLED", True))

    def _target_lane_kind(self, item: Dict[str, Any]) -> str:
        return next(
            (
                str(row.get("runtime_lane_kind") or "").strip()
                for row in list(item.get("lineages") or [])
                if str(row.get("runtime_lane_kind") or "").strip()
            ),
            str(item.get("runtime_lane_kind") or "").strip(),
        )

    def _isolated_lane_liveness_penalty(self, item: Dict[str, Any]) -> int:
        lane_kind = self._target_lane_kind(item)
        if lane_kind != "isolated_challenger":
            return 0
        stale_hours = float(getattr(config, "FACTORY_RUNTIME_ALIAS_STALE_HOURS", 4.0) or 4.0)
        lineages = list(item.get("lineages") or [])
        activation_statuses = {
            str(row.get("activation_status") or "").strip().lower()
            for row in lineages
            if str(row.get("activation_status") or "").strip()
        }
        issue_codes = {
            str(code).strip()
            for row in lineages
            for code in (row.get("execution_issue_codes") or [])
            if str(code).strip()
        }
        alias_runner_running = any(bool(row.get("alias_runner_running")) for row in lineages)
        evidence_progress = any(
            int(row.get("live_paper_trade_count", 0) or 0) > 0
            or abs(float(row.get("live_paper_realized_pnl", 0.0) or 0.0)) > 0.0
            or bool(dict(row.get("execution_validation") or {}).get("has_execution_signal"))
            for row in lineages
        )
        runtime_age_hours = max(
            (
                float(dict(row.get("execution_validation") or {}).get("runtime_age_hours", 0.0) or 0.0)
                for row in lineages
            ),
            default=0.0,
        )
        if "start_failed" in activation_statuses:
            return 3
        if issue_codes.intersection({"trade_stalled", "training_stalled", "stalled_model"}):
            return 2
        if "started" in activation_statuses and not alias_runner_running and not evidence_progress:
            return 2
        if "ready_to_launch" in activation_statuses and runtime_age_hours >= stale_hours and not evidence_progress:
            return 2
        if (
            activation_statuses.intersection({"started", "running", "ready_to_launch"})
            and runtime_age_hours >= stale_hours
            and not evidence_progress
        ):
            return 1
        return 0

    def _activation_status_from_runtime(
        self,
        current: Dict[str, Any],
        *,
        has_isolated_lane: bool,
        prepared_isolated_lane: bool,
    ) -> str:
        running = bool(current.get("running"))
        runtime_status = str(current.get("runtime_status") or "").strip().lower()
        publish_status = str(current.get("publish_status") or "").strip().lower()
        health_status = str(current.get("health_status") or "").strip().lower()
        issue_codes = {
            str(code).strip().lower()
            for code in (current.get("issue_codes") or [])
            if str(code).strip()
        }
        lane_pending = has_isolated_lane or prepared_isolated_lane
        if running and publish_status == "publishing":
            return "running"
        if running and lane_pending:
            return "started"
        if lane_pending and (
            publish_status in {"failed", "error"}
            or runtime_status in {"failed", "crashed", "error"}
            or issue_codes.intersection({"startup_failed", "runner_crashed", "first_publish_failed"})
            or (health_status == "critical" and runtime_status in {"stopped", "idle"})
        ):
            return "start_failed"
        if has_isolated_lane:
            return "ready_to_launch"
        if prepared_isolated_lane:
            return "pending_stage"
        return "running" if running else ""

    def _target_priority(self, item: Dict[str, Any]) -> tuple[Any, ...]:
        lineages = list(item.get("lineages") or [])
        lane_kind = self._target_lane_kind(item)
        iteration_statuses = {
            str(row.get("iteration_status") or "").strip()
            for row in lineages
            if str(row.get("iteration_status") or "").strip()
        }
        stage_priority = {
            "approved_live": 0,
            "live_ready": 1,
            "canary_ready": 2,
            "paper": 3,
            "shadow": 4,
            "stress": 5,
            "walkforward": 6,
        }
        best_stage = min(stage_priority.get(str(row.get("current_stage") or ""), 999) for row in lineages) if lineages else 999
        best_fitness = max(float(row.get("fitness_score", 0.0) or 0.0) for row in lineages) if lineages else 0.0
        lane_bias = 0 if lane_kind == "isolated_challenger" else (1 if lane_kind == "primary_incumbent" else 2)
        qualification_bias = 0
        if lane_kind == "isolated_challenger" and "isolated_lane_first_assessment_passed" in iteration_statuses:
            qualification_bias = 1
        liveness_penalty = self._isolated_lane_liveness_penalty(item)
        return (
            lane_bias,
            qualification_bias,
            liveness_penalty,
            best_stage,
            -best_fitness,
            str(item.get("portfolio_id") or ""),
        )

    def _apply_runtime_caps(self, rows: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        global_cap = max(1, int(getattr(config, "FACTORY_RUNTIME_MAX_ACTIVE_PAPER_LANES", 6) or 6))
        per_family_cap = max(1, int(getattr(config, "FACTORY_RUNTIME_MAX_ACTIVE_PAPER_LANES_PER_FAMILY", 2) or 2))
        accepted: List[Dict[str, Any]] = []
        suppressed: List[Dict[str, Any]] = []
        global_count = 0
        family_counts: Dict[str, int] = defaultdict(int)
        for item in sorted(rows, key=self._target_priority):
            families = [str(family_id) for family_id in (item.get("families") or []) if str(family_id).strip()]
            if global_count >= global_cap:
                item["suppressed_by_runtime_cap"] = True
                item["suppression_reason"] = "global_active_paper_lane_cap"
                suppressed.append(item)
                continue
            capped_family = next((family_id for family_id in families if family_counts[family_id] >= per_family_cap), None)
            if capped_family is not None:
                item["suppressed_by_runtime_cap"] = True
                item["suppression_reason"] = f"family_active_paper_lane_cap:{capped_family}"
                suppressed.append(item)
                continue
            item["suppressed_by_runtime_cap"] = False
            item["suppression_reason"] = ""
            accepted.append(item)
            global_count += 1
            for family_id in families:
                family_counts[family_id] += 1
        return accepted, suppressed

    def _desired_targets(self, state: Dict[str, Any]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        requested_targets_by_portfolio: Dict[str, set[str]] = {}
        active_lineages_by_portfolio: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        prepared_lineages_by_portfolio: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for lineage in state.get("lineages") or []:
            if not lineage.get("active", True):
                continue
            current_stage = str(lineage.get("current_stage") or "")
            for portfolio_id in lineage.get("target_portfolios") or []:
                requested_portfolio_id = str(portfolio_id)
                resolved_portfolio_id = resolve_target_portfolio(requested_portfolio_id)
                if str(lineage.get("iteration_status") or "").strip() == "prepare_isolated_lane":
                    prepared_lineages_by_portfolio[resolved_portfolio_id].append(dict(lineage))
                if current_stage in {"idea", "spec", "data_check", "goldfish_run", "walkforward", "stress"}:
                    continue
                requested_targets_by_portfolio.setdefault(resolved_portfolio_id, set()).add(requested_portfolio_id)
                active_lineages_by_portfolio[resolved_portfolio_id].append(dict(lineage))
        for resolved_portfolio_id in prepared_lineages_by_portfolio:
            requested_targets_by_portfolio.setdefault(resolved_portfolio_id, set()).add(resolved_portfolio_id)
        rows: List[Dict[str, Any]] = []
        for resolved_portfolio_id, requested_targets in requested_targets_by_portfolio.items():
            candidate_refs = candidate_context_refs_for_portfolio(resolved_portfolio_id)
            prepared_lineages = [
                {
                    "lineage_id": str(item.get("lineage_id") or ""),
                    "family_id": str(item.get("family_id") or ""),
                    "role": str(item.get("role") or ""),
                    "current_stage": str(item.get("current_stage") or ""),
                    "iteration_status": str(item.get("iteration_status") or ""),
                }
                for item in prepared_lineages_by_portfolio.get(resolved_portfolio_id) or []
                if str(item.get("lineage_id") or "").strip()
            ]
            runtime_target_portfolio = str(resolved_portfolio_id)
            canonical_target_portfolio = str(resolved_portfolio_id)
            if len(candidate_refs) == 1:
                selected_runtime_target = str(candidate_refs[0].get("runtime_target_portfolio") or "").strip()
                if selected_runtime_target:
                    runtime_target_portfolio = selected_runtime_target
                selected_canonical_target = str(candidate_refs[0].get("canonical_target_portfolio") or "").strip()
                if selected_canonical_target:
                    canonical_target_portfolio = selected_canonical_target
            selected_lineages = [
                {
                    "lineage_id": str(item.get("lineage_id") or ""),
                    "family_id": str(item.get("family_id") or ""),
                    "role": str(item.get("role") or ""),
                    "current_stage": str(item.get("current_stage") or ""),
                    "iteration_status": str(item.get("iteration_status") or ""),
                    "activation_status": str(item.get("activation_status") or ""),
                    "alias_runner_running": bool(item.get("alias_runner_running")),
                    "fitness_score": item.get("fitness_score"),
                    "strict_gate_pass": bool(item.get("strict_gate_pass")),
                    "runtime_lane_kind": str(item.get("runtime_lane_kind") or ""),
                    "runtime_lane_reason": str(item.get("runtime_lane_reason") or ""),
                    "runtime_target_portfolio": str(item.get("runtime_target_portfolio") or ""),
                    "canonical_target_portfolio": str(item.get("canonical_target_portfolio") or ""),
                    "live_paper_target_portfolio_id": str(item.get("live_paper_target_portfolio_id") or ""),
                    "live_paper_trade_count": item.get("live_paper_trade_count"),
                    "live_paper_realized_pnl": item.get("live_paper_realized_pnl"),
                    "execution_issue_codes": list(item.get("execution_issue_codes") or []),
                    "execution_validation": dict(item.get("execution_validation") or {}),
                    "suppressed_sibling_count": int(item.get("suppressed_sibling_count", 0) or 0),
                }
                for item in candidate_refs
            ]
            families = {str(item.get("family_id") or "unknown") for item in candidate_refs}
            if not selected_lineages:
                fallback_rows = active_lineages_by_portfolio.get(resolved_portfolio_id) or []
                selected_lineages = [
                    {
                        "lineage_id": str(item.get("lineage_id") or ""),
                        "family_id": str(item.get("family_id") or "unknown"),
                        "role": str(item.get("role") or ""),
                        "current_stage": str(item.get("current_stage") or ""),
                        "iteration_status": str(item.get("iteration_status") or ""),
                        "activation_status": str(item.get("activation_status") or ""),
                        "alias_runner_running": bool(item.get("alias_runner_running")),
                        "fitness_score": item.get("fitness_score"),
                        "strict_gate_pass": bool(item.get("strict_gate_pass")),
                        "runtime_lane_kind": "",
                        "runtime_lane_reason": "",
                        "live_paper_target_portfolio_id": str(item.get("live_paper_target_portfolio_id") or ""),
                        "live_paper_trade_count": item.get("live_paper_trade_count"),
                        "live_paper_realized_pnl": item.get("live_paper_realized_pnl"),
                        "execution_issue_codes": list(item.get("execution_issue_codes") or []),
                        "execution_validation": dict(item.get("execution_validation") or {}),
                        "suppressed_sibling_count": 0,
                    }
                    for item in fallback_rows
                ]
                families = {str(item.get("family_id") or "unknown") for item in fallback_rows}
            rows.append(
                {
                    "portfolio_id": runtime_target_portfolio,
                    "canonical_portfolio_id": canonical_target_portfolio,
                    "requested_targets": sorted(requested_targets),
                    "families": sorted(families),
                    "stages": sorted({str(item.get("current_stage") or "") for item in selected_lineages if str(item.get("current_stage") or "")}),
                    "lineages": selected_lineages,
                    "prepared_lineages": prepared_lineages,
                    "prepared_isolated_lane": bool(prepared_lineages),
                    "lane_selected": bool(candidate_refs),
                }
            )
        rows, suppressed = self._apply_runtime_caps(rows)
        return (
            sorted(rows, key=lambda item: item["portfolio_id"]),
            sorted(suppressed, key=lambda item: item["portfolio_id"]),
        )

    def sync(self, state: Dict[str, Any]) -> Dict[str, Any]:
        runtime_mode = current_agentic_factory_runtime_mode()
        desired, suppressed_targets = self._desired_targets(state)
        statuses: List[Dict[str, Any]] = []
        family_counts = defaultdict(int)
        for item in desired:
            for family_id in item["families"]:
                family_counts[family_id] += 1
            portfolio_id = str(item["portfolio_id"])
            status: Dict[str, Any] = {
                "portfolio_id": portfolio_id,
                "canonical_portfolio_id": str(item.get("canonical_portfolio_id") or portfolio_id),
                "families": list(item["families"]),
                "requested_targets": list(item["requested_targets"]),
                "stages": list(item["stages"]),
                "active_lineage_count": len(item["lineages"]),
                "lineage_ids": [row["lineage_id"] for row in item["lineages"]],
                "lineages": list(item["lineages"]),
                "prepared_lineage_ids": [row["lineage_id"] for row in item.get("prepared_lineages") or []],
                "prepared_isolated_lane": bool(item.get("prepared_isolated_lane")),
                "lineage_roles": sorted({row["role"] for row in item["lineages"] if row.get("role")}),
                "runtime_mode": runtime_mode.value,
                "auto_start_enabled": self._auto_start,
                "desired": True,
            }
            has_isolated_lane = any(
                str(row.get("runtime_lane_kind") or "").strip() == "isolated_challenger"
                for row in item["lineages"]
            )
            if has_isolated_lane:
                status["runtime_lane_kind"] = "isolated_challenger"
                status["runtime_lane_reason"] = next(
                    (
                        str(row.get("runtime_lane_reason") or "").strip()
                        for row in item["lineages"]
                        if str(row.get("runtime_lane_reason") or "").strip()
                    ),
                    "",
                )
            elif item["lineages"]:
                status["runtime_lane_kind"] = str(item["lineages"][0].get("runtime_lane_kind") or "").strip()
                status["runtime_lane_reason"] = str(item["lineages"][0].get("runtime_lane_reason") or "").strip()
            else:
                status["runtime_lane_kind"] = ""
                status["runtime_lane_reason"] = ""
            try:
                spec = get_runtime_portfolio_spec(portfolio_id)
            except KeyError:
                status.update(
                    {
                        "runner_known": False,
                        "runner_enabled": False,
                        "control_mode": "unknown",
                        "running": False,
                        "status": "monitor_only_target",
                        "activation_status": "start_failed" if has_isolated_lane else "pending_stage",
                        "note": "Target portfolio is a synthetic monitor view or not registered as a managed runner.",
                    }
                )
                statuses.append(status)
                continue

            status.update(
                {
                    "runner_known": True,
                    "runner_enabled": bool(spec.enabled),
                    "control_mode": spec.control_mode,
                    "label": spec.label,
                    "target_aliases_resolved": list(item["requested_targets"]) != [portfolio_id],
                }
            )
            if spec.control_mode == "disabled":
                status.update(
                    {
                        "running": False,
                        "status": "monitor_only_target",
                        "activation_status": "pending_stage",
                        "note": "Target portfolio is a disabled synthetic monitor view, not a runner process.",
                    }
                )
                statuses.append(status)
                continue
            if not spec.enabled:
                status.update(
                    {
                        "running": False,
                        "status": "runner_disabled",
                        "activation_status": "pending_stage" if status.get("prepared_isolated_lane") else "",
                        "note": "Portfolio exists but is disabled in config, so the factory will not auto-start it.",
                    }
                )
                statuses.append(status)
                continue
            current = self._process_manager.status(portfolio_id)
            activation_status = self._activation_status_from_runtime(
                current,
                has_isolated_lane=has_isolated_lane,
                prepared_isolated_lane=bool(status.get("prepared_isolated_lane")),
            )
            status.update(
                {
                    "running": bool(current.get("running")),
                    "pid": current.get("pid"),
                    "heartbeat": current.get("heartbeat"),
                    "runtime_status": str(current.get("runtime_status") or ""),
                    "publish_status": str(current.get("publish_status") or ""),
                    "health_status": str(current.get("health_status") or ""),
                    "issue_codes": list(current.get("issue_codes") or []),
                    "activation_status": activation_status,
                }
            )
            if not runtime_mode.factory_influence_allowed:
                status.update(
                    {
                        "status": "factory_influence_paused",
                        "activation_status": status.get("activation_status") or ("pending_stage" if status.get("prepared_isolated_lane") else ""),
                        "note": "Runtime mode paused factory influence, so execution auto-start is suppressed.",
                    }
                )
                statuses.append(status)
                continue
            if status["running"]:
                status.update(
                    {
                        "status": "running",
                        "activation_status": activation_status or "running",
                        "note": "Execution runner is already live for this factory target.",
                    }
                )
                statuses.append(status)
                continue
            if not self._auto_start:
                status.update(
                    {
                        "status": "autostart_disabled",
                        "activation_status": "ready_to_launch" if has_isolated_lane else status.get("activation_status"),
                        "note": "Execution auto-start is disabled, so the runner must be started manually.",
                    }
                )
                statuses.append(status)
                continue
            started = self._process_manager.start(portfolio_id)
            current = self._process_manager.status(portfolio_id)
            status.update(
                {
                    "running": bool(current.get("running")),
                    "pid": current.get("pid"),
                    "heartbeat": current.get("heartbeat"),
                    "start_result": dict(started),
                }
            )
            if started.get("ok") and current.get("running"):
                status.update(
                    {
                        "status": "started",
                        "activation_status": "started",
                        "note": "Factory auto-started the execution runner for paper validation.",
                    }
                )
            elif started.get("error") == "already_running":
                status.update(
                    {
                        "status": "running",
                        "activation_status": "running",
                        "note": "Execution runner was already running.",
                    }
                )
            else:
                status.update(
                    {
                        "status": "start_failed",
                        "activation_status": "start_failed" if has_isolated_lane else status.get("activation_status"),
                        "note": str(started.get("error") or "Runner did not start successfully."),
                    }
                )
            statuses.append(status)
        for item in suppressed_targets:
            portfolio_id = str(item["portfolio_id"])
            status: Dict[str, Any] = {
                "portfolio_id": portfolio_id,
                "canonical_portfolio_id": str(item.get("canonical_portfolio_id") or portfolio_id),
                "families": list(item["families"]),
                "requested_targets": list(item["requested_targets"]),
                "stages": list(item["stages"]),
                "active_lineage_count": len(item["lineages"]),
                "lineage_ids": [row["lineage_id"] for row in item["lineages"]],
                "lineages": list(item["lineages"]),
                "prepared_lineage_ids": [row["lineage_id"] for row in item.get("prepared_lineages") or []],
                "prepared_isolated_lane": bool(item.get("prepared_isolated_lane")),
                "lineage_roles": sorted({row["role"] for row in item["lineages"] if row.get("role")}),
                "runtime_mode": runtime_mode.value,
                "auto_start_enabled": self._auto_start,
                "desired": False,
                "suppression_reason": str(item.get("suppression_reason") or ""),
                "suppressed_by_runtime_cap": True,
                "status": "runtime_cap_suppressed",
                "note": "Target was suppressed by runtime caps.",
                "activation_status": "suppressed",
                "running": False,
                "pid": None,
                "heartbeat": None,
                "runtime_status": "",
                "publish_status": "",
                "health_status": "",
                "issue_codes": [],
            }
            try:
                spec = get_runtime_portfolio_spec(portfolio_id)
                status.update(
                    {
                        "runner_known": True,
                        "runner_enabled": bool(spec.enabled),
                        "control_mode": spec.control_mode,
                        "label": spec.label,
                        "target_aliases_resolved": list(item["requested_targets"]) != [portfolio_id],
                    }
                )
            except KeyError:
                status.update(
                    {
                        "runner_known": False,
                        "runner_enabled": False,
                        "control_mode": "unknown",
                        "target_aliases_resolved": list(item["requested_targets"]) != [portfolio_id],
                    }
                )
        return {
            "auto_start_enabled": self._auto_start,
            "runtime_mode": runtime_mode.value,
            "desired_portfolio_count": len(desired),
            "running_portfolio_count": sum(1 for item in statuses if item.get("running")),
            "family_target_counts": dict(sorted(family_counts.items())),
            "suppressed_portfolio_count": len(suppressed_targets),
            "suppressed_targets": [
                {
                    "portfolio_id": str(item["portfolio_id"]),
                    "canonical_portfolio_id": str(item.get("canonical_portfolio_id") or item["portfolio_id"]),
                    "families": list(item["families"]),
                    "requested_targets": list(item["requested_targets"]),
                    "suppression_reason": str(item.get("suppression_reason") or ""),
                }
                for item in suppressed_targets
            ],
            "targets": statuses,
        }
