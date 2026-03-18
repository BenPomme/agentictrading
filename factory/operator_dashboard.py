from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

import config
from factory.agent_runtime import recent_agent_runs
from factory.assessment import assessment_progress, compact_number
from factory.contracts import PromotionStage, utc_now_iso
from factory.execution_evidence import build_portfolio_execution_evidence
from factory.execution_targets import resolve_target_portfolio
from factory.idea_intake import all_ideas, annotate_idea_statuses, split_active_and_archived_ideas
from factory.registry import FactoryRegistry
from factory.connectors import default_connector_catalog


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_backtest_roi(project_root: Path, family_id: str, lineage_id: str) -> dict:
    """Load backtest ROI metrics for a lineage."""
    result = {"backtest_roi_pct": None, "backtest_sharpe": None, "backtest_gate_status": None}

    backtest_dir = project_root / "data" / "backtest_results" / family_id
    if not backtest_dir.exists():
        return result

    # Check for optimization results first
    opt_path = backtest_dir / "optimized_params.json"
    if opt_path.exists():
        try:
            data = json.loads(opt_path.read_text(encoding="utf-8"))
            metrics = data.get("best_metrics") or {}
            result["backtest_roi_pct"] = float(metrics.get("total_return_pct", 0) or 0)
            result["backtest_sharpe"] = float(metrics.get("sharpe", 0) or 0)
            result["backtest_gate_status"] = "positive" if result["backtest_roi_pct"] > 0 else "negative"
            return result
        except Exception:
            pass

    best_roi = None
    best_sharpe = None
    for rf in sorted(backtest_dir.glob("*.json")):
        try:
            data = json.loads(rf.read_text(encoding="utf-8"))
            metrics = (
                data.get("best_metrics")
                or data.get("test_metrics")
                or (data.get("optimization") or {}).get("best_metrics")
                or {}
            )
            roi = float(metrics.get("total_return_pct", metrics.get("total_return", 0)) or 0)
            sharpe = float(metrics.get("sharpe", metrics.get("sharpe_ratio", 0)) or 0)
            if best_roi is None or roi > best_roi:
                best_roi = roi
                best_sharpe = sharpe
        except Exception:
            continue

    if best_roi is not None:
        result["backtest_roi_pct"] = best_roi
        result["backtest_sharpe"] = best_sharpe
        result["backtest_gate_status"] = "positive" if best_roi > 0 else "negative"

    return result


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _read_jsonl(path: Path, limit: int = 20) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    if limit <= 0:
        return rows
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            end = handle.tell()
            block = 4096
            buffer = b""
            pos = end
            while pos > 0 and buffer.count(b"\n") <= limit * 2:
                read_size = min(block, pos)
                pos -= read_size
                handle.seek(pos)
                buffer = handle.read(read_size) + buffer
    except Exception:
        return rows
    for line in buffer.decode("utf-8", errors="ignore").splitlines()[-limit * 2 :]:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows[-limit:]


def _compact_number(value: Any) -> float:
    return compact_number(value)


def _parse_iso_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _lineage_paper_runtime_status(row: Dict[str, Any]) -> str:
    if not bool(row.get("active", True)):
        return "retired"
    if bool(row.get("suppressed_runtime_sibling")):
        return "suppressed"
    lane_kind = str(row.get("runtime_lane_kind") or "").strip()
    activation_status = str(row.get("activation_status") or "").strip().lower()
    has_runtime_target = bool(
        str(
            row.get("runtime_target_portfolio")
            or row.get("live_paper_target_portfolio_id")
            or ""
        ).strip()
    )
    if activation_status == "start_failed":
        return "paper_start_failed"
    if lane_kind in {"primary_incumbent", "isolated_challenger"}:
        if (
            has_runtime_target
            and (
                bool(row.get("alias_runner_running"))
                or activation_status == "running"
                or bool(row.get("execution_has_signal"))
            )
        ):
            return "paper_running"
        if activation_status in {"ready_to_launch", "started", "launching"}:
            return "paper_starting"
        if has_runtime_target or bool(row.get("runtime_lane_selected")):
            return "paper_assigned"
        return "paper_candidate"
    if str(row.get("current_stage") or "").strip() in {
        PromotionStage.PAPER.value,
        PromotionStage.SHADOW.value,
        PromotionStage.CANARY_READY.value,
        PromotionStage.LIVE_READY.value,
        PromotionStage.APPROVED_LIVE.value,
    }:
        return "paper_candidate"
    return "research_only"


def _lineage_expected_to_trade(row: Dict[str, Any]) -> bool:
    return _lineage_paper_runtime_status(row) in {
        "paper_running",
        "paper_starting",
        "paper_assigned",
        "paper_candidate",
        "paper_start_failed",
    }


def _paper_runtime_summary(factory_state: Dict[str, Any]) -> Dict[str, Any]:
    lineages = list(factory_state.get("lineages") or [])
    statuses = [_lineage_paper_runtime_status(dict(row)) for row in lineages]
    summary = {
        "expected_count": sum(
            1
            for status in statuses
            if status in {"paper_running", "paper_starting", "paper_assigned", "paper_candidate", "paper_start_failed"}
        ),
        "running_count": sum(1 for status in statuses if status == "paper_running"),
        "starting_count": sum(1 for status in statuses if status == "paper_starting"),
        "assigned_count": sum(1 for status in statuses if status == "paper_assigned"),
        "candidate_count": sum(1 for status in statuses if status == "paper_candidate"),
        "failed_count": sum(1 for status in statuses if status == "paper_start_failed"),
        "suppressed_count": sum(1 for status in statuses if status == "suppressed"),
        "research_only_count": sum(1 for status in statuses if status == "research_only"),
        "retired_count": sum(1 for status in statuses if status == "retired"),
    }
    return summary


def _clean_operator_text(value: Any, *, max_len: int = 220) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[: max_len - 1] + "…" if len(text) > max_len else text


