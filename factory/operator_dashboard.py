from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

import config
from factory.agent_runtime import recent_agent_runs
from factory.contracts import PromotionStage, utc_now_iso
from factory.idea_intake import all_ideas, annotate_idea_statuses, split_active_and_archived_ideas
from factory.registry import FactoryRegistry


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


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


def _compact_number(value: Any) -> float:
    try:
        return round(float(value or 0.0), 4)
    except Exception:
        return 0.0


def _assessment_uses_slow_thresholds(*labels: str) -> bool:
    normalized = [str(item or "").strip().lower() for item in labels if str(item or "").strip()]
    return any(token.startswith("betfair") or token.startswith("polymarket") for token in normalized)


def _assessment_progress(
    *,
    paper_days: int,
    trade_count: int,
    labels: Iterable[str],
    realized_roi_pct: float | None = None,
    current_stage: str | None = None,
) -> Dict[str, Any]:
    joined_labels = [str(item or "") for item in labels]
    slow = _assessment_uses_slow_thresholds(*joined_labels)
    required_days = int(
        getattr(
            config,
            "FACTORY_PAPER_GATE_MIN_DAYS",
            30,
        )
        or 30
    )
    required_trades = int(
        getattr(
            config,
            "FACTORY_PAPER_GATE_MIN_SLOW_SETTLED" if slow else "FACTORY_PAPER_GATE_MIN_FAST_TRADES",
            10 if slow else 50,
        )
        or (10 if slow else 50)
    )
    observed_days = max(0, int(paper_days or 0))
    observed_trades = max(0, int(trade_count or 0))
    days_progress = min(1.0, observed_days / max(required_days, 1))
    trades_progress = min(1.0, observed_trades / max(required_trades, 1))
    completion_pct = round(((days_progress + trades_progress) / 2.0) * 100.0, 1)
    days_remaining = max(0, required_days - observed_days)
    trades_remaining = max(0, required_trades - observed_trades)
    trades_per_day = (observed_trades / observed_days) if observed_days > 0 else 0.0
    if days_remaining <= 0 and trades_remaining <= 0:
        eta = "complete"
        status = "complete"
    else:
        eta_days_candidates: List[int] = []
        if days_remaining > 0:
            eta_days_candidates.append(days_remaining)
        if trades_remaining > 0:
            if trades_per_day > 0.0:
                eta_days_candidates.append(max(1, int((trades_remaining / trades_per_day) + 0.999)))
            else:
                eta_days_candidates.append(required_days)
        eta_days = max(eta_days_candidates) if eta_days_candidates else 0
        eta = f"~{eta_days}d left"
        status = "complete" if completion_pct >= 100.0 else ("early" if completion_pct < 50.0 else "maturing")
    roi = _compact_number(realized_roi_pct) if realized_roi_pct is not None else None
    promoted = str(current_stage or "") in {
        PromotionStage.CANARY_READY.value,
        PromotionStage.LIVE_READY.value,
        PromotionStage.APPROVED_LIVE.value,
    }
    if promoted and completion_pct >= 100.0:
        status = "complete"
    return {
        "status": status,
        "completion_pct": completion_pct,
        "paper_days_observed": observed_days,
        "paper_days_required": required_days,
        "days_remaining": days_remaining,
        "trade_count_observed": observed_trades,
        "trade_count_required": required_trades,
        "trades_remaining": trades_remaining,
        "eta": eta,
        "slow_strategy": slow,
        "roi_pct": roi,
    }