def _compact_agent_run_error(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "OpenAI Codex v" in text or "mcp startup:" in text:
        if "invalid_json_schema" in text:
            match = re.search(r'"message":\s*"([^"]+)"', text)
            if match:
                return _clean_operator_text(f"structured_output_error: {match.group(1)}", max_len=240)
            return "structured_output_error"
        env_match = re.search(r"Environment variable ([A-Z0-9_]+) .* is not set", text)
        if env_match:
            return f"missing_env: {env_match.group(1)}"
        auth_match = re.findall(r"The ([a-zA-Z0-9_-]+) MCP server is not logged in", text)
        if auth_match:
            return _clean_operator_text(f"mcp_auth_missing: {', '.join(sorted(set(auth_match)))}", max_len=240)
        failed = re.findall(r"mcp:\s*([a-zA-Z0-9_-]+)\s+failed", text)
        if failed:
            return _clean_operator_text(f"mcp_startup_failed: {', '.join(sorted(set(failed)))}", max_len=240)
        return "codex_exec_failed"
    return _clean_operator_text(text, max_len=240)


def _read_markdown_sections(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"content": "", "recent_actions": []}
    content = path.read_text(encoding="utf-8")
    recent_actions: List[str] = []
    section = ""
    for line in content.splitlines():
        if line.startswith("## "):
            section = line.strip()
            continue
        if section == "## Recent Actions" and line.startswith("- "):
            recent_actions.append(line[2:])
    return {"content": content, "recent_actions": recent_actions}


def _parse_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        normalized = text.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except Exception:
        return None


def _age_seconds(value: Any) -> float | None:
    ts = _parse_ts(value)
    if ts is None:
        return None
    now = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return round(max(0.0, (now - ts).total_seconds()), 1)


def _build_connector_feed_health(connectors: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(connectors or [])
    if not rows:
        return {
            "status": "info",
            "headline": "No data feeds configured",
            "summary": "Connector health will appear here once feeds are wired into factory state.",
            "total_count": 0,
            "healthy_count": 0,
            "warning_count": 0,
            "critical_count": 0,
            "latest_data_ts": None,
            "latest_age_seconds": None,
            "connectors": [],
        }

    # Connector-level view is about **connectivity**, not model/runtime freshness.
    # Treat a connector as healthy when it has any resolved source paths and records,
    # and reserve warning/critical for truly missing data.
    healthy_count = 0
    warning_count = 0
    critical_count = 0
    latest_data_ts: str | None = None
    latest_age_seconds: float | None = None
    connector_rows: List[Dict[str, Any]] = []

    for connector in rows:
        ready = bool(connector.get("ready"))
        latest_ts = connector.get("latest_data_ts")
        latest_age = _age_seconds(latest_ts)
        record_count = int(connector.get("record_count") or 0)

        # New semantics:
        # - not ready  -> critical (no data resolved at all)
        # - ready but record_count == 0 -> warning (configured but empty)
        # - ready and record_count > 0 -> healthy regardless of exact age
        if not ready:
            status = "critical"
            critical_count += 1
        elif record_count == 0:
            status = "warning"
            warning_count += 1
        else:
            status = "healthy"
            healthy_count += 1

        if latest_age is not None and (latest_age_seconds is None or latest_age < latest_age_seconds):
            latest_data_ts = str(latest_ts)
            latest_age_seconds = latest_age

        connector_rows.append(
            {
                "connector_id": str(connector.get("connector_id") or ""),
                "venue": str(connector.get("venue") or ""),
                "status": status,
                "ready": ready,
                "latest_data_ts": latest_ts,
                "latest_age_seconds": latest_age,
                "record_count": record_count,
                "issue_count": len(list(connector.get("issues") or [])),
            }
        )

    total_count = len(rows)
    if healthy_count == total_count:
        status = "healthy"
    elif critical_count:
        status = "critical"
    else:
        status = "degraded"

    summary_parts = [f"{healthy_count}/{total_count} healthy"]
    if warning_count:
        summary_parts.append(f"{warning_count} stale")
    if critical_count:
        summary_parts.append(f"{critical_count} down")
    if latest_age_seconds is not None:
        summary_parts.append(f"latest {int(latest_age_seconds)}s ago")

    return {
        "status": status,
        "headline": f"{healthy_count}/{total_count} feeds healthy",
        "summary": " · ".join(summary_parts),
        "total_count": total_count,
        "healthy_count": healthy_count,
        "warning_count": warning_count,
        "critical_count": critical_count,
        "latest_data_ts": latest_data_ts,
        "latest_age_seconds": latest_age_seconds,
        "connectors": connector_rows,
    }


_VENUE_FEED_PORTFOLIOS: Dict[str, List[str]] = {
    "binance": ["hedge_validation", "hedge_research", "contrarian_legacy", "cascade_alpha"],
    "betfair": ["betfair_core"],
    "polymarket": ["polymarket_quantum_fold"],
}


def _feed_candidate_portfolios(factory_state: Dict[str, Any], connectors: Iterable[Dict[str, Any]]) -> Dict[str, List[str]]:
    venue_map: Dict[str, List[str]] = {}
    for connector in connectors or []:
        venue = str(connector.get("venue") or "").strip().lower()
        if venue:
            venue_map.setdefault(venue, []).extend(_VENUE_FEED_PORTFOLIOS.get(venue, []))
    for lineage in list(factory_state.get("lineages") or []):
        venues = [str(item).strip().lower() for item in (lineage.get("target_venues") or []) if str(item).strip()]
        portfolio_candidates = [
            resolve_target_portfolio(str(lineage.get("runtime_target_portfolio") or "").strip()),
            resolve_target_portfolio(str(lineage.get("live_paper_target_portfolio_id") or "").strip()),
            resolve_target_portfolio(str(lineage.get("curated_target_portfolio_id") or "").strip()),
        ]
        portfolio_candidates.extend(
            resolve_target_portfolio(str(item).strip())
            for item in (lineage.get("target_portfolios") or [])
            if str(item).strip()
        )
        for venue in venues:
            venue_map.setdefault(venue, []).extend(_VENUE_FEED_PORTFOLIOS.get(venue, []))
            venue_map[venue].extend([item for item in portfolio_candidates if item])
    if not venue_map:
        venue_map = {venue: list(portfolios) for venue, portfolios in _VENUE_FEED_PORTFOLIOS.items()}
    return {
        venue: list(dict.fromkeys([item for item in portfolio_ids if item]))
        for venue, portfolio_ids in venue_map.items()
    }


def _portfolio_feed_signal(portfolio_id: str) -> Dict[str, Any] | None:
    root = _execution_root()
    if not root.exists():
        return None
    evidence = build_portfolio_execution_evidence(portfolio_id, root=str(root))
    if not evidence.get("evidence_store_exists"):
        return None

    latest_ts = (
        evidence.get("heartbeat_ts")
        or evidence.get("last_trade_activity_at")
        or evidence.get("runtime_started_at")
        or evidence.get("training_state", {}).get("last_training_activity_at")
    )
    latest_age = _age_seconds(latest_ts)
    issue_codes = list(evidence.get("issue_codes") or [])
    health_status = str(evidence.get("health_status") or "critical").strip().lower()
    evidence_store_exists = bool(evidence.get("evidence_store_exists"))

    status = "healthy"
    if any(code in {"runtime_error", "heartbeat_stale"} for code in issue_codes):
        status = "critical"
    elif issue_codes:
        status = "warning"
    elif health_status == "critical":
        status = "critical"
    elif health_status == "warning":
        status = "warning"
    elif latest_age is None or latest_age >= 300:
        status = "critical"
    elif latest_age >= 120:
        status = "warning"

    return {
        "portfolio_id": portfolio_id,
        "evidence_store_exists": evidence_store_exists,
        "status": status,
        "latest_data_ts": latest_ts,
        "latest_age_seconds": latest_age,
        "running": bool(evidence.get("running")),
        "ready": evidence_store_exists and status != "critical",
        "issue_count": len(issue_codes),
        "runtime_target": str(evidence.get("runtime_target") or evidence.get("store_target") or portfolio_id),
        "evidence_source": str(evidence.get("evidence_source") or "requested_portfolio"),
        "is_alias_store": portfolio_id != str(evidence.get("store_target") or portfolio_id),
    }


def _build_feed_health(factory_state: Dict[str, Any], connectors: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    connector_rows = list(connectors or [])
    venue_candidates = _feed_candidate_portfolios(factory_state, connector_rows)
    venue_rows: List[Dict[str, Any]] = []

    for venue, portfolio_ids in venue_candidates.items():
        signals = [item for item in (_portfolio_feed_signal(portfolio_id) for portfolio_id in portfolio_ids) if item]
        if not signals:
            venue_rows.append(
                {
                    "connector_id": f"{venue}_core",
                    "venue": venue,
                    "status": "critical",
                    "ready": False,
                    "latest_data_ts": None,
                    "latest_age_seconds": None,
                    "record_count": 0,
                    "issue_count": 1,
                }
            )
            continue
        has_critical = any(str(signal.get("status") or "healthy") == "critical" for signal in signals)
        has_warning = any(str(signal.get("status") or "healthy") == "warning" for signal in signals)
        venue_status = "critical" if has_critical else "warning" if has_warning else "healthy"
        latest_row = min(
            (signal for signal in signals if signal.get("latest_age_seconds") is not None),
            key=lambda row: float(row.get("latest_age_seconds") or 0.0),
            default=None,
        )
        best_for_readiness = max(signals, key=lambda signal: int(bool(signal.get("running"))))
        venue_rows.append(
            {
                "connector_id": f"{venue}_core",
                "venue": venue,
                "status": venue_status,
                "ready": bool(any(signal.get("ready") for signal in signals)),
                "latest_data_ts": (latest_row or signals[0]).get("latest_data_ts"),
                "latest_age_seconds": (latest_row or signals[0]).get("latest_age_seconds"),
                "record_count": sum(1 for item in signals if item.get("latest_data_ts")),
                "issue_count": max(int(item.get("issue_count") or 0) for item in signals),
                "portfolio_id": str(best_for_readiness.get("portfolio_id") or ""),
            }
        )

    if venue_rows:
        healthy_count = sum(1 for row in venue_rows if row.get("status") == "healthy")
        warning_count = sum(1 for row in venue_rows if row.get("status") == "warning")
        critical_count = sum(1 for row in venue_rows if row.get("status") == "critical")
        latest_row = min(
            (row for row in venue_rows if row.get("latest_age_seconds") is not None),
            key=lambda row: float(row.get("latest_age_seconds") or 0.0),
            default=None,
        )
        latest_data_ts = latest_row.get("latest_data_ts") if latest_row else None
        latest_age_seconds = latest_row.get("latest_age_seconds") if latest_row else None
        overall_status = (
            "critical"
            if critical_count > 0
            else ("degraded" if warning_count > 0 else ("healthy" if healthy_count else "critical"))
        )
        summary_parts = [f"{healthy_count}/{len(venue_rows)} healthy"]
        if warning_count:
            summary_parts.append(f"{warning_count} slow")
        if critical_count:
            summary_parts.append(f"{critical_count} down")
        if latest_age_seconds is not None:
            summary_parts.append(f"latest {int(latest_age_seconds)}s ago")
        return {
            "status": overall_status,
            "headline": f"{healthy_count}/{len(venue_rows)} feeds healthy",
            "summary": " · ".join(summary_parts),
            "total_count": len(venue_rows),
            "healthy_count": healthy_count,
            "warning_count": warning_count,
            "critical_count": critical_count,
            "latest_data_ts": latest_data_ts,
            "latest_age_seconds": latest_age_seconds,
            "connectors": venue_rows,
        }

    return _build_connector_feed_health(connector_rows)


def _assessment_progress(
    *,
    paper_days: int,
    trade_count: int,
    labels: Iterable[str],
    realized_roi_pct: float | None = None,
    current_stage: str | None = None,
) -> Dict[str, Any]:
    return assessment_progress(
        paper_days=paper_days,
        trade_count=trade_count,
        labels=labels,
        realized_roi_pct=realized_roi_pct,
        current_stage=current_stage,
        phase="full",
    )


def _first_assessment_progress(
    *,
    paper_days: int,
    trade_count: int,
    labels: Iterable[str],
    realized_roi_pct: float | None = None,
    current_stage: str | None = None,
) -> Dict[str, Any]:
    return assessment_progress(
        paper_days=paper_days,
        trade_count=trade_count,
        labels=labels,
        realized_roi_pct=realized_roi_pct,
        current_stage=current_stage,
        phase="first",
    )


def _execution_root() -> Path:
    explicit = str(getattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", "") or "").strip()
    if explicit:
        p = Path(explicit)
        if not p.is_absolute():
            p = _project_root() / p
        return p
    return _project_root() / "data" / "portfolios"


def _factory_root() -> Path:
    factory_root = Path(getattr(config, "FACTORY_ROOT", "data/factory"))
    if not factory_root.is_absolute():
        factory_root = _project_root() / factory_root
    return factory_root


def _factory_state_path() -> Path:
    return _factory_root() / "state" / "summary.json"


def _factory_journal_path() -> Path:
    return _factory_root() / "state" / "STATE.md"


def _ideas_path() -> Path:
    root = _project_root()
    for name in ("ideas.md", "IDEAS.md"):
        path = root / name
        if path.exists():
            return path
    return root / "ideas.md"


def _idea_status_counts(idea_items: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts = {status: 0 for status in ["new", "adapted", "incubated", "tested", "promoted", "rejected"]}
    for item in idea_items:
        status = str(item.get("status") or "").strip()
        if not status:
            continue
        counts[status] = counts.get(status, 0) + 1
    return counts


def _portfolio_dirs() -> List[Path]:
    root = _execution_root()
    if not root.exists():
        return []
    tracked_raw = str(getattr(config, "EXECUTION_TRACKED_PORTFOLIOS", "") or "").strip()
    tracked = [item.strip() for item in tracked_raw.split(",") if item.strip()]
    if tracked:
        dirs = [root / name for name in tracked if (root / name).is_dir()]
        # Also include lineage-scoped paper portfolios (lineage__*) for Paper Models visibility
        for lineage_dir in sorted(root.glob("lineage__*")):
            if lineage_dir.is_dir() and lineage_dir not in dirs:
                dirs.append(lineage_dir)
        return sorted(dirs)
    return sorted(path for path in root.iterdir() if path.is_dir())


def _tracked_portfolio_ids() -> List[str]:
    tracked_raw = str(getattr(config, "EXECUTION_TRACKED_PORTFOLIOS", "") or "").strip()
    tracked = [item.strip() for item in tracked_raw.split(",") if item.strip()]
    if tracked:
        return tracked
    return [path.name for path in _portfolio_dirs()]


def _runtime_alias_ids(factory_state: Dict[str, Any]) -> List[str]:
    return list(
        dict.fromkeys(
            [
                str(lineage.get("runtime_target_portfolio") or "").strip()
                for lineage in list(factory_state.get("lineages") or [])
                if str(lineage.get("runtime_target_portfolio") or "").strip()
            ]
        )
    )


def _running_portfolio_ids() -> set[str]:
    try:
        result = subprocess.run(
            ["ps", "-axo", "command"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except Exception:
        return set()
    running: set[str] = set()
    for line in result.stdout.splitlines():
        is_external = "scripts/run_portfolio.py" in line and "--portfolio" in line
        is_embedded = "factory.local_runner_main" in line and "--portfolio" in line
        if not is_external and not is_embedded:
            continue
        try:
            runtime_portfolio = ""
            if "--runtime-portfolio-id" in line:
                runtime_portfolio = line.split("--runtime-portfolio-id", 1)[1].strip().split()[0].strip()
            portfolio = runtime_portfolio or line.split("--portfolio", 1)[1].strip().split()[0].strip()
        except Exception:
            continue
        if portfolio:
            running.add(portfolio)
    return running


def _has_runtime_state(path: Path) -> bool:
    runtime_files = (
        "account.json",
        "heartbeat.json",
        "runtime_health.json",
        "state.json",
        "config_snapshot.json",
        "readiness.json",
        "trades.jsonl",
        "events.jsonl",
        "runner.log",
    )
    return any((path / name).exists() for name in runtime_files)


def _portfolio_error(state: Dict[str, Any], readiness: Dict[str, Any]) -> str | None:
    error = str(state.get("error") or "").strip()
    if error:
        return error
    return None


def _execution_evidence_for_portfolio(portfolio_id: str, root: Path) -> Dict[str, Any]:
    try:
        return build_portfolio_execution_evidence(portfolio_id, root=str(root))
    except Exception:
        return {
            "requested_target": str(portfolio_id),
            "resolved_target": str(portfolio_id),
            "store_target": str(portfolio_id),
            "canonical_target": str(portfolio_id),
            "runtime_target": str(portfolio_id),
            "evidence_source": "dashboard_fallback",
            "evidence_store_exists": False,
            "running": False,
            "heartbeat_ts": None,
            "heartbeat_age_seconds": None,
            "status": "unknown",
            "health_status": "warning",
            "issue_codes": [],
            "issues": [],
            "account": {
                "currency": "USD",
                "current_balance": 0.0,
                "starting_balance": 0.0,
                "realized_pnl": 0.0,
                "roi_pct": 0.0,
                "drawdown_pct": 0.0,
                "trade_count": 0,
                "wins": 0,
                "losses": 0,
            },
            "training_progress": {},
            "trainability": {},
            "recommendation_context": [],
        }


def _portfolio_snapshot_light(path: Path) -> Dict[str, Any]:
    has_runtime_state = _has_runtime_state(path)
    evidence = _execution_evidence_for_portfolio(path.name, path.parent)
    if not evidence.get("evidence_store_exists"):
        return {
            "portfolio_id": path.name,
            "is_placeholder": not has_runtime_state,
            "running": False,
            "blocked": False,
            "realized_pnl": 0.0,
            "heartbeat_age_seconds": None,
            "readiness_status": None,
            "execution_health_status": "warning",
            "execution_issue_codes": [],
        }

    account = dict(evidence.get("account") or {})
    issue_codes = list(evidence.get("issue_codes") or [])
    readiness_status = str(evidence.get("status") or "").strip().lower()
    health_status = str(evidence.get("health_status") or "").strip().lower()
    running = bool(evidence.get("running"))
    heartbeat_age = evidence.get("heartbeat_age_seconds")
    blocked = health_status in {"critical", "warning"} and any(
        item in {"runtime_error", "heartbeat_stale", "readiness_blocked", "trade_stalled", "training_stalled", "stalled_model"}
        for item in issue_codes
    )
    if not readiness_status:
        readiness_status = "running" if running else (health_status if health_status else "unknown")
    realized_pnl = _compact_number(account.get("realized_pnl"))
    return {
        "portfolio_id": path.name,
        "is_placeholder": not has_runtime_state,
        "running": running,
        "blocked": blocked,
        "realized_pnl": realized_pnl,
        "heartbeat_age_seconds": heartbeat_age,
        "readiness_status": readiness_status or None,
        "execution_health_status": health_status or "warning",
        "execution_issue_codes": issue_codes[:3],
    }


def _execution_summary_light() -> Dict[str, Any]:
    portfolios = [_portfolio_snapshot_light(path) for path in _portfolio_dirs()]
    placeholder_portfolios = [row for row in portfolios if row.get("is_placeholder")]
    tracked_portfolios = [
        row
        for row in portfolios
        if row["portfolio_id"] != "command_center" and not row.get("is_placeholder")
    ]
    return {
        "portfolio_count": len(tracked_portfolios),
        "placeholder_count": len(placeholder_portfolios),
        "running_count": sum(1 for row in tracked_portfolios if row.get("running")),
        "blocked_count": sum(1 for row in tracked_portfolios if row.get("blocked")),
        "realized_pnl_total": round(sum(_compact_number(row.get("realized_pnl")) for row in tracked_portfolios), 4),
        "portfolios": [],
        "placeholders": [],
        "mode": "light_snapshot",
        "note": "Execution portfolio details are summarized in lightweight mode; full portfolio cards remain disabled.",
    }


def _best_execution_performer(portfolios: Iterable[Dict[str, Any]]) -> Dict[str, Any] | None:
    rows = [dict(row) for row in portfolios if isinstance(row, dict)]
    if not rows:
        return None
    ranked = sorted(
        rows,
        key=lambda row: (
            _compact_number(row.get("roi_pct")),
            _compact_number(row.get("realized_pnl")),
            int(row.get("trade_count") or 0),
        ),
        reverse=True,
    )
    best = ranked[0]
    return {
        "portfolio_id": str(best.get("portfolio_id") or ""),
        "label": str(best.get("label") or best.get("portfolio_id") or ""),
        "currency": str(best.get("currency") or "USD"),
        "realized_pnl": _compact_number(best.get("realized_pnl")),
        "roi_pct": _compact_number(best.get("roi_pct")),
        "trade_count": int(best.get("trade_count") or 0),
        "paper_days": int(best.get("paper_days") or 0),
    }


def _extract_trainability(training_state: Dict[str, Any]) -> Dict[str, Any]:
    status = str(training_state.get("trainability_status") or "")
    if not status and isinstance(training_state.get("trainability"), dict):
        status = str(training_state["trainability"].get("status") or "")
    return {
        "status": status,
        "required_model_count": int(training_state.get("required_model_count", 0) or 0),
        "trainable_model_count": int(training_state.get("trainable_model_count", 0) or 0),
        "trained_model_count": int(training_state.get("trained_model_count", 0) or 0),
        "strict_pass_model_count": int(training_state.get("strict_pass_model_count", 0) or 0),
        "blocked_models": list(training_state.get("blocked_models") or [])[:3],
    }


def _lineage_portfolio_light(factory_state: Dict[str, Any]) -> Dict[str, Any]:
    tracked_ids = _tracked_portfolio_ids()
    tracked_ids = list(dict.fromkeys(list(tracked_ids) + _runtime_alias_ids(factory_state)))
    running_ids = _running_portfolio_ids()
    execution_root = _execution_root()
    grouped: Dict[str, List[Dict[str, Any]]] = {portfolio_id: [] for portfolio_id in tracked_ids}
    for lineage in list(factory_state.get("lineages") or []):
        portfolio_id = str(
            lineage.get("runtime_target_portfolio")
            or lineage.get("live_paper_target_portfolio_id")
            or lineage.get("curated_target_portfolio_id")
            or ""
        ).strip()
        if portfolio_id in grouped:
            grouped[portfolio_id].append(dict(lineage))

    severity_rank = {"critical": 2, "warning": 1, "healthy": 0}
    portfolios: List[Dict[str, Any]] = []
    placeholder_count = 0
    for portfolio_id in tracked_ids:
        related = grouped.get(portfolio_id) or []
        path = execution_root / portfolio_id
        evidence = _execution_evidence_for_portfolio(portfolio_id, execution_root)
        has_runtime_state = path.is_dir() and (_has_runtime_state(path) or bool(evidence.get("evidence_store_exists")))
        if not related and not has_runtime_state and portfolio_id not in running_ids:
            placeholder_count += 1
            continue
        account = dict(evidence.get("account") or {})
        training_state = dict(evidence.get("training_state") or {})
        currency = str(account.get("currency") or "USD")
        starting_balance = _compact_number(account.get("starting_balance"))
        if starting_balance == 0.0 and evidence.get("health_status") not in {"critical", "warning"}:
            # keep compatibility with older files that only had current balance
            starting_balance = _compact_number(evidence.get("starting_balance") or account.get("current_balance"))
        current_balance = _compact_number(account.get("current_balance"))
        realized_pnl = _compact_number(account.get("realized_pnl"))
        account_roi_pct = _compact_number(account.get("roi_pct"))
        account_trade_count = int(account.get("trade_count", 0) or 0)
        wins = int(account.get("wins", 0) or 0)
        losses = int(account.get("losses", 0) or 0)
        win_rate = (wins / (wins + losses)) if (wins + losses) > 0 else 0.0
        worst_health = "healthy"
        issue_codes: List[str] = []
        candidate_families: List[str] = []
        runtime_lanes: List[Dict[str, Any]] = []
        realized_roi_pct = account_roi_pct
        trade_count = account_trade_count
        paper_days = 0
        score_pct = 0.0
        for lineage in related:
            health = str(lineage.get("execution_health_status") or "healthy")
            if severity_rank.get(health, 0) > severity_rank.get(worst_health, 0):
                worst_health = health
            for item in lineage.get("execution_issue_codes") or []:
                code = str(item).strip()
                if code and code not in issue_codes:
                    issue_codes.append(code)
            family_id = str(lineage.get("family_id") or "").strip()
            if family_id and family_id not in candidate_families:
                candidate_families.append(family_id)
            lane_kind = str(lineage.get("runtime_lane_kind") or "").strip()
            if lane_kind:
                runtime_lanes.append(
                    {
                        "family_id": family_id,
                        "lineage_id": str(lineage.get("lineage_id") or ""),
                        "lane_kind": lane_kind,
                        "lane_reason": str(lineage.get("runtime_lane_reason") or ""),
                        "runtime_target_portfolio": str(lineage.get("runtime_target_portfolio") or ""),
                        "canonical_target_portfolio": str(lineage.get("canonical_target_portfolio") or ""),
                    }
                )
            realized_roi_pct = max(
                realized_roi_pct,
                _compact_number(
                    lineage.get("curated_paper_roi_pct")
                    if lineage.get("curated_paper_roi_pct") is not None
                    else lineage.get("realized_roi_pct")
                ),
            )
            trade_count = max(
                trade_count,
                int(
                    lineage.get("paper_trade_count")
                    or lineage.get("curated_paper_closed_trade_count")
                    or 0
                ),
            )
            paper_days = max(paper_days, int(lineage.get("paper_days") or 0))
            score_pct = max(score_pct, _compact_number(lineage.get("readiness_score_pct")))
        runtime_health_status = str(evidence.get("health_status") or "").strip()
        runtime_health_issue_codes = [
            str(item).strip()
            for item in list(evidence.get("issue_codes") or [])
            if str(item).strip()
        ]
        readiness_status = str(evidence.get("status") or "").strip()
        if not readiness_status:
            readiness_status = str(evidence.get("status") or "").strip()
        if not runtime_health_status:
            if readiness_status in {"paper_validating", "validation_blocked", "research_only"}:
                runtime_health_status = "warning"
            elif readiness_status in {"running", "active"}:
                runtime_health_status = "healthy"
            else:
                runtime_health_status = "warning"
        if runtime_health_status in severity_rank:
            worst_health = runtime_health_status
            if runtime_health_issue_codes:
                issue_codes = list(dict.fromkeys(runtime_health_issue_codes))
        running = portfolio_id in running_ids or bool(evidence.get("running"))
        display_status = (
            readiness_status
            if readiness_status in {"paper_validating", "validation_blocked", "research_only", "running", "active"}
            else (
                "active"
                if running
                else ("blocked" if worst_health == "critical" else ("degraded" if worst_health == "warning" else "idle"))
            )
        )
        assessment = _assessment_progress(
            paper_days=paper_days,
            trade_count=trade_count,
            labels=[portfolio_id],
            realized_roi_pct=realized_roi_pct,
            current_stage="paper",
        )
        first_assessment = _first_assessment_progress(
            paper_days=paper_days,
            trade_count=trade_count,
            labels=[portfolio_id],
            realized_roi_pct=realized_roi_pct,
            current_stage="paper",
        )
        portfolios.append(
            {
                "portfolio_id": portfolio_id,
                "label": _title_case_slug(portfolio_id),
                "category": "execution_runner",
                "currency": currency,
                "starting_balance": starting_balance,
                "current_balance": current_balance,
                "realized_pnl": realized_pnl,
                "roi_pct": realized_roi_pct,
                "win_rate": _compact_number(win_rate),
                "wins": wins,
                "losses": losses,
                "drawdown_pct": 0.0,
                "trade_count": trade_count,
                "paper_days": paper_days,
                "status": "running" if running else "idle",
                "display_status": display_status,
                "running": running,
                "heartbeat_ts": evidence.get("heartbeat_ts"),
                "heartbeat_age_seconds": evidence.get("heartbeat_age_seconds"),
                "error": str(evidence.get("error") or "") or None,
                "readiness_status": readiness_status or (worst_health if related else ("running" if running else "unknown")),
                "readiness_score_pct": score_pct,
                "readiness_blockers": issue_codes[:3],
                "training_progress": {
                    "tracked_examples": int(training_state.get("tracked_examples", 0) or 0),
                    "labeled_examples": int(training_state.get("labeled_examples", 0) or 0),
                    "pending_labels": int(training_state.get("pending_labels", 0) or 0),
                    "closed_trades": int(training_state.get("closed_trades", 0) or 0),
                },
                "trainability": _extract_trainability(training_state),
                "candidate_context_count": len(related),
                "runtime_lane_count": len(runtime_lanes),
                "runtime_lanes": runtime_lanes[:4],
                "primary_incumbent_lineage_id": next(
                    (item["lineage_id"] for item in runtime_lanes if item.get("lane_kind") == "primary_incumbent"),
                    None,
                ),
                "isolated_challenger_lineage_id": next(
                    (item["lineage_id"] for item in runtime_lanes if item.get("lane_kind") == "isolated_challenger"),
                    None,
                ),
                "live_manifest_count": 0,
                "candidate_families": candidate_families[:4],
                "recent_trades": [],
                "recent_events": [],
                "state_excerpt": {},
                "blocked": worst_health == "critical" and not running,
                "has_runtime_state": True,
                "is_placeholder": False,
                "assessment": assessment,
                "first_assessment": first_assessment,
                "execution_health_status": worst_health if related or runtime_health_status else ("healthy" if running else "warning"),
                "execution_issue_codes": issue_codes[:3],
                "execution_recommendation_context": (evidence.get("recommendation_context") or issue_codes)[:1],
                "evidence_source": str(evidence.get("evidence_source") or ""),
                "evidence_store": str(evidence.get("store_target") or portfolio_id),
                "runtime_target": str(evidence.get("runtime_target") or portfolio_id),
                "is_alias_store": bool(evidence.get("is_runtime_alias")),
            }
        )
    research_summary = dict(factory_state.get("research_summary") or {})
    blocked_count = sum(
        1
        for row in portfolios
        if str(row.get("execution_health_status") or "") in {"critical", "warning"} and not row.get("running")
    )
    return {
        "portfolio_count": len(portfolios),
        "placeholder_count": placeholder_count,
        "running_count": sum(1 for row in portfolios if row.get("running")),
        "blocked_count": blocked_count,
        "realized_pnl_total": _compact_number(research_summary.get("paper_pnl")),
        "best_performer": _best_execution_performer(portfolios),
        "portfolios": portfolios,
        "placeholders": [],
        "mode": "light_snapshot",
        "note": "Execution summary is derived from factory state and active runner processes in lightweight mode.",
    }


def _portfolio_snapshot(path: Path) -> Dict[str, Any]:
    has_runtime_state = _has_runtime_state(path)
    account = _read_json(path / "account.json", default={}) or {}
    heartbeat = _read_json(path / "heartbeat.json", default={}) or {}
    state = _read_json(path / "state.json", default={}) or {}
    readiness = _read_json(path / "readiness.json", default={}) or {}
    config_snapshot = _read_json(path / "config_snapshot.json", default={}) or {}
    trades = _read_jsonl(path / "trades.jsonl", limit=8)
    events = _read_jsonl(path / "events.jsonl", limit=10)

    heartbeat_ts = heartbeat.get("ts") or account.get("last_updated") or state.get("last_cycle_at")
    heartbeat_age = _age_seconds(heartbeat_ts)
    observed_paper_days = int(
        state.get("paper_days")
        or state.get("runtime_days")
        or state.get("days_running")
        or account.get("paper_days")
        or 0
    )
    status = str(
        state.get("status")
        or readiness.get("status")
        or heartbeat.get("status")
        or ("running" if state.get("running") else "idle")
    )
    raw_issue_codes = []
    raw_issue_codes.extend(str(item) for item in (readiness.get("blockers") or []) if str(item).strip())
    if str(state.get("error") or "").strip():
        raw_issue_codes.append("runtime_error")
    heartbeat_health = "healthy"
    runner_market_idle = str(heartbeat.get("skipped") or "").lower() == "market_closed"
    runner_idle_between_cycles = bool(heartbeat.get("idle_until_next_cycle"))
    runner_paper_ready = str(heartbeat.get("status") or "").lower() in ("paper_ready", "idle", "waiting")
    suppress_stale = runner_market_idle or runner_idle_between_cycles or runner_paper_ready
    if not suppress_stale:
        if heartbeat_age is not None and heartbeat_age >= 300:
            raw_issue_codes.append("heartbeat_stale")
            heartbeat_health = "critical"
        elif heartbeat_age is not None and heartbeat_age >= 120:
            raw_issue_codes.append("heartbeat_slow")
            heartbeat_health = "warning"
    execution_issue_codes = list(dict.fromkeys(raw_issue_codes))
    blocked = bool(_portfolio_error(state, readiness))
    display_status = status
    if not has_runtime_state:
        status = "placeholder"
        display_status = "placeholder"
    elif blocked:
        display_status = "blocked"
    elif str(heartbeat.get("status") or "").lower() == "stopped":
        display_status = "stopped"
    elif str(readiness.get("status") or "").lower() in {"blocked", "paper_validating", "research_only"}:
        readiness_status = str(readiness.get("status") or "validating")
        display_status = "validation_blocked" if readiness_status == "blocked" else readiness_status
    elif heartbeat_health in {"critical", "warning"}:
        display_status = "degraded"
    elif str(status).lower() == "running" or heartbeat_age is not None:
        display_status = "active"
    else:
        display_status = str(status or "idle")
    assessment = _assessment_progress(
        paper_days=observed_paper_days,
        trade_count=int(account.get("trade_count", state.get("trade_count", 0)) or 0),
        labels=[path.name, config_snapshot.get("category"), config_snapshot.get("label")],
        realized_roi_pct=_compact_number(account.get("roi_pct")),
        current_stage=str(readiness.get("status") or status or ""),
    )
    first_assessment = _first_assessment_progress(
        paper_days=observed_paper_days,
        trade_count=int(account.get("trade_count", state.get("trade_count", 0)) or 0),
        labels=[path.name, config_snapshot.get("category"), config_snapshot.get("label")],
        realized_roi_pct=_compact_number(account.get("roi_pct")),
        current_stage=str(readiness.get("status") or status or ""),
    )
    return {
        "portfolio_id": path.name,
        "label": str(config_snapshot.get("label") or path.name.replace("_", " ").title()),
        "category": str(config_snapshot.get("category") or "portfolio"),
        "currency": str(account.get("currency") or config_snapshot.get("currency") or "USD"),
        "starting_balance": _compact_number(account.get("starting_balance")),
        "current_balance": _compact_number(account.get("current_balance")),
        "realized_pnl": _compact_number(account.get("realized_pnl")),
        "roi_pct": _compact_number(account.get("roi_pct")),
        "drawdown_pct": _compact_number(account.get("drawdown_pct")),
        "trade_count": int(account.get("trade_count", state.get("trade_count", 0)) or 0),
        "paper_days": observed_paper_days,
        "status": status,
        "display_status": display_status,
        "running": bool(state.get("running", heartbeat.get("status") == "running")) or (heartbeat_age is not None and heartbeat_age <= 300),
        "heartbeat_ts": heartbeat_ts,
        "heartbeat_age_seconds": heartbeat_age,
        "error": _portfolio_error(state, readiness),
        "readiness_status": str(readiness.get("status") or ""),
        "readiness_score_pct": _compact_number(readiness.get("score_pct")),
        "readiness_blockers": [str(item) for item in (readiness.get("blockers") or [])],
        "candidate_context_count": int(config_snapshot.get("factory_candidate_context_count", 0) or 0),
        "live_manifest_count": int(config_snapshot.get("factory_live_manifest_count", 0) or 0),
        "candidate_families": sorted(
            {
                str(item.get("family_id") or "")
                for item in (config_snapshot.get("factory_candidate_contexts") or [])
                if item.get("family_id")
            }
        ),
        "recent_trades": trades,
        "recent_events": events,
        "state_excerpt": {
            key: state.get(key)
            for key in [
                "mode",
                "watchlist_size",
                "scan_count",
                "signal_count",
                "opportunity_count",
                "open_hedges",
                "ws_connected",
                "trading_halted",
                "minutes_to_next_settlement",
            ]
            if key in state
        },
        "blocked": blocked,
        "has_runtime_state": has_runtime_state,
        "is_placeholder": not has_runtime_state,
        "assessment": assessment,
        "first_assessment": first_assessment,
        "execution_health_status": (
            "critical"
            if blocked or heartbeat_health == "critical"
            else ("warning" if heartbeat_health == "warning" or str(readiness.get("status") or "").lower() == "blocked" else "healthy")
        ),
        "execution_issue_codes": execution_issue_codes,
        "execution_recommendation_context": (
            [str(state.get("error") or "").strip()]
            if str(state.get("error") or "").strip()
            else [str((readiness.get("blockers") or [])[0])] if (readiness.get("blockers") or []) else []
        ),
    }


def _severity_rank(alert: Dict[str, Any]) -> tuple[int, str]:
    severity = str(alert.get("severity") or "info")
    order = {"critical": 0, "warning": 1, "positive": 2, "info": 3}
    return (order.get(severity, 9), str(alert.get("title") or ""))


def _sanitize_alert_detail(value: Any, *, fallback: str = "") -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    if "Command '['codex', 'exec'" in text or "Command '[\"codex\", \"exec\"" in text:
        return "codex_exec_failed"
    compact = _compact_agent_run_error(text)
    if compact and compact != _clean_operator_text(text, max_len=240):
        return compact
    if "Traceback" in text:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in reversed(lines):
            if line.startswith("Traceback"):
                continue
            if line.startswith("File "):
                continue
            return _clean_operator_text(line, max_len=240)
        return fallback or "runtime_error"
    return _clean_operator_text(text, max_len=240) or fallback


def _finalize_alerts(alerts: Iterable[Dict[str, Any]], *, limit: int = 12) -> List[Dict[str, Any]]:
    severity_rank = {"critical": 0, "warning": 1, "positive": 2, "info": 3}
    deduped: Dict[str, Dict[str, Any]] = {}
    for item in alerts:
        if not isinstance(item, dict):
            continue
        detail = _sanitize_alert_detail(item.get("detail"), fallback="")
        if not detail:
            continue
        alert = dict(item)
        alert["detail"] = detail
        key = str(
            alert.get("dedupe_key")
            or "::".join(
                [
                    str(alert.get("severity") or ""),
                    str(alert.get("title") or ""),
                    str(alert.get("lineage_id") or alert.get("portfolio_id") or ""),
                    detail,
                ]
            )
        )
        existing = deduped.get(key)
        if existing is None:
            alert.pop("dedupe_key", None)
            deduped[key] = alert
            continue
        current_rank = severity_rank.get(str(alert.get("severity") or "info"), 9)
        existing_rank = severity_rank.get(str(existing.get("severity") or "info"), 9)
        if current_rank < existing_rank:
            alert.pop("dedupe_key", None)
            deduped[key] = alert
    return sorted(deduped.values(), key=_severity_rank)[:limit]


def _build_agent_fallback_alerts(agent_runs: Iterable[Dict[str, Any]], *, max_age_hours: float = 24.0) -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc)
    latest_success_by_scope: Dict[tuple[str, str, str], datetime] = {}
    latest_success_by_family_task: Dict[tuple[str, str], datetime] = {}
    normalized_runs: List[Dict[str, Any]] = []
    for run in agent_runs:
        normalized_runs.append(dict(run))
        generated_at = _parse_ts(run.get("generated_at"))
        if generated_at is None:
            continue
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=timezone.utc)
        if run.get("success") is True:
            scope = (
                str(run.get("task_type") or ""),
                str(run.get("family_id") or ""),
                str(run.get("lineage_id") or ""),
            )
            previous = latest_success_by_scope.get(scope)
            if previous is None or generated_at > previous:
                latest_success_by_scope[scope] = generated_at
            family_task = (str(run.get("task_type") or ""), str(run.get("family_id") or ""))
            family_previous = latest_success_by_family_task.get(family_task)
            if family_previous is None or generated_at > family_previous:
                latest_success_by_family_task[family_task] = generated_at
    grouped: Dict[str, Dict[str, Any]] = {}
    for run in normalized_runs:
        if run.get("success") is not False:
            continue
        generated_at = _parse_ts(run.get("generated_at"))
        if generated_at is not None:
            if generated_at.tzinfo is None:
                generated_at = generated_at.replace(tzinfo=timezone.utc)
            age_hours = max(0.0, (now - generated_at).total_seconds()) / 3600.0
            if age_hours > max_age_hours:
                continue
        task_type = str(run.get("task_type") or "agent_task")
        family_id = str(run.get("family_id") or "").strip()
        lineage_id = str(run.get("lineage_id") or "").strip()
        success_scope = (task_type, family_id, lineage_id)
        latest_success = latest_success_by_scope.get(success_scope)
        if latest_success is not None and generated_at is not None and latest_success >= generated_at:
            continue
        family_task_success = latest_success_by_family_task.get((task_type, family_id))
        if task_type in {"runtime_debug_review", "post_eval_critique", "family_bootstrap_generation", "maintenance_resolution_review"}:
            if family_task_success is not None and generated_at is not None and family_task_success >= generated_at:
                continue
        detail = _sanitize_alert_detail(run.get("error") or f"{family_id or 'agent'} fell back from Codex", fallback="codex_exec_failed")
        key = f"{task_type}:{detail}"
        entry = grouped.get(key)
        if entry is None:
            grouped[key] = {
                "severity": "warning",
                "title": f"Agent fallback: {task_type}",
                "detail": detail,
                "task_type": task_type,
                "families": [family_id] if family_id else [],
                "lineages": [lineage_id] if lineage_id else [],
                "count": 1,
                "dedupe_key": f"agent_fallback_group:{key}",
            }
            continue
        entry["count"] = int(entry.get("count") or 0) + 1
        if family_id:
            entry["families"] = list(dict.fromkeys(list(entry.get("families") or []) + [family_id]))
        if lineage_id:
            entry["lineages"] = list(dict.fromkeys(list(entry.get("lineages") or []) + [lineage_id]))
    alerts: List[Dict[str, Any]] = []
    for entry in grouped.values():
        count = int(entry.get("count") or 0)
        families = [str(item) for item in list(entry.get("families") or []) if str(item).strip()]
        detail = str(entry.get("detail") or "")
        if count > 1:
            family_text = ""
            if families:
                shown = ", ".join(families[:2])
                extra = len(families) - 2
                family_text = f" across {shown}" + (f" +{extra} more" if extra > 0 else "")
            detail = f"{detail} · {count} recent runs{family_text}"
        alerts.append(
            {
                "severity": "warning",
                "title": str(entry.get("title") or "Agent fallback"),
                "detail": detail,
                "dedupe_key": str(entry.get("dedupe_key") or ""),
            }
        )
    return alerts


def _primary_issue_codes(portfolio: Dict[str, Any]) -> List[str]:
    issue_codes = [str(item) for item in (portfolio.get("execution_issue_codes") or []) if str(item).strip()]
    generic = {"readiness_blocked", "heartbeat_stale", "heartbeat_slow"}
    specific = [item for item in issue_codes if item not in generic]
    if specific:
        return specific[:3]
    return issue_codes[:3]


def _build_alerts(factory_state: Dict[str, Any], portfolios: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    alerts: List[Dict[str, Any]] = []
    readiness = dict(factory_state.get("readiness") or {})
    operator_signals = dict(factory_state.get("operator_signals") or {})
    for check in readiness.get("checks") or []:
        if not check.get("ok"):
            if str(check.get("name") or "").strip() in {"connector_catalog_ready"}:
                continue
            alerts.append(
                {
                    "severity": "warning",
                    "title": f"Factory check failing: {check.get('name')}",
                    "detail": str(check.get("reason") or ""),
                    "dedupe_key": f"readiness:{check.get('name')}",
                }
            )
    for portfolio in portfolios:
        if portfolio.get("error"):
            severity = "critical" if portfolio.get("running") else "warning"
            alerts.append(
                {
                    "severity": severity,
                    "title": f"{portfolio.get('label')} issue",
                    "detail": str(portfolio.get("error")),
                    "portfolio_id": portfolio.get("portfolio_id"),
                    "dedupe_key": f"portfolio_error:{portfolio.get('portfolio_id')}:{_sanitize_alert_detail(portfolio.get('error'), fallback='runtime_error')}",
                }
            )
        elif str(portfolio.get("execution_health_status") or "") in {"critical", "warning"}:
            notes = _primary_issue_codes(portfolio)
            alerts.append(
                {
                    "severity": str(portfolio.get("execution_health_status") or "warning"),
                    "title": f"{portfolio.get('label')} model health",
                    "detail": ", ".join(notes[:3]) or "execution health degraded",
                    "portfolio_id": portfolio.get("portfolio_id"),
                    "dedupe_key": f"portfolio_health:{portfolio.get('portfolio_id')}:{','.join(notes[:3]) or 'execution health degraded'}",
                }
            )
    for item in list(operator_signals.get("human_action_required") or [])[:4]:
        alerts.append(
            {
                "severity": "critical" if str(item.get("execution_health_status") or "") == "critical" else "warning",
                "title": f"Human action required: {item.get('family_id')}",
                "detail": str(item.get("human_action") or item.get("summary") or item.get("lineage_id") or ""),
                "lineage_id": item.get("lineage_id"),
                "dedupe_key": f"human_action:{item.get('lineage_id')}:{_sanitize_alert_detail(item.get('human_action') or item.get('summary') or item.get('lineage_id') or '', fallback='human_action_required')}",
            }
        )
    return _finalize_alerts(alerts)


def _agent_invocations_by_role(agent_runs: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for run in agent_runs:
        result_payload = dict(run.get("result_payload") or {})
        names = [str(result_payload.get("lead_agent_role") or "").strip()]
        names.extend(str(item).strip() for item in (result_payload.get("collaborating_agent_roles") or []))
        for name in names:
            if not name:
                continue
            counts[name] = counts.get(name, 0) + 1
    return counts


def _desk_member_invocation_counts(agent_runs: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for run in agent_runs:
        if not run.get("success"):
            continue
        task_type = str(run.get("task_type") or "").strip()
        model_class = str(run.get("model_class") or "").strip()
        model = str(run.get("model") or "").strip()
        members: List[str] = []
        if task_type in {"proposal_generation", "family_bootstrap_generation"}:
            members.extend(["hypothesis_author", "feature_ideator", "pipeline_assembler"])
        if task_type == "underperformance_tweak":
            members.append("genome_mutation_runner")
        if task_type == "post_eval_critique":
            members.extend(["evaluation_integrator", "capital_risk_reviewer", "promotion_policy_reviewer"])
        if task_type == "runtime_debug_review":
            members.extend(["execution_path_reviewer", "evaluation_integrator"])
        if task_type == "maintenance_resolution_review":
            members.extend(["evaluation_integrator", "capital_risk_reviewer", "execution_path_reviewer"])
        if model_class == "cheap_structured" or "mini" in model or "spark" in model:
            members.append("test_scaffold")
        for member in members:
            counts[member] = counts.get(member, 0) + 1
    return counts


def _scientific_domain_invocation_counts(agent_runs: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for run in agent_runs:
        if not run.get("success"):
            continue
        result_payload = dict(run.get("result_payload") or {})
        prompt_payload = dict(run.get("prompt_payload") or {})
        domains = list(result_payload.get("scientific_domains") or [])
        if not domains:
            domains = list(prompt_payload.get("hypothesis", {}).get("scientific_domains") or [])
        if not domains:
            domains = list(prompt_payload.get("champion_hypothesis", {}).get("scientific_domains") or [])
        if not domains:
            domains = list(prompt_payload.get("family", {}).get("scientific_domains") or [])
        for domain in domains:
            name = str(domain).strip()
            if not name:
                continue
            counts[name] = counts.get(name, 0) + 1
    return counts


def _build_agent_run_view(agent_runs: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for run in agent_runs:
        result_payload = dict(run.get("result_payload") or {})
        prompt_payload = dict(run.get("prompt_payload") or {})
        headline = _clean_operator_text(
            result_payload.get("title")
            or result_payload.get("summary")
            or result_payload.get("thesis")
            or prompt_payload.get("family", {}).get("label")
            or ""
        )
        notes = [
            _clean_operator_text(item)
            for item in (
                result_payload.get("agent_notes")
                or result_payload.get("next_tests")
                or result_payload.get("recommended_actions")
                or []
            )
            if _clean_operator_text(item)
        ]
        rows.append(
            {
                "run_id": str(run.get("run_id") or ""),
                "generated_at": str(run.get("generated_at") or ""),
                "task_type": str(run.get("task_type") or ""),
                "model_class": str(run.get("model_class") or ""),
                "provider": str(run.get("provider") or ""),
                "model": str(run.get("model") or ""),
                "reasoning_effort": str(run.get("reasoning_effort") or ""),
                "family_id": str(run.get("family_id") or ""),
                "lineage_id": str(run.get("lineage_id") or ""),
                "success": bool(run.get("success")),
                "fallback_used": bool(run.get("fallback_used")),
                "duration_ms": int(run.get("duration_ms", 0) or 0),
                "error": _compact_agent_run_error(run.get("error")),
                "artifact_path": str(run.get("artifact_path") or ""),
                "headline": headline,
                "notes": notes[:3],
            }
        )
    return rows


def _title_case_slug(value: str) -> str:
    return str(value or "").replace("_", " ").replace("-", " ").title()


def _lineage_registry_context(factory_state: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    state_ids = {
        str(row.get("lineage_id") or "").strip()
        for row in list(factory_state.get("lineages") or [])
        if str(row.get("lineage_id") or "").strip()
    }
    if not state_ids:
        return {}, {}
    registry = FactoryRegistry(_factory_root())
    records_by_id = {
        record.lineage_id: record
        for record in registry.lineages()
        if record.lineage_id in state_ids
    }
    genome_params_by_id: Dict[str, Dict[str, Any]] = {}
    for lineage_id in state_ids:
        genome = registry.load_genome(lineage_id)
        genome_params_by_id[lineage_id] = dict(getattr(genome, "parameters", {}) or {})
    return records_by_id, genome_params_by_id


def _lineage_parameter_summary(parameters: Dict[str, Any]) -> Dict[str, Any]:
    horizon = parameters.get("selected_horizon_seconds")
    try:
        horizon_value = int(horizon) if horizon is not None else None
    except Exception:
        horizon_value = None
    min_edge = parameters.get("selected_min_edge")
    stake_fraction = parameters.get("selected_stake_fraction")
    return {
        "model_class": str(parameters.get("selected_model_class") or "").strip() or None,
        "horizon_seconds": horizon_value,
        "feature_subset": str(parameters.get("selected_feature_subset") or "").strip() or None,
        "min_edge": _compact_number(min_edge) if min_edge is not None else None,
        "stake_fraction": _compact_number(stake_fraction) if stake_fraction is not None else None,
    }


def _lineage_short_name(lineage_id: str) -> str:
    parts = str(lineage_id or "").split(":")
    if len(parts) >= 3:
        return f"{_title_case_slug(parts[-2])} {parts[-1]}"
    if len(parts) >= 2:
        return _title_case_slug(parts[-1])
    return str(lineage_id or "")


def _lineage_sort_key(node: Dict[str, Any]) -> tuple[int, float, str]:
    created = _parse_ts(node.get("created_at"))
    return (
        int(node.get("depth") or 0),
        created.timestamp() if created is not None else 0.0,
        str(node.get("lineage_id") or ""),
    )


def _history_sort_key(node: Dict[str, Any]) -> tuple[float, str]:
    created = _parse_ts(node.get("created_at"))
    return (
        created.timestamp() if created is not None else 0.0,
        str(node.get("lineage_id") or ""),
    )


def _build_lineage_atlas(factory_state: Dict[str, Any]) -> Dict[str, Any]:
    state_rows = [dict(item) for item in list(factory_state.get("lineages") or [])]
    if not state_rows:
        return {
            "summary": {
                "family_count": 0,
                "node_count": 0,
                "root_count": 0,
                "max_depth": 0,
                "mutation_count": 0,
                "new_model_count": 0,
                "positive_roi_count": 0,
            },
            "families": [],
        }

    records_by_id, genome_params_by_id = _lineage_registry_context(factory_state)
    family_meta = {item["family_id"]: item for item in _build_family_view(factory_state)}
    family_nodes: Dict[str, List[Dict[str, Any]]] = {}

    for row in state_rows:
        lineage_id = str(row.get("lineage_id") or "").strip()
        family_id = str(row.get("family_id") or "").strip()
        if not lineage_id or not family_id:
            continue
        record = records_by_id.get(lineage_id)
        params = _lineage_parameter_summary(genome_params_by_id.get(lineage_id) or {})
        latest_agent_decision = dict(row.get("latest_agent_decision") or {})
        proposal_agent = dict(row.get("proposal_agent") or {})
        node = {
            "lineage_id": lineage_id,
            "family_id": family_id,
            "display_name": str(getattr(record, "label", "") or _lineage_short_name(lineage_id)),
            "short_name": _lineage_short_name(lineage_id),
            "parent_lineage_id": str(
                getattr(record, "parent_lineage_id", None)
                or row.get("parent_lineage_id")
                or ""
            ).strip() or None,
            "role": str(row.get("role") or getattr(record, "role", "") or ""),
            "current_stage": str(row.get("current_stage") or getattr(record, "current_stage", "") or ""),
            "iteration_status": str(row.get("iteration_status") or getattr(record, "iteration_status", "") or ""),
            "active": bool(row.get("active", getattr(record, "active", True))),
            "created_at": str(getattr(record, "created_at", "") or row.get("created_at") or ""),
            "updated_at": str(getattr(record, "updated_at", "") or row.get("updated_at") or ""),
            "fitness_score": _compact_number(row.get("fitness_score")),
            "monthly_roi_pct": _compact_number(row.get("monthly_roi_pct")),
            "paper_days": int(row.get("paper_days", 0) or 0),
            "trade_count": int(row.get("trade_count", 0) or 0),
            "assessment": _assessment_progress(
                paper_days=int(row.get("paper_days", 0) or 0),
                trade_count=int(row.get("trade_count", 0) or 0),
                labels=[family_id, row.get("current_stage"), row.get("curated_target_portfolio_id")],
                realized_roi_pct=_compact_number(row.get("monthly_roi_pct")),
                current_stage=str(row.get("current_stage") or ""),
            ),
            "first_assessment": _first_assessment_progress(
                paper_days=int(row.get("paper_days", 0) or 0),
                trade_count=int(row.get("live_paper_trade_count", row.get("trade_count", 0)) or 0),
                labels=[family_id, row.get("current_stage"), row.get("live_paper_target_portfolio_id")],
                realized_roi_pct=_compact_number(row.get("live_paper_roi_pct", row.get("monthly_roi_pct"))),
                current_stage=str(row.get("current_stage") or ""),
            ),
            "creation_kind": str(getattr(record, "creation_kind", "") or row.get("creation_kind") or "").strip() or None,
            "source_idea_id": str(getattr(record, "source_idea_id", "") or row.get("source_idea_id") or "").strip() or None,
            "has_artifact_package": bool(row.get("latest_artifact_package")),
            "latest_artifact_package": str(row.get("latest_artifact_package") or ""),
            "hypothesis_origin": str(row.get("hypothesis_origin") or ""),
            "lead_agent_role": str(row.get("lead_agent_role") or getattr(record, "lead_agent_role", "") or ""),
            "collaborating_agent_roles": [str(item) for item in (row.get("collaborating_agent_roles") or getattr(record, "collaborating_agent_roles", []) or [])],
            "scientific_domains": [str(item) for item in (row.get("scientific_domains") or getattr(record, "scientific_domains", []) or [])],
            "target_portfolios": [str(item) for item in (row.get("target_portfolios") or getattr(record, "target_portfolios", []) or [])],
            "execution_health_status": str(row.get("execution_health_status") or ""),
            "execution_issue_codes": [str(item) for item in (row.get("execution_issue_codes") or [])],
            "latest_agent_provider": str(latest_agent_decision.get("provider") or ""),
            "latest_agent_model": str(latest_agent_decision.get("model") or ""),
            "proposal_provider": str(proposal_agent.get("provider") or ""),
            "proposal_model": str(proposal_agent.get("model") or ""),
            "selected_model_class": params["model_class"],
            "selected_horizon_seconds": params["horizon_seconds"],
            "selected_feature_subset": params["feature_subset"],
            "selected_min_edge": params["min_edge"],
            "selected_stake_fraction": params["stake_fraction"],
            "runtime_lane_selected": bool(row.get("runtime_lane_selected")),
            "runtime_lane_kind": row.get("runtime_lane_kind"),
            "runtime_lane_reason": row.get("runtime_lane_reason"),
            "runtime_target_portfolio": row.get("runtime_target_portfolio"),
            "canonical_target_portfolio": row.get("canonical_target_portfolio"),
            "suppressed_runtime_sibling": bool(row.get("suppressed_runtime_sibling")),
            "backtest_roi_pct": _compact_number(row.get("backtest_roi_pct")) if row.get("backtest_roi_pct") is not None else _load_backtest_roi(_project_root(), family_id, lineage_id).get("backtest_roi_pct"),
            "child_lineage_ids": [],
            "depth": 0,
        }
        family_nodes.setdefault(family_id, []).append(node)

    atlas_families: List[Dict[str, Any]] = []
    for family_id, nodes in family_nodes.items():
        nodes_by_id = {str(node["lineage_id"]): node for node in nodes}
        for node in nodes:
            parent_id = node.get("parent_lineage_id")
            if parent_id and parent_id in nodes_by_id:
                nodes_by_id[parent_id]["child_lineage_ids"].append(node["lineage_id"])

        depth_cache: Dict[str, int] = {}

        def _depth(lineage_id: str, trail: set[str] | None = None) -> int:
            if lineage_id in depth_cache:
                return depth_cache[lineage_id]
            node = nodes_by_id.get(lineage_id)
            if node is None:
                return 0
            parent_id = str(node.get("parent_lineage_id") or "").strip()
            if not parent_id or parent_id not in nodes_by_id:
                depth_cache[lineage_id] = 0
                return 0
            trail = set(trail or set())
            if lineage_id in trail:
                depth_cache[lineage_id] = 0
                return 0
            trail.add(lineage_id)
            depth_cache[lineage_id] = _depth(parent_id, trail) + 1
            return depth_cache[lineage_id]

        for node in nodes:
            node["child_lineage_ids"] = sorted(
                list(dict.fromkeys(node.get("child_lineage_ids") or [])),
                key=lambda item: _history_sort_key(nodes_by_id.get(item, {})),
            )
            node["depth"] = _depth(str(node["lineage_id"]))

        roots = sorted(
            [node["lineage_id"] for node in nodes if not node.get("parent_lineage_id") or node["parent_lineage_id"] not in nodes_by_id],
            key=lambda item: _history_sort_key(nodes_by_id[item]),
        )
        champion = next((node for node in nodes if node.get("role") == "champion"), None)
        summary = family_meta.get(family_id, {})
        primary_runtime_node = next(
            (node for node in nodes if str(node.get("lineage_id") or "") == str(summary.get("primary_incumbent_lineage_id") or "")),
            {},
        )
        isolated_runtime_node = next(
            (node for node in nodes if str(node.get("lineage_id") or "") == str(summary.get("isolated_challenger_lineage_id") or "")),
            {},
        )
        sorted_nodes = sorted(nodes, key=_lineage_sort_key)
        history = sorted(nodes, key=_history_sort_key, reverse=True)
        atlas_families.append(
            {
                "family_id": family_id,
                "label": str(summary.get("label") or _title_case_slug(family_id)),
                "champion_lineage_id": str(summary.get("champion_lineage_id") or (champion or {}).get("lineage_id") or ""),
                "active_lineage_count": int(summary.get("active_lineage_count", 0) or sum(1 for node in nodes if node.get("active"))),
                "retired_lineage_count": int(summary.get("retired_lineage_count", 0) or sum(1 for node in nodes if not node.get("active"))),
                "target_portfolios": list(summary.get("target_portfolios") or []),
                "primary_incumbent_lineage_id": summary.get("primary_incumbent_lineage_id"),
                "isolated_challenger_lineage_id": summary.get("isolated_challenger_lineage_id"),
                "runtime_lane_reason": summary.get("runtime_lane_reason"),
                "runtime_target_portfolio": summary.get("runtime_target_portfolio")
                or isolated_runtime_node.get("runtime_target_portfolio")
                or primary_runtime_node.get("runtime_target_portfolio"),
                "canonical_target_portfolio": summary.get("canonical_target_portfolio")
                or isolated_runtime_node.get("canonical_target_portfolio")
                or primary_runtime_node.get("canonical_target_portfolio"),
                "root_lineage_ids": roots,
                "max_depth": max((int(node.get("depth") or 0) for node in nodes), default=0),
                "mutation_count": sum(1 for node in nodes if node.get("creation_kind") == "mutation"),
                "new_model_count": sum(1 for node in nodes if node.get("creation_kind") == "new_model"),
                "agent_backed_count": sum(
                    1
                    for node in nodes
                    if node.get("latest_agent_provider") or node.get("proposal_provider") or node.get("hypothesis_origin", "").startswith("real_agent")
                ),
                "nodes": sorted_nodes,
                "history": history,
            }
        )

    atlas_families = sorted(
        atlas_families,
        key=lambda item: (
            -int(item.get("active_lineage_count") or 0),
            -int(item.get("max_depth") or 0),
            str(item.get("label") or ""),
        ),
    )
    all_nodes = [node for family in atlas_families for node in family.get("nodes") or []]
    return {
        "summary": {
            "family_count": len(atlas_families),
            "node_count": len(all_nodes),
            "root_count": sum(len(family.get("root_lineage_ids") or []) for family in atlas_families),
            "max_depth": max((int(family.get("max_depth") or 0) for family in atlas_families), default=0),
            "mutation_count": sum(1 for node in all_nodes if node.get("creation_kind") == "mutation"),
            "new_model_count": sum(1 for node in all_nodes if node.get("creation_kind") == "new_model"),
            "positive_roi_count": sum(1 for node in all_nodes if _compact_number(node.get("monthly_roi_pct")) > 0),
        },
        "families": atlas_families,
    }


def _build_agent_desks(
    factory_state: Dict[str, Any],
    journal_actions: List[str],
    agent_runs: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    deterministic_display_names = {
        "Director": "Control Router",
        "Budget Allocator": "Budget Policy",
        "Venue/Data Curator": "Venue/Data Policy",
        "Genome Mutator": "Mutation Engine",
        "Evaluator": "Evaluation Engine",
        "Risk Governor": "Risk Rules Engine",
        "Promotion Arbiter": "Promotion Rules Engine",
        "Goldfish Bridge": "Execution Bridge",
    }
    agent_roles = dict(factory_state.get("agent_roles") or {})
    lineages = list(factory_state.get("lineages") or [])
    recent_text = " | ".join(journal_actions[-12:])
    invocation_counts = _agent_invocations_by_role(agent_runs)
    desk_invocation_counts = _desk_member_invocation_counts(agent_runs)
    domain_invocation_counts = _scientific_domain_invocation_counts(agent_runs)
    families_by_agent: Dict[str, set[str]] = {}
    stages_by_agent: Dict[str, set[str]] = {}
    lineages_by_agent: Dict[str, set[str]] = {}

    for lineage in lineages:
        family_id = str(lineage.get("family_id") or "")
        stage = str(lineage.get("current_stage") or "")
        lineage_id = str(lineage.get("lineage_id") or "")
        names = [str(lineage.get("lead_agent_role") or "").strip()]
        names.extend(str(item).strip() for item in (lineage.get("collaborating_agent_roles") or []))
        for name in names:
            if not name:
                continue
            families_by_agent.setdefault(name, set()).add(family_id)
            stages_by_agent.setdefault(name, set()).add(stage)
            lineages_by_agent.setdefault(name, set()).add(lineage_id)

    desks: List[Dict[str, Any]] = []
    for tier, members in agent_roles.items():
        desk_kind = "algorithmic_control" if str(tier) == "tier0_deterministic" else "agent_desk"
        rows: List[Dict[str, Any]] = []
        for member in members:
            member_name = str(member)
            display_name = deterministic_display_names.get(member_name, member_name) if desk_kind == "algorithmic_control" else member_name
            involvement = len(lineages_by_agent.get(member_name, set()))
            recent_mentions = member_name.lower() in recent_text.lower()
            real_invocation_count = int(invocation_counts.get(member_name, 0)) + int(desk_invocation_counts.get(member_name, 0))
            if real_invocation_count > 0:
                status = "model_active"
            elif involvement or recent_mentions:
                status = "coverage_only"
            else:
                status = "standby"
            rows.append(
                {
                    "name": member_name,
                    "display_name": display_name,
                    "status": status,
                    "lineage_count": involvement,
                    "real_invocation_count": real_invocation_count,
                    "families": sorted(families_by_agent.get(member_name, set())),
                    "stages": sorted(stages_by_agent.get(member_name, set())),
                    "recent_mention": recent_mentions,
                }
            )
        model_active_count = sum(1 for row in rows if row["status"] == "model_active")
        coverage_count = sum(1 for row in rows if row["status"] == "coverage_only")
        desks.append(
            {
                "desk_id": tier,
                "label": "Deterministic Control Algorithms" if desk_kind == "algorithmic_control" else _title_case_slug(tier),
                "desk_kind": desk_kind,
                "member_count": len(rows),
                "active_count": model_active_count,
                "coverage_count": coverage_count,
                "members": rows,
                "status": "model_active" if model_active_count else ("coverage_only" if coverage_count else "standby"),
            }
        )

    scientist_rows: List[Dict[str, Any]] = []
    for domain in factory_state.get("scientific_researchers") or []:
        label = _title_case_slug(str(domain))
        involvement = sum(1 for lineage in lineages if str(domain) in [str(item) for item in (lineage.get("scientific_domains") or [])])
        real_invocation_count = int(domain_invocation_counts.get(str(domain), 0))
        status = "model_active" if real_invocation_count > 0 else ("coverage_only" if involvement else "standby")
        scientist_rows.append(
            {
                "name": label,
                "status": status,
                "lineage_count": involvement,
                "real_invocation_count": real_invocation_count,
                "families": sorted(
                    {
                        str(lineage.get("family_id") or "")
                        for lineage in lineages
                        if str(domain) in [str(item) for item in (lineage.get("scientific_domains") or [])]
                    }
                ),
                "stages": sorted(
                    {
                        str(lineage.get("current_stage") or "")
                        for lineage in lineages
                        if str(domain) in [str(item) for item in (lineage.get("scientific_domains") or [])]
                    }
                ),
                "recent_mention": str(domain).lower() in recent_text.lower(),
            }
        )
    desks.append(
        {
            "desk_id": "scientific_swarm",
            "label": "Scientific Swarm",
            "desk_kind": "agent_desk",
            "member_count": len(scientist_rows),
            "active_count": sum(1 for row in scientist_rows if row["status"] == "model_active"),
            "coverage_count": sum(1 for row in scientist_rows if row["status"] == "coverage_only"),
            "members": scientist_rows,
            "status": (
                "model_active"
                if any(row["status"] == "model_active" for row in scientist_rows)
                else ("coverage_only" if any(row["status"] == "coverage_only" for row in scientist_rows) else "standby")
            ),
        }
    )
    return desks


def _build_family_view(factory_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    lineages_by_id = {
        str(item.get("lineage_id") or ""): dict(item)
        for item in list(factory_state.get("lineages") or [])
        if str(item.get("lineage_id") or "").strip()
    }
    for family in factory_state.get("families") or []:
        champion = dict(family.get("champion") or {})
        primary = lineages_by_id.get(str(family.get("primary_incumbent_lineage_id") or ""), {})
        challenger = lineages_by_id.get(str(family.get("isolated_challenger_lineage_id") or ""), {})
        derived_isolated_ready = bool(
            str(challenger.get("runtime_target_portfolio") or "").strip()
            and str(challenger.get("runtime_target_portfolio") or "").strip()
            != str(primary.get("runtime_target_portfolio") or primary.get("canonical_target_portfolio") or "").strip()
        )
        runtime_lineages = [payload for payload in [primary, challenger] if payload]
        runtime_statuses = [_lineage_paper_runtime_status(payload) for payload in runtime_lineages]
        rows.append(
            {
                "family_id": family.get("family_id"),
                "label": family.get("label"),
                "queue_stage": family.get("queue_stage"),
                "lineage_count": int(family.get("lineage_count", 0) or 0),
                "active_lineage_count": int(family.get("active_lineage_count", 0) or 0),
                "retired_lineage_count": int(family.get("retired_lineage_count", 0) or 0),
                "target_portfolios": list(family.get("target_portfolios") or []),
                "champion_lineage_id": champion.get("lineage_id"),
                "champion_stage": champion.get("current_stage"),
                "champion_roi_pct": _compact_number(champion.get("monthly_roi_pct")),
                "champion_fitness": _compact_number(champion.get("fitness_score")),
                "origin": family.get("origin"),
                "source_idea_id": family.get("source_idea_id"),
                "incubation_status": family.get("incubation_status"),
                "incubation_cycle_created": family.get("incubation_cycle_created"),
                "incubation_notes": list(family.get("incubation_notes") or []),
                "incubation_decided_at": family.get("incubation_decided_at"),
                "incubation_decision_reason": family.get("incubation_decision_reason"),
                "primary_incumbent_lineage_id": family.get("primary_incumbent_lineage_id"),
                "isolated_challenger_lineage_id": family.get("isolated_challenger_lineage_id"),
                "prepared_isolated_lane_lineage_id": family.get("prepared_isolated_lane_lineage_id"),
                "runtime_lane_reason": family.get("runtime_lane_reason"),
                "activation_status": family.get("activation_status"),
                "alias_runner_running": bool(family.get("alias_runner_running")),
                "isolated_evidence_ready": bool(family.get("isolated_evidence_ready")) or derived_isolated_ready,
                "paper_runtime_expected_count": sum(1 for payload in runtime_lineages if _lineage_expected_to_trade(payload)),
                "paper_runtime_running_count": sum(1 for status in runtime_statuses if status == "paper_running"),
                "paper_runtime_statuses": runtime_statuses,
                "weak_family": bool(family.get("weak_family")),
                "autopilot_status": family.get("autopilot_status"),
                "autopilot_actions": list(family.get("autopilot_actions") or []),
                "autopilot_reason": family.get("autopilot_reason"),
                "autopilot_issue_codes": list(family.get("autopilot_issue_codes") or []),
                "autopilot_live_roi_pct": _compact_number(family.get("autopilot_live_roi_pct")),
                "autopilot_live_win_rate": _compact_number(family.get("autopilot_live_win_rate")),
                "autopilot_trade_count": int(family.get("autopilot_trade_count", 0) or 0),
                "curated_rankings": list(family.get("curated_rankings") or []),
            }
        )
    return rows


def _build_model_league_view(factory_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    lineages_by_id = {
        str(item.get("lineage_id") or ""): dict(item)
        for item in list(factory_state.get("lineages") or [])
        if str(item.get("lineage_id") or "").strip()
    }
    project_root = _project_root()
    for family in factory_state.get("families") or []:
        primary = lineages_by_id.get(str(family.get("primary_incumbent_lineage_id") or ""), {})
        challenger = lineages_by_id.get(str(family.get("isolated_challenger_lineage_id") or ""), {})
        derived_isolated_ready = bool(
            str(challenger.get("runtime_target_portfolio") or "").strip()
            and str(challenger.get("runtime_target_portfolio") or "").strip()
            != str(primary.get("runtime_target_portfolio") or primary.get("canonical_target_portfolio") or "").strip()
        )
        rankings = []
        for item in list(family.get("curated_rankings") or [])[:3]:
            lineage = lineages_by_id.get(str(item.get("lineage_id") or ""), {})
            bt_data = _load_backtest_roi(
                project_root, str(family.get("family_id") or ""), str(item.get("lineage_id") or "")
            )
            assessment = _assessment_progress(
                paper_days=int(lineage.get("paper_days", 0) or 0),
                trade_count=int(item.get("paper_closed_trade_count", 0) or 0),
                labels=[
                    family.get("family_id"),
                    item.get("target_portfolio_id"),
                    item.get("current_stage"),
                ],
                realized_roi_pct=_compact_number(item.get("paper_roi_pct")),
                current_stage=str(item.get("current_stage") or lineage.get("current_stage") or ""),
            )
            first_assessment = _first_assessment_progress(
                paper_days=int(lineage.get("paper_days", 0) or 0),
                trade_count=int(lineage.get("live_paper_trade_count", item.get("paper_closed_trade_count", 0)) or 0),
                labels=[
                    family.get("family_id"),
                    item.get("target_portfolio_id"),
                    item.get("current_stage"),
                ],
                realized_roi_pct=_compact_number(lineage.get("live_paper_roi_pct", item.get("paper_roi_pct"))),
                current_stage=str(item.get("current_stage") or lineage.get("current_stage") or ""),
            )
            rankings.append(
                {
                    "lineage_id": str(item.get("lineage_id") or ""),
                    "family_rank": int(item.get("family_rank") or 0),
                    "ranking_score": _compact_number(item.get("ranking_score")),
                    "target_portfolio_id": str(item.get("target_portfolio_id") or ""),
                    "paper_roi_pct": _compact_number(item.get("paper_roi_pct")),
                    "paper_realized_pnl": _compact_number(item.get("paper_realized_pnl")),
                    "paper_win_rate": _compact_number(item.get("paper_win_rate")),
                    "paper_closed_trade_count": int(item.get("paper_closed_trade_count", 0) or 0),
                    "strict_gate_pass": bool(item.get("strict_gate_pass", False)),
                    "current_stage": str(item.get("current_stage") or ""),
                    "runtime_lane_kind": lineage.get("runtime_lane_kind"),
                    "runtime_lane_reason": lineage.get("runtime_lane_reason"),
                    "runtime_target_portfolio": lineage.get("runtime_target_portfolio"),
                    "canonical_target_portfolio": lineage.get("canonical_target_portfolio"),
                    "assessment": assessment,
                    "first_assessment": first_assessment,
                }
            )
        rows.append(
            {
                "family_id": str(family.get("family_id") or ""),
                "label": str(family.get("label") or ""),
                "origin": family.get("origin"),
                "source_idea_id": family.get("source_idea_id"),
                "incubation_status": family.get("incubation_status"),
                "incubation_cycle_created": family.get("incubation_cycle_created"),
                "incubation_decided_at": family.get("incubation_decided_at"),
                "incubation_decision_reason": family.get("incubation_decision_reason"),
                "primary_incumbent_lineage_id": family.get("primary_incumbent_lineage_id"),
                "isolated_challenger_lineage_id": family.get("isolated_challenger_lineage_id"),
                "prepared_isolated_lane_lineage_id": family.get("prepared_isolated_lane_lineage_id"),
                "runtime_lane_reason": family.get("runtime_lane_reason"),
                "activation_status": family.get("activation_status"),
                "alias_runner_running": bool(family.get("alias_runner_running")),
                "isolated_evidence_ready": bool(family.get("isolated_evidence_ready")) or derived_isolated_ready,
                "rankings": rankings,
            }
        )
    return rows


def _build_operator_signal_view(factory_state: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(factory_state.get("operator_signals") or {})
    lineage_rows = {
        str(item.get("lineage_id") or "").strip(): dict(item)
        for item in list(factory_state.get("lineages") or [])
        if str(item.get("lineage_id") or "").strip()
    }
    positives_raw = [dict(item) for item in list(payload.get("positive_models") or [])]
    grouped_positive: Dict[str, Dict[str, Any]] = {}
    for item in positives_raw:
        evidence_source_type = str(item.get("evidence_source_type") or "lineage_evaluation")
        evidence_key = str(item.get("curated_target_portfolio_id") or item.get("lineage_id") or "")
        grouping_key = (
            f"{item.get('family_id')}::{evidence_source_type}::{evidence_key}"
            if evidence_source_type == "shared_portfolio_scorecard" and evidence_key
            else str(item.get("lineage_id") or "")
        )
        grouped = grouped_positive.get(grouping_key)
        if grouped is None:
            grouped = dict(item)
            grouped["shared_lineage_ids"] = [str(item.get("lineage_id") or "")]
            grouped["shared_lineage_count"] = 1
            grouped_positive[grouping_key] = grouped
            continue
        grouped["shared_lineage_ids"] = list(dict.fromkeys(list(grouped.get("shared_lineage_ids") or []) + [str(item.get("lineage_id") or "")]))
        grouped["shared_lineage_count"] = len(grouped["shared_lineage_ids"])
        current_rank = grouped.get("curated_family_rank")
        incoming_rank = item.get("curated_family_rank")
        if current_rank is None or (incoming_rank is not None and int(incoming_rank) < int(current_rank)):
            grouped.update({k: v for k, v in item.items() if k not in {"shared_lineage_ids", "shared_lineage_count"}})
            grouped["shared_lineage_ids"] = list(dict.fromkeys(list(grouped.get("shared_lineage_ids") or []) + [str(item.get("lineage_id") or "")]))
            grouped["shared_lineage_count"] = len(grouped["shared_lineage_ids"])
    positives = []
    for item in grouped_positive.values():
        row = dict(item)
        row["assessment"] = _assessment_progress(
            paper_days=int(row.get("paper_days", 0) or 0),
            trade_count=int(row.get("trade_count", 0) or 0),
            labels=[row.get("family_id"), row.get("current_stage")],
            realized_roi_pct=_compact_number(row.get("roi_pct")),
            current_stage=str(row.get("current_stage") or ""),
        )
        row["first_assessment"] = _first_assessment_progress(
            paper_days=int(row.get("paper_days", 0) or 0),
            trade_count=int(row.get("trade_count", 0) or 0),
            labels=[row.get("family_id"), row.get("current_stage")],
            realized_roi_pct=_compact_number(row.get("roi_pct")),
            current_stage=str(row.get("current_stage") or ""),
        )
        positives.append(row)
    escalations = []
    for item in list(payload.get("escalation_candidates") or []):
        row = dict(item)
        row["assessment"] = _assessment_progress(
            paper_days=int(row.get("paper_days", 0) or 0),
            trade_count=int(row.get("trade_count", 0) or 0),
            labels=[row.get("family_id"), row.get("current_stage")],
            realized_roi_pct=_compact_number(row.get("roi_pct")),
            current_stage=str(row.get("current_stage") or ""),
        )
        row["first_assessment"] = _first_assessment_progress(
            paper_days=int(row.get("paper_days", 0) or 0),
            trade_count=int(row.get("trade_count", 0) or 0),
            labels=[row.get("family_id"), row.get("current_stage")],
            realized_roi_pct=_compact_number(row.get("roi_pct")),
            current_stage=str(row.get("current_stage") or ""),
        )
        escalations.append(row)
    paper_qualification_queue = []
    for item in list(payload.get("paper_qualification_queue") or []):
        row = dict(item)
        row["first_assessment"] = _first_assessment_progress(
            paper_days=int(row.get("paper_days", 0) or 0),
            trade_count=int(row.get("live_trade_count", 0) or 0),
            labels=[row.get("family_id"), row.get("current_stage")],
            realized_roi_pct=0.0,
            current_stage=str(row.get("current_stage") or ""),
        )
        paper_qualification_queue.append(row)
    family_autopilot_actions = {
        str(item.get("family_id") or "").strip()
        for item in list(payload.get("maintenance_queue") or [])
        if str(item.get("action") or "").strip() == "family_autopilot"
    }
    cooldown_hours = max(0, int(getattr(config, "FACTORY_MAINTENANCE_QUEUE_REVIEW_COOLDOWN_HOURS", 12) or 12))
    review_cutoff = datetime.now(timezone.utc) - timedelta(hours=cooldown_hours)
    raw_queue: List[Dict[str, Any]] = []
    for item in list(payload.get("maintenance_queue") or []):
        row = dict(item)
        lineage_id = str(row.get("lineage_id") or "").strip()
        lineage = dict(lineage_rows.get(lineage_id) or {})
        if lineage and not bool(lineage.get("active", True)):
            continue
        reviewed_at = _parse_iso_dt(lineage.get("last_maintenance_review_at") or row.get("last_maintenance_review_at"))
        reviewed_status = str(lineage.get("last_maintenance_review_status") or row.get("last_maintenance_review_status") or "").strip().lower()
        reviewed_action = str(lineage.get("last_maintenance_review_action") or row.get("last_maintenance_review_action") or "").strip().lower()
        action = str(row.get("action") or "").strip().lower()
        if (
            reviewed_at is not None
            and reviewed_at >= review_cutoff
            and reviewed_status in {"completed", "success", "succeeded"}
            and (action == "review_due" or (reviewed_action and reviewed_action == action))
        ):
            continue
        if (
            str(row.get("family_id") or "").strip() in family_autopilot_actions
            and action in {"replace", "retrain", "rework", "retire", "review_due"}
            and str(row.get("source") or "").strip() != "family_autopilot"
            and not bool(row.get("requires_human"))
        ):
            continue
        row["assessment"] = _assessment_progress(
            paper_days=int(row.get("paper_days", 0) or 0),
            trade_count=int(row.get("trade_count", 0) or 0),
            labels=[row.get("family_id"), row.get("current_stage")],
            realized_roi_pct=_compact_number(row.get("roi_pct")),
            current_stage=str(row.get("current_stage") or ""),
        )
        row["first_assessment"] = _first_assessment_progress(
            paper_days=int(row.get("paper_days", 0) or 0),
            trade_count=int(row.get("trade_count", 0) or 0),
            labels=[row.get("family_id"), row.get("current_stage")],
            realized_roi_pct=_compact_number(row.get("roi_pct")),
            current_stage=str(row.get("current_stage") or ""),
        )
        raw_queue.append(row)
    deduped_queue: Dict[str, Dict[str, Any]] = {}
    family_scoped_queue: List[Dict[str, Any]] = []
    for row in raw_queue:
        lineage_id = str(row.get("lineage_id") or "").strip()
        if not lineage_id:
            family_scoped_queue.append(row)
            continue
        current = deduped_queue.get(lineage_id)
        candidate_key = (
            int(row.get("priority", 9) if row.get("priority") is not None else 9),
            0 if row.get("requires_human") else 1,
            0 if str(row.get("source") or "") == "family_autopilot" else 1,
        )
        if current is None:
            deduped_queue[lineage_id] = row
            continue
        current_key = (
            int(current.get("priority", 9) if current.get("priority") is not None else 9),
            0 if current.get("requires_human") else 1,
            0 if str(current.get("source") or "") == "family_autopilot" else 1,
        )
        if candidate_key < current_key:
            deduped_queue[lineage_id] = row
    per_family_cap = max(1, int(getattr(config, "FACTORY_MAINTENANCE_QUEUE_MAX_PER_FAMILY", 3) or 3))
    per_family_counts: Dict[str, int] = {}
    maintenance_queue: List[Dict[str, Any]] = []
    for row in sorted(
        list(deduped_queue.values()) + family_scoped_queue,
        key=lambda item: (
            int(item.get("priority", 9) if item.get("priority") is not None else 9),
            0 if item.get("execution_health_status") == "critical" else 1,
            item.get("family_id") or "",
            item.get("lineage_id") or "",
        ),
    ):
        family_id = str(row.get("family_id") or "").strip()
        if family_id and str(row.get("action") or "") != "family_autopilot":
            count = int(per_family_counts.get(family_id, 0) or 0)
            if count >= per_family_cap:
                continue
            per_family_counts[family_id] = count + 1
        maintenance_queue.append(row)
    return {
        "positive_models": positives,
        "research_positive_models": list(payload.get("research_positive_models") or []),
        "paper_qualification_queue": paper_qualification_queue,
        "first_assessment_candidates": list(payload.get("first_assessment_candidates") or []),
        "escalation_candidates": escalations,
        "human_action_required": list(payload.get("human_action_required") or []),
        "action_inbox": list(payload.get("action_inbox") or []),
        "maintenance_queue": maintenance_queue,
    }


def _build_lineage_table(factory_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    lineages = list(factory_state.get("lineages") or [])
    sorted_lineages = sorted(
        lineages,
        key=lambda row: (
            0 if row.get("active", True) else 1,
            row.get("pareto_rank") if row.get("pareto_rank") is not None else 999,
            -float(row.get("fitness_score", 0.0) or 0.0),
        ),
    )
    rows: List[Dict[str, Any]] = []
    project_root = _project_root()
    for row in sorted_lineages[:24]:
        paper_runtime_status = _lineage_paper_runtime_status(dict(row))
        bt_data = _load_backtest_roi(project_root, str(row.get("family_id") or ""), str(row.get("lineage_id") or ""))
        summary = {
                "lineage_id": row.get("lineage_id"),
                "family_id": row.get("family_id"),
                "role": row.get("role"),
                "current_stage": row.get("current_stage"),
                "iteration_status": row.get("iteration_status"),
                "fitness_score": _compact_number(row.get("fitness_score")),
                "monthly_roi_pct": _compact_number(row.get("monthly_roi_pct")),
                "paper_days": int(row.get("paper_days", 0) or 0),
                "trade_count": int(row.get("trade_count", 0) or 0),
                "assessment": _assessment_progress(
                    paper_days=int(row.get("paper_days", 0) or 0),
                    trade_count=int(
                        (
                            row.get("curated_paper_closed_trade_count")
                            if int(row.get("curated_paper_closed_trade_count", 0) or 0) > 0
                            else row.get("trade_count", 0)
                        )
                        or 0
                    ),
                    labels=[row.get("family_id"), row.get("current_stage"), row.get("curated_target_portfolio_id")],
                    realized_roi_pct=_compact_number(
                        row.get("curated_paper_roi_pct")
                        if int(row.get("curated_paper_closed_trade_count", 0) or 0) > 0
                        else row.get("monthly_roi_pct")
                    ),
                    current_stage=str(row.get("current_stage") or ""),
                ),
                "first_assessment": _first_assessment_progress(
                    paper_days=int(row.get("paper_days", 0) or 0),
                    trade_count=int(row.get("live_paper_trade_count", 0) or 0),
                    labels=[row.get("family_id"), row.get("current_stage"), row.get("live_paper_target_portfolio_id")],
                    realized_roi_pct=_compact_number(row.get("live_paper_roi_pct")),
                    current_stage=str(row.get("current_stage") or ""),
                ),
                "execution_has_signal": bool(row.get("execution_has_signal")),
                "execution_health_status": row.get("execution_health_status"),
                "execution_issue_codes": list(row.get("execution_issue_codes") or []),
                "execution_recommendation_context": list(row.get("execution_recommendation_context") or []),
                "latest_retrain_action": row.get("latest_retrain_action"),
                "latest_retrain_triggered": bool(row.get("latest_retrain_triggered")),
                "latest_execution_refresh_status": row.get("latest_execution_refresh_status"),
                "latest_execution_refresh_reason": row.get("latest_execution_refresh_reason"),
                "latest_execution_refresh_selected": row.get("latest_execution_refresh_selected"),
                "blockers": [str(item) for item in (row.get("blockers") or [])],
                "promotion_scorecard": dict(row.get("promotion_scorecard") or {}),
                "curated_family_rank": row.get("curated_family_rank"),
                "curated_ranking_score": _compact_number(row.get("curated_ranking_score")),
                "curated_target_portfolio_id": row.get("curated_target_portfolio_id"),
                "curated_paper_roi_pct": _compact_number(row.get("curated_paper_roi_pct")),
                "curated_paper_win_rate": _compact_number(row.get("curated_paper_win_rate")),
                "runtime_lane_selected": bool(row.get("runtime_lane_selected")),
                "runtime_lane_kind": row.get("runtime_lane_kind"),
                "runtime_lane_reason": row.get("runtime_lane_reason"),
                "paper_runtime_status": paper_runtime_status,
                "expected_to_trade": _lineage_expected_to_trade(dict(row)),
                "prepared_isolated_lane": bool(row.get("prepared_isolated_lane")),
                "activation_status": row.get("activation_status"),
                "alias_runner_running": bool(row.get("alias_runner_running")),
                "isolate_evidence_start_failed": bool(row.get("isolate_evidence_start_failed")),
                "runtime_target_portfolio": row.get("runtime_target_portfolio"),
                "canonical_target_portfolio": row.get("canonical_target_portfolio"),
                "suppressed_runtime_sibling": bool(row.get("suppressed_runtime_sibling")),
                "hypothesis_origin": row.get("hypothesis_origin"),
                "latest_agent_decision": dict(row.get("latest_agent_decision") or {}),
                "proposal_agent": dict(row.get("proposal_agent") or {}),
                "last_tweak_agent_provider": row.get("last_tweak_agent_provider"),
                "last_tweak_agent_model": row.get("last_tweak_agent_model"),
                "agent_review_due": bool(row.get("agent_review_due")),
                "agent_review_due_reason": row.get("agent_review_due_reason"),
                "last_agent_review_at": row.get("last_agent_review_at"),
                "last_agent_review_status": row.get("last_agent_review_status"),
                "last_debug_review_at": row.get("last_debug_review_at"),
                "last_debug_review_status": row.get("last_debug_review_status"),
                "last_debug_requires_human": bool(row.get("last_debug_requires_human")),
                "last_debug_human_action": row.get("last_debug_human_action"),
                "last_debug_bug_category": row.get("last_debug_bug_category"),
                "last_debug_summary": row.get("last_debug_summary"),
                "source_idea_id": row.get("source_idea_id"),
                "backtest_roi_pct": bt_data["backtest_roi_pct"],
                "backtest_sharpe": bt_data["backtest_sharpe"],
                "backtest_gate_status": bt_data["backtest_gate_status"],
            }
        rows.append(summary)
    return rows


def build_dashboard_snapshot() -> Dict[str, Any]:
    factory_state = _read_json(_factory_state_path(), default={}) or {}
    journal = _read_markdown_sections(_factory_journal_path())
    ideas_path = _ideas_path()
    ideas_text = ideas_path.read_text(encoding="utf-8") if ideas_path.exists() else ""
    idea_items = annotate_idea_statuses(all_ideas(_project_root()), list(factory_state.get("lineages") or []))
    idea_buckets = split_active_and_archived_ideas(idea_items)
    idea_status_counts = _idea_status_counts(idea_items)
    agent_run_rows = recent_agent_runs(_project_root(), limit=24)

    portfolio_paths = {path.name: path for path in _portfolio_dirs()}
    execution_root = _execution_root()
    for portfolio_id in _runtime_alias_ids(factory_state):
        path = execution_root / portfolio_id
        if path.is_dir():
            portfolio_paths.setdefault(portfolio_id, path)
    portfolios = [_portfolio_snapshot(path) for path in portfolio_paths.values()]
    placeholder_portfolios = [row for row in portfolios if row.get("is_placeholder")]
    tracked_portfolios = [
        row
        for row in portfolios
        if row["portfolio_id"] != "command_center" and not row.get("is_placeholder")
    ]

    execution = {
        "portfolio_count": len(tracked_portfolios),
        "placeholder_count": len(placeholder_portfolios),
        "running_count": sum(1 for row in tracked_portfolios if row.get("running")),
        "blocked_count": sum(1 for row in tracked_portfolios if row.get("blocked")),
        "realized_pnl_total": round(sum(_compact_number(row.get("realized_pnl")) for row in tracked_portfolios), 4),
        "portfolios": tracked_portfolios,
        "placeholders": placeholder_portfolios,
    }
    alerts = _build_alerts(factory_state, tracked_portfolios)
    alerts.extend(_build_agent_fallback_alerts(agent_run_rows))
    alerts = _finalize_alerts(alerts)
    research_summary = dict(factory_state.get("research_summary") or {})
    readiness = dict(factory_state.get("readiness") or {})
    paper_runtime = _paper_runtime_summary(factory_state)

    # Derive live connector snapshots directly from the local filesystem/catalog so
    # the API feeds view always includes all venues (binance, betfair, polymarket,
    # yahoo, alpaca) even if the long-running factory process was started before
    # new connectors were added.
    project_root = _project_root()
    connector_adapters = default_connector_catalog(project_root)
    connector_snapshots = [adapter.snapshot().to_dict() for adapter in connector_adapters]

    feed_health = _build_feed_health(factory_state, connector_snapshots)

    return {
        "generated_at": utc_now_iso(),
        "project_root": str(project_root),
        "factory_paused": (project_root / "data" / "factory" / "factory_paused.flag").exists(),
        "api_health": {"status": "ok", "snapshot_source": "factory_state"},
        "api_feeds": _build_connector_feed_health(connector_snapshots),
        "factory": {
            "mode": factory_state.get("agentic_factory_mode"),
            "status": factory_state.get("status"),
            "cycle_count": int(factory_state.get("cycle_count", 0) or 0),
            "readiness": readiness,
            "research_summary": research_summary,
            "paper_runtime": paper_runtime,
            "feed_health": feed_health,
            "families": _build_family_view(factory_state),
            "model_league": _build_model_league_view(factory_state),
            "lineages": _build_lineage_table(factory_state),
            "lineage_atlas": _build_lineage_atlas(factory_state),
            "queue": list(factory_state.get("queue") or []),
            # Surface catalog-based connector snapshots so the UI can see all venues,
            # independent of the long-running factory process.
            "connectors": connector_snapshots,
            "manifests": dict(factory_state.get("manifests") or {}),
            "agent_runs": _build_agent_run_view(agent_run_rows),
            "operator_signals": _build_operator_signal_view(factory_state),
            "execution_bridge": dict(factory_state.get("execution_bridge") or {}),
        },
        "company": {
            "journal_markdown": journal.get("content") or "",
            "recent_actions": journal.get("recent_actions") or [],
            "desks": _build_agent_desks(factory_state, journal.get("recent_actions") or [], agent_run_rows),
            "alerts": alerts,
        },
        "execution": execution,
        "ideas": {
            "present": bool(ideas_text.strip()),
            "path": str(ideas_path),
            "content": ideas_text,
            "line_count": len(ideas_text.splitlines()) if ideas_text else 0,
            "idea_count": len(idea_items),
            "active_count": len(idea_buckets["active"]),
            "archived_count": len(idea_buckets["archived"]),
            "status_counts": idea_status_counts,
            "items": idea_buckets["active"],
            "archived_items": idea_buckets["archived"],
        },
    }


def build_snapshot_v2() -> Dict[str, Any]:
    """Snapshot v2 — versioned contract with explicit typed fields per lineage.

    Calls build_dashboard_snapshot() for the full base payload, then augments
    it with a schema_version tag, a structured runtime block, and a lineage_v2
    array that makes previously implicit decisions explicit (holdoff state,
    venue-scope exclusion, paper portfolio identity, deterministic blockers).
    """
    base = build_dashboard_snapshot()
    base["schema_version"] = "v2"

    # --- Runtime block ---
    try:
        runtime_backend = str(getattr(config, "FACTORY_RUNTIME_BACKEND", "legacy") or "legacy")
        runtime_mode = str(getattr(config, "AGENTIC_FACTORY_MODE", "unknown") or "unknown")
        paper_holdoff_enabled = bool(getattr(config, "FACTORY_PAPER_HOLDOFF_ENABLED", False))
        venue_scope_raw = str(getattr(config, "FACTORY_PAPER_WINDOW_VENUE_SCOPE", "") or "")
    except Exception:
        runtime_backend = "unknown"
        runtime_mode = "unknown"
        paper_holdoff_enabled = False
        venue_scope_raw = ""

    venue_scope_set: set = {v.strip() for v in venue_scope_raw.split(",") if v.strip()} if venue_scope_raw else set()

    base["runtime"] = {
        "backend": runtime_backend,
        "mode": runtime_mode,
        "paused": base.get("factory_paused", False),
        "paper_holdoff_enabled": paper_holdoff_enabled,
        "venue_scope": sorted(venue_scope_set) if venue_scope_set else None,
    }

    # --- Lineage v2 ---
    lineages = list((base.get("factory") or {}).get("lineages") or [])
    families = list((base.get("factory") or {}).get("families") or [])

    # Build venue lookup: family_id -> venue string
    family_venue_map: Dict[str, str] = {}
    family_venues_map: Dict[str, set] = {}
    for fam in families:
        fid = str(fam.get("family_id") or "")
        venue_str = str(fam.get("venue") or "")
        family_venue_map[fid] = venue_str
        family_venues_map[fid] = {v.strip() for v in venue_str.split(",") if v.strip()} if venue_str else set()

    _PAPER_STAGE = "paper"
    _HOLDOFF_EXCLUDED_STATUSES = {"failed", "retiring", "review_requested_rework"}

    lineage_v2: List[Dict[str, Any]] = []
    for lin in lineages:
        lineage_id = str(lin.get("lineage_id") or "")
        family_id = str(lin.get("family_id") or "")
        current_stage = str(lin.get("current_stage") or "")
        iteration_status = str(lin.get("iteration_status") or "")

        venue = family_venue_map.get(family_id, "")

        # Convert string blockers to structured objects
        raw_blockers = list(lin.get("blockers") or [])
        deterministic_blockers = [
            {"code": str(b), "description": str(b), "evidence": None}
            for b in raw_blockers
        ]

        # Holdoff: active paper-stage lineage with healthy status is held off from agentic churn
        holdoff_reason: str | None = None
        if (
            paper_holdoff_enabled
            and current_stage == _PAPER_STAGE
            and iteration_status not in _HOLDOFF_EXCLUDED_STATUSES
        ):
            holdoff_reason = f"paper_holdoff active: stage={current_stage} status={iteration_status}"

        # Venue scope: flag lineages whose family targets venues outside the scope set
        venue_scope_reason: str | None = None
        if venue_scope_set:
            lin_venues = family_venues_map.get(family_id, set())
            if lin_venues and not lin_venues.issubset(venue_scope_set):
                out_of_scope = lin_venues - venue_scope_set
                venue_scope_reason = (
                    f"venue_out_of_scope: {','.join(sorted(out_of_scope))} "
                    f"not in scope={','.join(sorted(venue_scope_set))}"
                )

        # Paper portfolio id — lineage-scoped isolated accounting
        safe_id = lineage_id.replace(":", "__")
        paper_portfolio_id = f"lineage__{safe_id}" if lineage_id else None

        lineage_v2.append({
            "lineage_id": lineage_id,
            "family_id": family_id,
            "venue": venue,
            "canonical_stage": current_stage,
            "deterministic_blockers": deterministic_blockers,
            "holdoff_reason": holdoff_reason,
            "venue_scope_reason": venue_scope_reason,
            "paper_portfolio_id": paper_portfolio_id,
            # created_at for time-in-stage calculations in the UI
            "created_at": str(lin.get("created_at") or "") or None,
        })

    base["lineage_v2"] = lineage_v2

    # --- Mobkit health proxy (derived from agent run data + config) ---
    _agent_runs = list((base.get("factory") or {}).get("agent_runs") or [])
    _run_count = len(_agent_runs)
    _fail_count = sum(1 for r in _agent_runs if not r.get("success", True))
    _fallback_count = sum(1 for r in _agent_runs if r.get("fallback_used", False))
    _by_provider: Dict[str, int] = {}
    _by_task: Dict[str, int] = {}
    _by_model_class: Dict[str, int] = {}
    for _r in _agent_runs:
        _prov = str(_r.get("provider") or "unknown")
        _by_provider[_prov] = _by_provider.get(_prov, 0) + 1
        _task = str(_r.get("task_type") or "unknown")
        _by_task[_task] = _by_task.get(_task, 0) + 1
        _mc = str(_r.get("model_class") or "unknown")
        _by_model_class[_mc] = _by_model_class.get(_mc, 0) + 1

    base["mobkit_health"] = {
        "configured": runtime_backend == "mobkit",
        "backend": runtime_backend,
        "rpc_healthy": None,  # Not directly accessible without a live gateway call
        "recent_runs_24h": _run_count,
        "recent_failures_24h": _fail_count,
        "fallback_used_24h": _fallback_count,
        "success_rate_pct": round((1 - _fail_count / _run_count) * 100, 1) if _run_count > 0 else None,
        "runs_by_provider": dict(sorted(_by_provider.items(), key=lambda x: -x[1])),
        "runs_by_task": dict(sorted(_by_task.items(), key=lambda x: -x[1])),
        "runs_by_model_class": dict(sorted(_by_model_class.items(), key=lambda x: -x[1])),
        "note": "RPC gateway telemetry not accessible from snapshot; proxied from agent run data",
    }

    # --- Budget governance (from config) ---
    try:
        base["budget_governance"] = {
            "daily_budget_usd": float(getattr(config, "FACTORY_DAILY_INFERENCE_BUDGET_USD", 15) or 15),
            "weekly_budget_usd": float(getattr(config, "FACTORY_WEEKLY_INFERENCE_BUDGET_USD", 75) or 75),
            "strict_budgets": bool(
                getattr(config, "FACTORY_STRICT_BUDGETS", False)
                or getattr(config, "FACTORY_ENABLE_STRICT_BUDGETS", False)
            ),
            "force_cheap_ratio": float(getattr(config, "FACTORY_BUDGET_FORCE_CHEAP_RATIO", 0.80) or 0.80),
            "single_agent_ratio": float(getattr(config, "FACTORY_BUDGET_SINGLE_AGENT_RATIO", 0.90) or 0.90),
            "reviewer_removal_ratio": float(getattr(config, "FACTORY_BUDGET_REVIEWER_REMOVAL_RATIO", 0.70) or 0.70),
            # Backend gaps — not tracked yet
            "daily_spend_usd": None,
            "weekly_spend_usd": None,
            "token_count_total": None,
        }
    except Exception:
        base["budget_governance"] = {
            "daily_budget_usd": None, "weekly_budget_usd": None,
            "strict_budgets": False, "note": "Error reading budget config",
        }

    # --- DNA packets per family (from local registry learning memories) ---
    _dna_packets: List[Dict[str, Any]] = []
    try:
        from factory.provenance.dna_extractor import build_family_dna_packet as _build_dna
        _registry = FactoryRegistry(_project_root())
        _family_ids = list({str(l.get("family_id") or "") for l in lineages if l.get("family_id")})
        for _fid in _family_ids[:10]:  # cap at 10 families to stay fast
            try:
                _mems = _registry.learning_memories(family_id=_fid, limit=15)
                _pkt = _build_dna(_fid, _mems)
                _dna_packets.append({
                    "family_id": _fid,
                    "total_lineages_seen": _pkt.total_lineages_seen,
                    "failure_motifs": list(_pkt.failure_motifs),
                    "success_motifs": list(_pkt.success_motifs),
                    "hard_veto_causes": list(_pkt.hard_veto_causes),
                    "retirement_reasons": list(_pkt.retirement_reasons[:5]),
                    "dominant_failure": _pkt.dominant_failure_pattern(),
                    "best_known_roi": _pkt.best_known_roi(),
                    "best_ancestors": [
                        {
                            "lineage_id": _a.lineage_id,
                            "roi": round(_a.roi, 2),
                            "trades": _a.trades,
                            "outcome": _a.outcome,
                            "domains": list(_a.domains),
                        }
                        for _a in _pkt.best_ancestors[:3]
                    ],
                    "worst_relatives": [
                        {"lineage_id": _a.lineage_id, "roi": round(_a.roi, 2), "outcome": _a.outcome}
                        for _a in _pkt.worst_relatives[:3]
                    ],
                    "prompt_text": _pkt.as_prompt_text(),
                })
            except Exception:
                pass
    except Exception:
        pass
    base["dna_packets"] = _dna_packets

    # --- Goldfish health (from config + filesystem evidence) ---
    try:
        import datetime as _dt
        _goldfish_enabled = bool(getattr(config, "FACTORY_ENABLE_GOLDFISH_PROVENANCE", False))
        _gl_root = _project_root() / "data" / "factory" / "goldfish_learning"
        _gl_files = list(_gl_root.glob("*.json")) if _gl_root.exists() else []
        _latest_write: str | None = None
        if _gl_files:
            _latest_mtime = max(f.stat().st_mtime for f in _gl_files if f.exists())
            _latest_write = _dt.datetime.fromtimestamp(_latest_mtime, tz=_dt.timezone.utc).isoformat()
        base["goldfish_health"] = {
            "enabled": _goldfish_enabled,
            "learning_files": len(_gl_files),
            "latest_write": _latest_write,
            "workspace_root": str(getattr(config, "GOLDFISH_WORKSPACE_ROOT", ".goldfish") or ".goldfish"),
            "artefact_root": str(getattr(config, "GOLDFISH_ARTEFACT_ROOT", "./artifacts/goldfish") or "./artifacts/goldfish"),
            "strict_mode": bool(getattr(config, "FACTORY_GOLDFISH_STRICT_MODE", False)),
            "note": "Daemon health state not accessible from snapshot; check goldfish_learning/ for write evidence",
        }
    except Exception as _ge:
        base["goldfish_health"] = {
            "enabled": False, "learning_files": 0, "latest_write": None,
            "note": f"Error reading goldfish health: {_ge}",
        }

    return base


def build_dashboard_snapshot_light() -> Dict[str, Any]:
    factory_state = _read_json(_factory_state_path(), default={}) or {}
    journal = _read_markdown_sections(_factory_journal_path())
    ideas_path = _ideas_path()
    ideas_text = ideas_path.read_text(encoding="utf-8") if ideas_path.exists() else ""
    idea_items = annotate_idea_statuses(all_ideas(_project_root()), list(factory_state.get("lineages") or []))
    idea_buckets = split_active_and_archived_ideas(idea_items)
    idea_status_counts = _idea_status_counts(idea_items)
    agent_run_rows = recent_agent_runs(_project_root(), limit=24)
    alerts = _build_alerts(factory_state, [])
    alerts.extend(_build_agent_fallback_alerts(agent_run_rows))
    alerts = _finalize_alerts(alerts)
    research_summary = dict(factory_state.get("research_summary") or {})
    readiness = dict(factory_state.get("readiness") or {})
    execution_summary = _lineage_portfolio_light(factory_state)
    paper_runtime = _paper_runtime_summary(factory_state)
    connectors = list(factory_state.get("connectors") or [])
    feed_health = _build_feed_health(factory_state, connectors)
    return {
        "generated_at": utc_now_iso(),
        "project_root": str(_project_root()),
        "factory": {
            "mode": factory_state.get("agentic_factory_mode"),
            "status": factory_state.get("status"),
            "cycle_count": int(factory_state.get("cycle_count", 0) or 0),
            "readiness": readiness,
            "research_summary": research_summary,
            "paper_runtime": paper_runtime,
            "feed_health": feed_health,
            "families": _build_family_view(factory_state),
            "model_league": _build_model_league_view(factory_state),
            "lineages": _build_lineage_table(factory_state),
            "lineage_atlas": _build_lineage_atlas(factory_state),
            "queue": list(factory_state.get("queue") or []),
            "connectors": connectors,
            "manifests": dict(factory_state.get("manifests") or {}),
            "agent_runs": _build_agent_run_view(agent_run_rows),
            "operator_signals": _build_operator_signal_view(factory_state),
            "execution_bridge": dict(factory_state.get("execution_bridge") or {}),
        },
        "company": {
            "journal_markdown": journal.get("content") or "",
            "recent_actions": journal.get("recent_actions") or [],
            "desks": _build_agent_desks(factory_state, journal.get("recent_actions") or [], agent_run_rows),
            "alerts": alerts,
        },
        "execution": execution_summary,
        "ideas": {
            "present": bool(ideas_text.strip()),
            "path": str(ideas_path),
            "content": ideas_text,
            "line_count": len(ideas_text.splitlines()) if ideas_text else 0,
            "idea_count": len(idea_items),
            "active_count": len(idea_buckets["active"]),
            "archived_count": len(idea_buckets["archived"]),
            "status_counts": idea_status_counts,
            "items": idea_buckets["active"],
            "archived_items": idea_buckets["archived"],
        },
    }