def _execution_root() -> Path:
    explicit = str(getattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", "") or "").strip()
    if explicit:
        return Path(explicit)
    execution_repo = str(getattr(config, "EXECUTION_REPO_ROOT", "") or "").strip()
    if execution_repo:
        return Path(execution_repo) / "data" / "portfolios"
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


def _portfolio_dirs() -> List[Path]:
    root = _execution_root()
    if not root.exists():
        return []
    tracked_raw = str(getattr(config, "EXECUTION_TRACKED_PORTFOLIOS", "") or "").strip()
    tracked = [item.strip() for item in tracked_raw.split(",") if item.strip()]
    if tracked:
        dirs = [root / name for name in tracked if (root / name).is_dir()]
        return sorted(dirs)
    return sorted(path for path in root.iterdir() if path.is_dir())


def _tracked_portfolio_ids() -> List[str]:
    tracked_raw = str(getattr(config, "EXECUTION_TRACKED_PORTFOLIOS", "") or "").strip()
    tracked = [item.strip() for item in tracked_raw.split(",") if item.strip()]
    if tracked:
        return tracked
    return [path.name for path in _portfolio_dirs()]


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
        if "scripts/run_portfolio.py" not in line or "--portfolio" not in line:
            continue
        try:
            portfolio = line.split("--portfolio", 1)[1].strip().split()[0].strip()
        except Exception:
            continue
        if portfolio:
            running.add(portfolio)
    return running


def _has_runtime_state(path: Path) -> bool:
    runtime_files = (
        "account.json",
        "heartbeat.json",
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


def _portfolio_snapshot_light(path: Path) -> Dict[str, Any]:
    has_runtime_state = _has_runtime_state(path)
    account = _read_json(path / "account.json", default={}) or {}
    heartbeat = _read_json(path / "heartbeat.json", default={}) or {}
    state = _read_json(path / "state.json", default={}) or {}
    readiness = _read_json(path / "readiness.json", default={}) or {}

    heartbeat_ts = heartbeat.get("ts") or account.get("last_updated") or state.get("last_cycle_at")
    heartbeat_age = _age_seconds(heartbeat_ts)
    running = bool(has_runtime_state and heartbeat_age is not None and heartbeat_age <= 600)
    readiness_status = str(readiness.get("status") or state.get("status") or "").strip().lower()
    issue_codes = list(readiness.get("issue_codes") or state.get("issue_codes") or [])
    blocked = readiness_status in {"blocked", "validation_blocked", "critical"} or "readiness_blocked" in issue_codes
    realized_pnl = _compact_number(
        account.get("realized_pnl")
        if account.get("realized_pnl") is not None
        else state.get("realized_pnl")
    )
    return {
        "portfolio_id": path.name,
        "is_placeholder": not has_runtime_state,
        "running": running,
        "blocked": blocked,
        "realized_pnl": realized_pnl,
        "heartbeat_age_seconds": heartbeat_age,
        "readiness_status": readiness_status or None,
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


def _lineage_portfolio_light(factory_state: Dict[str, Any]) -> Dict[str, Any]:
    tracked_ids = _tracked_portfolio_ids()
    running_ids = _running_portfolio_ids()
    grouped: Dict[str, List[Dict[str, Any]]] = {portfolio_id: [] for portfolio_id in tracked_ids}
    for lineage in list(factory_state.get("lineages") or []):
        portfolio_id = str(lineage.get("curated_target_portfolio_id") or "").strip()
        if portfolio_id in grouped:
            grouped[portfolio_id].append(dict(lineage))

    severity_rank = {"critical": 2, "warning": 1, "healthy": 0}
    portfolios: List[Dict[str, Any]] = []
    placeholder_count = 0
    for portfolio_id in tracked_ids:
        related = grouped.get(portfolio_id) or []
        if not related and portfolio_id not in running_ids:
            placeholder_count += 1
            continue
        worst_health = "healthy"
        issue_codes: List[str] = []
        candidate_families: List[str] = []
        realized_roi_pct = 0.0
        trade_count = 0
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
        running = portfolio_id in running_ids
        display_status = (
            "active"
            if running
            else ("blocked" if worst_health == "critical" else ("degraded" if worst_health == "warning" else "idle"))
        )
        assessment = _assessment_progress(
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
                "currency": "USD",
                "starting_balance": 0.0,
                "current_balance": 0.0,
                "realized_pnl": 0.0,
                "roi_pct": realized_roi_pct,
                "drawdown_pct": 0.0,
                "trade_count": trade_count,
                "paper_days": paper_days,
                "status": "running" if running else "idle",
                "display_status": display_status,
                "running": running,
                "heartbeat_ts": None,
                "heartbeat_age_seconds": None,
                "error": None,
                "readiness_status": worst_health if related else ("running" if running else "unknown"),
                "readiness_score_pct": score_pct,
                "readiness_blockers": issue_codes[:3],
                "candidate_context_count": len(related),
                "live_manifest_count": 0,
                "candidate_families": candidate_families[:4],
                "recent_trades": [],
                "recent_events": [],
                "state_excerpt": {},
                "blocked": worst_health == "critical",
                "has_runtime_state": True,
                "is_placeholder": False,
                "assessment": assessment,
                "execution_health_status": worst_health if related else ("healthy" if running else "warning"),
                "execution_issue_codes": issue_codes[:3],
                "execution_recommendation_context": issue_codes[:1],
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
        "running": bool(state.get("running", heartbeat.get("status") == "running")),
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
            alerts.append(
                {
                    "severity": "warning",
                    "title": f"Factory check failing: {check.get('name')}",
                    "detail": str(check.get("reason") or ""),
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
                }
            )
    for item in list(operator_signals.get("escalation_candidates") or [])[:4]:
        alerts.append(
            {
                "severity": "positive",
                "title": f"Operator review: {item.get('family_id')}",
                "detail": f"{item.get('lineage_id')} is {item.get('current_stage')} with {item.get('roi_pct')}% paper ROI. Review before any real-trading push.",
                "lineage_id": item.get("lineage_id"),
            }
        )
    for item in list(operator_signals.get("positive_models") or [])[:4]:
        alerts.append(
            {
                "severity": "positive",
                "title": f"Positive ROI: {item.get('family_id')}",
                "detail": f"{item.get('lineage_id')} is at {item.get('roi_pct')}% ROI across {item.get('trade_count')} trades.",
                "lineage_id": item.get("lineage_id"),
            }
        )
    for item in list(operator_signals.get("human_action_required") or [])[:4]:
        alerts.append(
            {
                "severity": "critical" if str(item.get("execution_health_status") or "") == "critical" else "warning",
                "title": f"Human action required: {item.get('family_id')}",
                "detail": str(item.get("human_action") or item.get("summary") or item.get("lineage_id") or ""),
                "lineage_id": item.get("lineage_id"),
            }
        )
    for item in list(factory_state.get("lineages") or []):
        if item.get("agent_review_due"):
            alerts.append(
                {
                    "severity": "info",
                    "title": f"Agent review due: {item.get('family_id')}",
                    "detail": f"{item.get('lineage_id')} is due for {item.get('agent_review_due_reason')}.",
                    "lineage_id": item.get("lineage_id"),
                }
            )
    return sorted(alerts, key=_severity_rank)[:12]


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


def _build_agent_run_view(agent_runs: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for run in agent_runs:
        result_payload = dict(run.get("result_payload") or {})
        prompt_payload = dict(run.get("prompt_payload") or {})
        headline = str(
            result_payload.get("title")
            or result_payload.get("summary")
            or result_payload.get("thesis")
            or prompt_payload.get("family", {}).get("label")
            or ""
        ).strip()
        notes = [
            str(item)
            for item in (
                result_payload.get("agent_notes")
                or result_payload.get("next_tests")
                or result_payload.get("recommended_actions")
                or []
            )
            if str(item).strip()
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
                "error": str(run.get("error") or ""),
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
    agent_roles = dict(factory_state.get("agent_roles") or {})
    lineages = list(factory_state.get("lineages") or [])
    recent_text = " | ".join(journal_actions[-12:])
    invocation_counts = _agent_invocations_by_role(agent_runs)
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
        rows: List[Dict[str, Any]] = []
        for member in members:
            member_name = str(member)
            involvement = len(lineages_by_agent.get(member_name, set()))
            recent_mentions = member_name.lower() in recent_text.lower()
            status = "active" if involvement or recent_mentions else "standby"
            rows.append(
                {
                    "name": member_name,
                    "status": status,
                    "lineage_count": involvement,
                    "real_invocation_count": int(invocation_counts.get(member_name, 0)),
                    "families": sorted(families_by_agent.get(member_name, set())),
                    "stages": sorted(stages_by_agent.get(member_name, set())),
                    "recent_mention": recent_mentions,
                }
            )
        desks.append(
            {
                "desk_id": tier,
                "label": _title_case_slug(tier),
                "member_count": len(rows),
                "active_count": sum(1 for row in rows if row["status"] == "active"),
                "members": rows,
            }
        )

    scientist_rows: List[Dict[str, Any]] = []
    for domain in factory_state.get("scientific_researchers") or []:
        label = _title_case_slug(str(domain))
        involvement = sum(1 for lineage in lineages if str(domain) in [str(item) for item in (lineage.get("scientific_domains") or [])])
        scientist_rows.append(
            {
                "name": label,
                "status": "active" if involvement else "standby",
                "lineage_count": involvement,
                "real_invocation_count": 0,
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
            "member_count": len(scientist_rows),
            "active_count": sum(1 for row in scientist_rows if row["status"] == "active"),
            "members": scientist_rows,
        }
    )
    return desks


def _build_family_view(factory_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for family in factory_state.get("families") or []:
        champion = dict(family.get("champion") or {})
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
    for family in factory_state.get("families") or []:
        rankings = []
        for item in list(family.get("curated_rankings") or [])[:3]:
            lineage = lineages_by_id.get(str(item.get("lineage_id") or ""), {})
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
                    "assessment": assessment,
                }
            )
        rows.append(
            {
                "family_id": str(family.get("family_id") or ""),
                "label": str(family.get("label") or ""),
                "rankings": rankings,
            }
        )
    return rows


def _build_operator_signal_view(factory_state: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(factory_state.get("operator_signals") or {})
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
        escalations.append(row)
    return {
        "positive_models": positives,
        "research_positive_models": list(payload.get("research_positive_models") or []),
        "escalation_candidates": escalations,
        "human_action_required": list(payload.get("human_action_required") or []),
        "action_inbox": list(payload.get("action_inbox") or []),
        "maintenance_queue": list(payload.get("maintenance_queue") or []),
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
    for row in sorted_lineages[:24]:
        rows.append(
            {
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
            }
        )
    return rows


def build_dashboard_snapshot() -> Dict[str, Any]:
    factory_state = _read_json(_factory_state_path(), default={}) or {}
    journal = _read_markdown_sections(_factory_journal_path())
    ideas_path = _ideas_path()
    ideas_text = ideas_path.read_text(encoding="utf-8") if ideas_path.exists() else ""
    idea_items = annotate_idea_statuses(all_ideas(_project_root()), list(factory_state.get("lineages") or []))
    idea_buckets = split_active_and_archived_ideas(idea_items)
    agent_run_rows = recent_agent_runs(_project_root(), limit=24)

    portfolios = [_portfolio_snapshot(path) for path in _portfolio_dirs()]
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
    for run in agent_run_rows:
        if run.get("success") is False:
            alerts.append(
                {
                    "severity": "warning",
                    "title": f"Agent fallback: {run.get('task_type')}",
                    "detail": str(run.get("error") or f"{run.get('family_id')} fell back from Codex"),
                }
            )
    alerts = sorted(alerts, key=_severity_rank)[:12]
    research_summary = dict(factory_state.get("research_summary") or {})
    readiness = dict(factory_state.get("readiness") or {})

    return {
        "generated_at": utc_now_iso(),
        "project_root": str(_project_root()),
        "factory": {
            "mode": factory_state.get("agentic_factory_mode"),
            "status": factory_state.get("status"),
            "cycle_count": int(factory_state.get("cycle_count", 0) or 0),
            "readiness": readiness,
            "research_summary": research_summary,
            "families": _build_family_view(factory_state),
            "model_league": _build_model_league_view(factory_state),
            "lineages": _build_lineage_table(factory_state),
            "lineage_atlas": _build_lineage_atlas(factory_state),
            "queue": list(factory_state.get("queue") or []),
            "connectors": list(factory_state.get("connectors") or []),
            "manifests": dict(factory_state.get("manifests") or {}),
            "agent_runs": _build_agent_run_view(agent_run_rows),
            "operator_signals": _build_operator_signal_view(factory_state),
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
            "status_counts": {
                status: sum(1 for item in idea_items if item.get("status") == status)
                for status in ["new", "adapted", "tested", "promoted", "rejected"]
            },
            "items": idea_buckets["active"],
            "archived_items": idea_buckets["archived"],
        },
    }


def build_dashboard_snapshot_light() -> Dict[str, Any]:
    factory_state = _read_json(_factory_state_path(), default={}) or {}
    journal = _read_markdown_sections(_factory_journal_path())
    ideas_path = _ideas_path()
    ideas_text = ideas_path.read_text(encoding="utf-8") if ideas_path.exists() else ""
    idea_items = annotate_idea_statuses(all_ideas(_project_root()), list(factory_state.get("lineages") or []))
    idea_buckets = split_active_and_archived_ideas(idea_items)
    agent_run_rows = recent_agent_runs(_project_root(), limit=24)
    alerts = _build_alerts(factory_state, [])
    for run in agent_run_rows:
        if run.get("success") is False:
            alerts.append(
                {
                    "severity": "warning",
                    "title": f"Agent fallback: {run.get('task_type')}",
                    "detail": str(run.get("error") or f"{run.get('family_id')} fell back from Codex"),
                }
            )
    alerts = sorted(alerts, key=_severity_rank)[:12]
    research_summary = dict(factory_state.get("research_summary") or {})
    readiness = dict(factory_state.get("readiness") or {})
    execution_summary = _lineage_portfolio_light(factory_state)
    return {
        "generated_at": utc_now_iso(),
        "project_root": str(_project_root()),
        "factory": {
            "mode": factory_state.get("agentic_factory_mode"),
            "status": factory_state.get("status"),
            "cycle_count": int(factory_state.get("cycle_count", 0) or 0),
            "readiness": readiness,
            "research_summary": research_summary,
            "families": _build_family_view(factory_state),
            "model_league": _build_model_league_view(factory_state),
            "lineages": _build_lineage_table(factory_state),
            "lineage_atlas": _build_lineage_atlas(factory_state),
            "queue": list(factory_state.get("queue") or []),
            "connectors": list(factory_state.get("connectors") or []),
            "manifests": dict(factory_state.get("manifests") or {}),
            "agent_runs": _build_agent_run_view(agent_run_rows),
            "operator_signals": _build_operator_signal_view(factory_state),
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
            "status_counts": {
                status: sum(1 for item in idea_items if item.get("status") == status)
                for status in ["new", "adapted", "tested", "promoted", "rejected"]
            },
            "items": idea_buckets["active"],
            "archived_items": idea_buckets["archived"],
        },
    }
