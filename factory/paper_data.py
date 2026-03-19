from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

import config
from factory.contracts import LineageRecord, StrategyGenome
from factory.registry import FactoryRegistry


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _ensure_utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def cadence_to_seconds(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().lower()
    if not text:
        return 0
    if text.endswith("sec") or text.endswith("secs"):
        return int(float(text.split("sec")[0]))
    if text.endswith("s") and text[:-1].replace(".", "", 1).isdigit():
        return int(float(text[:-1]))
    if text.endswith("m") and text[:-1].replace(".", "", 1).isdigit():
        return int(float(text[:-1]) * 60)
    if text.endswith("min") and text[:-3].replace(".", "", 1).isdigit():
        return int(float(text[:-3]) * 60)
    if text.endswith("h") and text[:-1].replace(".", "", 1).isdigit():
        return int(float(text[:-1]) * 3600)
    if text.endswith("d") and text[:-1].replace(".", "", 1).isdigit():
        return int(float(text[:-1]) * 86400)
    return int(float(text))


def seconds_to_pandas_rule(seconds: int) -> str:
    if seconds <= 0:
        raise ValueError(f"Invalid cadence seconds: {seconds}")
    if seconds % 86400 == 0:
        return f"{seconds // 86400}D"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}H"
    if seconds % 60 == 0:
        return f"{seconds // 60}min"
    return f"{seconds}S"


def _title_interval(seconds: int) -> str:
    if seconds % 86400 == 0:
        return f"{seconds // 86400}Day"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}Hour"
    if seconds % 60 == 0:
        return f"{seconds // 60}Min"
    return f"{seconds}Sec"


@dataclass
class DataRequirement:
    venue: str
    source: str
    instruments: List[str] = field(default_factory=list)
    fields: List[str] = field(default_factory=list)
    feed_type: str = "bars"
    raw_cadence_seconds: int = 0
    required_bar_seconds: int = 0
    freshness_sla_seconds: int = 0
    optional: bool = False
    name: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "venue": self.venue,
            "source": self.source,
            "instruments": list(self.instruments),
            "fields": list(self.fields),
            "feed_type": self.feed_type,
            "raw_cadence_seconds": self.raw_cadence_seconds,
            "required_bar_seconds": self.required_bar_seconds,
            "freshness_sla_seconds": self.freshness_sla_seconds,
            "optional": self.optional,
            "name": self.name,
        }

    def loader_dict(self) -> Dict[str, Any]:
        payload = {
            "source": self.source,
            "venue": self.venue,
            "instruments": list(self.instruments),
            "fields": list(self.fields),
            "feed_type": self.feed_type,
        }
        if self.required_bar_seconds:
            payload["cadence_seconds"] = self.required_bar_seconds
        elif self.raw_cadence_seconds:
            payload["cadence_seconds"] = self.raw_cadence_seconds
        return payload


@dataclass
class PaperDataContract:
    requirements: List[DataRequirement]
    cross_venue_required: bool = False
    aggregation_policy: str = "local_resample_from_1m"
    contract_version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "cross_venue_required": self.cross_venue_required,
            "aggregation_policy": self.aggregation_policy,
            "requirements": [item.to_dict() for item in self.requirements],
        }


@dataclass
class RequirementStatus:
    requirement: DataRequirement
    ready: bool
    message: str
    latest_data_ts: Optional[str] = None
    age_seconds: Optional[float] = None
    available_raw_cadence_seconds: int = 0
    status: str = "missing"
    task_id: Optional[str] = None
    interval_seconds: int = 0
    grace_deadline_at: Optional[str] = None
    within_grace: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requirement": self.requirement.to_dict(),
            "ready": self.ready,
            "message": self.message,
            "reason": self.message,
            "status": self.status,
            "latest_data_ts": self.latest_data_ts,
            "age_seconds": self.age_seconds,
            "available_raw_cadence_seconds": self.available_raw_cadence_seconds,
            "task_id": self.task_id,
            "interval_seconds": self.interval_seconds,
            "grace_deadline_at": self.grace_deadline_at,
            "within_grace": self.within_grace,
        }


@dataclass
class DataReadinessResult:
    ready: bool
    blocking_reason: str
    requirement_statuses: List[RequirementStatus] = field(default_factory=list)
    contract: Optional[PaperDataContract] = None
    status: str = "missing"
    grace_deadline_at: Optional[str] = None
    within_grace: bool = False
    contract_proven: bool = True
    contract_source: Optional[str] = None
    scheduler_running: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ready": self.ready,
            "blocking_reason": self.blocking_reason,
            "status": self.status,
            "grace_deadline_at": self.grace_deadline_at,
            "within_grace": self.within_grace,
            "requirement_statuses": [item.to_dict() for item in self.requirement_statuses],
            "contract": self.contract.to_dict() if self.contract else None,
            "contract_proven": self.contract_proven,
            "contract_source": self.contract_source,
            "scheduler_running": self.scheduler_running,
        }


@dataclass
class RefreshTaskPlan:
    task_id: str
    script: str
    args: List[str]
    interval_seconds: int
    feed_type: str
    source: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "script": self.script,
            "args": list(self.args),
            "interval_seconds": self.interval_seconds,
            "feed_type": self.feed_type,
            "source": self.source,
        }


def _default_venue_for_source(source: str) -> str:
    mapping = {
        "alpaca": "alpaca",
        "yahoo": "yahoo",
        "binance": "binance",
        "polymarket": "polymarket",
        "betfair": "betfair",
    }
    return mapping.get(source, source)


def _default_feed_type(source: str, fields: Iterable[str]) -> str:
    lowered = {str(field).strip().lower() for field in fields}
    if lowered.intersection({"fundingrate", "funding_rate", "markprice", "mark_price"}):
        return "funding"
    if source == "betfair":
        return "market_state"
    if source == "polymarket":
        return "prediction_history"
    return "bars"


def _is_equity_execution_hint(source: str, venue: str, target_venues: Iterable[str] | None) -> bool:
    values = {str(item).strip().lower() for item in (target_venues or []) if str(item).strip()}
    values.update({str(source).strip().lower(), str(venue).strip().lower()})
    return bool(values.intersection({"yahoo", "alpaca", "stock", "equity", "equity_options", "us_equities_etf"}))


def _default_raw_cadence_seconds(source: str, feed_type: str) -> int:
    if feed_type == "funding":
        return 8 * 3600
    if source == "yahoo":
        return 86400
    if source in {"alpaca", "binance", "polymarket"}:
        return 60
    if source == "betfair":
        return 60
    return 300


def _default_freshness_sla_seconds(raw_cadence_seconds: int) -> int:
    if raw_cadence_seconds <= 60:
        return 180
    if raw_cadence_seconds <= 300:
        return 900
    return max(raw_cadence_seconds * 2, 1800)


def _normalize_requirement(raw: Dict[str, Any]) -> DataRequirement:
    source = str(raw.get("source") or raw.get("venue") or "").strip().lower()
    venue = str(raw.get("venue") or _default_venue_for_source(source)).strip().lower()
    fields = [str(item) for item in (raw.get("fields") or []) if str(item).strip()]
    feed_type = str(raw.get("feed_type") or _default_feed_type(source, fields)).strip().lower()
    required_bar_seconds = cadence_to_seconds(raw.get("required_bar_seconds") or raw.get("cadence_seconds") or raw.get("cadence"))
    raw_cadence_seconds = cadence_to_seconds(raw.get("raw_cadence_seconds"))
    if raw_cadence_seconds <= 0:
        raw_cadence_seconds = _default_raw_cadence_seconds(source, feed_type)
    if required_bar_seconds <= 0:
        required_bar_seconds = raw_cadence_seconds
    freshness_sla_seconds = cadence_to_seconds(raw.get("freshness_sla_seconds"))
    if freshness_sla_seconds <= 0:
        freshness_sla_seconds = _default_freshness_sla_seconds(required_bar_seconds)
    return DataRequirement(
        venue=venue,
        source=source,
        instruments=[str(item) for item in (raw.get("instruments") or []) if str(item).strip()],
        fields=fields,
        feed_type=feed_type,
        raw_cadence_seconds=raw_cadence_seconds,
        required_bar_seconds=required_bar_seconds,
        freshness_sla_seconds=freshness_sla_seconds,
        optional=bool(raw.get("optional", False)),
        name=str(raw.get("name") or f"{venue}_{feed_type}").strip(),
    )


def build_paper_data_contract(
    genome_params: Dict[str, Any] | None = None,
    *,
    model_requirement: Dict[str, Any] | None = None,
    target_venues: Iterable[str] | None = None,
) -> PaperDataContract:
    genome_params = dict(genome_params or {})
    explicit = genome_params.get("paper_data_contract")
    if isinstance(explicit, dict):
        requirements = [
            _normalize_requirement(item)
            for item in (explicit.get("requirements") or [])
            if isinstance(item, dict)
        ]
        if requirements:
            return PaperDataContract(
                requirements=requirements,
                cross_venue_required=bool(explicit.get("cross_venue_required", len({r.venue for r in requirements}) > 1)),
                aggregation_policy=str(explicit.get("aggregation_policy") or "local_resample_from_1m"),
                contract_version=int(explicit.get("contract_version") or 1),
            )

    if model_requirement:
        requirement_payload = dict(model_requirement)
        if _is_equity_execution_hint(
            str(requirement_payload.get("source") or ""),
            str(requirement_payload.get("venue") or ""),
            target_venues,
        ):
            requirement_payload["source"] = "alpaca"
            requirement_payload["venue"] = "alpaca"
        requirement = _normalize_requirement(requirement_payload)
        venues = {requirement.venue}
        if target_venues:
            venues.update(str(item).strip().lower() for item in target_venues if str(item).strip())
        return PaperDataContract(
            requirements=[requirement],
            cross_venue_required=len(venues) > 1,
        )

    requirements: List[DataRequirement] = []
    for venue in (target_venues or []):
        clean = str(venue).strip().lower()
        if not clean:
            continue
        if clean.startswith(("yahoo", "stock", "equity")):
            clean = "alpaca"
            source = "alpaca"
        else:
            source = "alpaca" if clean.startswith("alpaca") else clean
        fields = ["fundingRate", "markPrice"] if source == "binance" else ["close"]
        feed_type = "funding" if source == "binance" else "bars"
        requirements.append(
            _normalize_requirement(
                {
                    "source": source,
                    "venue": clean,
                    "fields": fields,
                    "feed_type": feed_type,
                }
            )
        )
    return PaperDataContract(
        requirements=requirements,
        cross_venue_required=len({req.venue for req in requirements}) > 1,
    )


def explicit_current_champion_contract(family_id: str) -> Dict[str, Any] | None:
    normalized = str(family_id or "").strip().lower()
    templates: Dict[str, Dict[str, Any]] = {
        "cross_venue_probability_elasticity": {
            "cross_venue_required": True,
            "requirements": [
                {
                    "source": "polymarket",
                    "venue": "polymarket",
                    "fields": ["price"],
                    "feed_type": "prediction_history",
                    "raw_cadence_seconds": 60,
                    "freshness_sla_seconds": 300,
                    "required_bar_seconds": 60,
                },
                {
                    "source": "binance",
                    "venue": "binance",
                    "fields": ["close"],
                    "feed_type": "bars",
                    "raw_cadence_seconds": 60,
                    "freshness_sla_seconds": 300,
                    "required_bar_seconds": 60,
                },
            ],
        },
        "funding_term_structure_dislocation": {
            "requirements": [
                {
                    "source": "binance",
                    "venue": "binance",
                    "fields": ["funding_rate"],
                    "feed_type": "funding",
                    "raw_cadence_seconds": 28800,
                    "freshness_sla_seconds": 43200,
                    "required_bar_seconds": 28800,
                }
            ]
        },
        "liquidation_rebound_absorption": {
            "requirements": [
                {
                    "source": "binance",
                    "venue": "binance",
                    "fields": ["close"],
                    "feed_type": "bars",
                    "raw_cadence_seconds": 60,
                    "freshness_sla_seconds": 300,
                    "required_bar_seconds": 60,
                }
            ]
        },
        "polymarket_cross_venue": {
            "cross_venue_required": True,
            "requirements": [
                {
                    "source": "polymarket",
                    "venue": "polymarket",
                    "fields": ["price"],
                    "feed_type": "prediction_history",
                    "raw_cadence_seconds": 60,
                    "freshness_sla_seconds": 300,
                    "required_bar_seconds": 60,
                },
                {
                    "source": "betfair",
                    "venue": "betfair",
                    "fields": ["midpoint"],
                    "feed_type": "market_state",
                    "raw_cadence_seconds": 60,
                    "freshness_sla_seconds": 300,
                    "required_bar_seconds": 60,
                },
            ],
        },
        "fam_betfair_inplay_goal_overreaction_v1": {
            "requirements": [
                {
                    "source": "betfair",
                    "venue": "betfair",
                    "fields": ["midpoint"],
                    "feed_type": "market_state",
                    "raw_cadence_seconds": 60,
                    "freshness_sla_seconds": 300,
                    "required_bar_seconds": 60,
                }
            ]
        },
        "polymarket_cross_venue_oil_supply_shock_momentum_v1": {
            "requirements": [
                {
                    "source": "yahoo",
                    "venue": "yahoo",
                    "fields": ["close"],
                    "feed_type": "bars",
                    "raw_cadence_seconds": 86400,
                    "freshness_sla_seconds": 172800,
                    "required_bar_seconds": 86400,
                },
                {
                    "source": "polymarket",
                    "venue": "polymarket",
                    "fields": ["price"],
                    "feed_type": "prediction_history",
                    "raw_cadence_seconds": 60,
                    "freshness_sla_seconds": 300,
                    "required_bar_seconds": 60,
                    "optional": True,
                },
            ],
        },
        "vol_surface_dispersion_rotation": {
            "requirements": [
                {
                    "source": "alpaca",
                    "venue": "alpaca",
                    "fields": ["close"],
                    "feed_type": "bars",
                    "raw_cadence_seconds": 60,
                    "freshness_sla_seconds": 300,
                    "required_bar_seconds": 60,
                },
                {
                    "source": "yahoo",
                    "venue": "yahoo",
                    "fields": ["close"],
                    "feed_type": "bars",
                    "raw_cadence_seconds": 86400,
                    "freshness_sla_seconds": 172800,
                    "required_bar_seconds": 86400,
                },
            ],
        },
    }
    payload = templates.get(normalized)
    if payload is None:
        return None
    return json.loads(json.dumps(payload))


def aggregate_time_bars(df: pd.DataFrame, target_seconds: int) -> pd.DataFrame:
    if target_seconds <= 0:
        return df
    if not isinstance(df.index, pd.DatetimeIndex):
        return df
    rule = seconds_to_pandas_rule(target_seconds)
    lowered = {str(col).lower(): col for col in df.columns}
    price_col = lowered.get("close") or lowered.get("price")
    open_col = lowered.get("open") or price_col
    high_col = lowered.get("high") or price_col
    low_col = lowered.get("low") or price_col
    volume_col = lowered.get("volume")

    if not price_col:
        return df

    agg_map = {}
    if open_col:
        agg_map[open_col] = "first"
    if high_col:
        agg_map[high_col] = "max"
    if low_col:
        agg_map[low_col] = "min"
    agg_map[price_col] = "last"
    if volume_col:
        agg_map[volume_col] = "sum"
    for extra in df.columns:
        if extra not in agg_map and extra != "symbol":
            agg_map[extra] = "last"

    out = df.sort_index().resample(rule, label="right", closed="right").agg(agg_map).dropna(how="all")
    if "symbol" in df.columns:
        out["symbol"] = df["symbol"].dropna().iloc[-1] if not df["symbol"].dropna().empty else ""
    return out


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _latest_ts_for_frame(df: pd.DataFrame) -> datetime | None:
    if isinstance(df.index, pd.DatetimeIndex) and len(df.index) > 0:
        ts = df.index.max()
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return _ensure_utc_datetime(ts.to_pydatetime())
    for col in ("timestamp", "time", "date", "fundingTime", "created_at", "ts"):
        if col in df.columns and not df[col].empty:
            ts_series = pd.to_datetime(df[col], errors="coerce", utc=True).dropna()
            if not ts_series.empty:
                return _ensure_utc_datetime(ts_series.max().to_pydatetime())
    return None


def _alpaca_metadata(project_root: Path) -> Dict[str, Any]:
    return _read_json(project_root / "data" / "alpaca" / "metadata.json")


def _polymarket_metadata(project_root: Path) -> Dict[str, Any]:
    return _read_json(project_root / "data" / "polymarket" / "markets_metadata.json")


def _betfair_market_books_metadata(project_root: Path) -> Dict[str, Any]:
    return _read_json(project_root / "data" / "betfair" / "market_books" / "metadata.json")


def _binance_bars_metadata(project_root: Path) -> Dict[str, Any]:
    return _read_json(project_root / "data" / "binance" / "klines" / "metadata.json")


def _binance_funding_metadata(project_root: Path) -> Dict[str, Any]:
    return _read_json(project_root / "data" / "funding_history" / "funding_rates" / "metadata.json")


def _read_latest_timestamp(path: Path) -> datetime | None:
    if not path.exists():
        return None
    try:
        if path.suffix == ".parquet":
            df = pd.read_parquet(path)
        elif path.suffix == ".csv":
            df = pd.read_csv(path)
        else:
            return _ensure_utc_datetime(datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc))
        return _ensure_utc_datetime(_latest_ts_for_frame(df))
    except Exception:
        return _ensure_utc_datetime(datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc))


def _sample_data_file(base_dir: Path, instruments: List[str]) -> Path | None:
    if instruments:
        for symbol in instruments:
            for ext in (".parquet", ".csv"):
                candidate = base_dir / f"{symbol}{ext}"
                if candidate.exists():
                    return candidate
        return None
    for pattern in ("*.parquet", "*.csv"):
        for candidate in sorted(base_dir.glob(pattern)):
            if candidate.is_file():
                return candidate
    return None


def _scheduler_pid_path(project_root: Path) -> Path:
    return project_root / "data" / "factory" / "data_refresh_scheduler.pid"


def refresh_scheduler_running(project_root: Path) -> bool:
    pid_path = _scheduler_pid_path(project_root)
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    try:
        import os

        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _refresh_state_path(project_root: Path) -> Path:
    return project_root / "data" / "factory" / "data_refresh_state.json"


def load_refresh_state(project_root: Path) -> Dict[str, Dict[str, Any]]:
    path = _refresh_state_path(project_root)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    normalized: Dict[str, Dict[str, Any]] = {}
    for task_id, payload in dict(raw or {}).items():
        if isinstance(payload, dict):
            normalized[str(task_id)] = dict(payload)
            continue
        if isinstance(payload, (int, float)):
            normalized[str(task_id)] = {
                "last_success_at": datetime.fromtimestamp(float(payload), tz=timezone.utc).isoformat()
            }
    return normalized


def refresh_task_for_requirement(requirement: DataRequirement) -> RefreshTaskPlan | None:
    interval_seconds = _interval_for_requirement(requirement)
    if requirement.source == "alpaca":
        return RefreshTaskPlan(
            task_id="alpaca_bars_quotes",
            script="scripts/refresh_alpaca_data.py",
            args=["--days", "2", "--timeframe", "1Min"],
            interval_seconds=interval_seconds,
            feed_type="bars",
            source="alpaca",
        )
    if requirement.source == "polymarket":
        poly_interval = "1m" if requirement.required_bar_seconds <= 60 else "1h"
        return RefreshTaskPlan(
            task_id=f"polymarket_history_{poly_interval}",
            script="scripts/fetch_polymarket_history.py",
            args=["--interval", poly_interval],
            interval_seconds=interval_seconds,
            feed_type="prediction_history",
            source="polymarket",
        )
    if requirement.source == "binance" and requirement.feed_type == "funding":
        return RefreshTaskPlan(
            task_id="binance_funding",
            script="scripts/refresh_binance_funding.py",
            args=[],
            interval_seconds=interval_seconds,
            feed_type="funding",
            source="binance",
        )
    if requirement.source == "binance":
        args = ["--interval", "1m"]
        if requirement.instruments:
            args.extend(["--symbols", ",".join(requirement.instruments)])
        return RefreshTaskPlan(
            task_id="binance_bars_1m",
            script="scripts/refresh_binance_klines.py",
            args=args,
            interval_seconds=interval_seconds,
            feed_type="bars",
            source="binance",
        )
    if requirement.source == "yahoo":
        return RefreshTaskPlan(
            task_id="yahoo_daily",
            script="scripts/refresh_yahoo_data.py",
            args=["--days", "7"],
            interval_seconds=interval_seconds,
            feed_type="bars",
            source="yahoo",
        )
    if requirement.source == "betfair":
        args = ["--max-markets", "40"]
        if requirement.instruments:
            args.extend(["--market-ids", ",".join(requirement.instruments)])
        return RefreshTaskPlan(
            task_id="betfair_market_books",
            script="scripts/refresh_betfair_market_books.py",
            args=args,
            interval_seconds=interval_seconds,
            feed_type=requirement.feed_type,
            source="betfair",
        )
    return None


def _inspect_requirement(requirement: DataRequirement, project_root: Path) -> RequirementStatus:
    now = _utc_now()
    source = requirement.source
    task = refresh_task_for_requirement(requirement)
    task_id = task.task_id if task is not None else None
    interval_seconds = int(task.interval_seconds if task is not None else 0)

    def result(
        ready: bool,
        status: str,
        message: str,
        latest_ts: datetime | None = None,
        age: float | None = None,
        available_raw: int = 0,
    ) -> RequirementStatus:
        grace_deadline_at = None
        within_grace = False
        if status == "stale" and latest_ts is not None and interval_seconds > 0:
            deadline = latest_ts + pd.Timedelta(seconds=requirement.freshness_sla_seconds + interval_seconds)
            grace_deadline_at = deadline.isoformat()
            within_grace = now <= deadline
        return RequirementStatus(
            requirement=requirement,
            ready=ready,
            message=message,
            latest_data_ts=latest_ts.isoformat() if latest_ts is not None else None,
            age_seconds=age,
            available_raw_cadence_seconds=available_raw,
            status=status,
            task_id=task_id,
            interval_seconds=interval_seconds,
            grace_deadline_at=grace_deadline_at,
            within_grace=within_grace,
        )

    if source == "alpaca":
        meta = _alpaca_metadata(project_root)
        timeframe = str(meta.get("timeframe") or "")
        available_raw = cadence_to_seconds(timeframe.replace("Hour", "h").replace("Day", "d").replace("Min", "m")) if timeframe else 0
        if available_raw <= 0:
            available_raw = 60
        bars_dir = project_root / "data" / "alpaca" / "bars"
        missing = [symbol for symbol in requirement.instruments if not any((bars_dir / f"{symbol}{ext}").exists() for ext in (".parquet", ".csv"))]
        if missing:
            return result(False, "missing", f"blocked: Alpaca data missing for {missing[0]}", available_raw=available_raw)
        sample = _sample_data_file(bars_dir, requirement.instruments)
        latest_ts = _ensure_utc_datetime(_read_latest_timestamp(sample) if sample else _parse_iso_ts(meta.get("last_refresh")))
        if latest_ts is None:
            return result(False, "missing", "blocked: Alpaca data has no timestamp", available_raw=available_raw)
        age = (now - latest_ts).total_seconds()
        if available_raw > requirement.raw_cadence_seconds and requirement.raw_cadence_seconds > 0:
            return result(
                False,
                "missing",
                f"blocked: Alpaca only has {_title_interval(available_raw)} bars but model needs {_title_interval(requirement.raw_cadence_seconds)} raw data",
                latest_ts,
                age,
                available_raw,
            )
        if age > requirement.freshness_sla_seconds:
            return result(False, "stale", f"blocked: Alpaca {_title_interval(available_raw)} bars stale", latest_ts, age, available_raw)
        return result(True, "fresh", f"ready: Alpaca {_title_interval(available_raw)} bars fresh", latest_ts, age, available_raw)

    if source == "yahoo":
        available_raw = 86400
        base = project_root / "data" / "yahoo" / "ohlcv"
        sample = _sample_data_file(base, requirement.instruments)
        if sample is None and requirement.instruments:
            return result(False, "missing", f"blocked: Yahoo data missing for {requirement.instruments[0]}", available_raw=available_raw)
        latest_ts = _ensure_utc_datetime(_read_latest_timestamp(sample) if sample else _parse_iso_ts(_read_json(project_root / "data" / "yahoo" / "metadata.json").get("last_refresh")))
        age = (now - latest_ts).total_seconds() if latest_ts else None
        if requirement.raw_cadence_seconds and available_raw > requirement.raw_cadence_seconds:
            return result(False, "missing", "blocked: Yahoo only has daily bars", latest_ts, age, available_raw)
        if latest_ts and age is not None and age > requirement.freshness_sla_seconds:
            return result(False, "stale", "blocked: Yahoo daily bars stale", latest_ts, age, available_raw)
        return result(True, "fresh", "ready: Yahoo bars available", latest_ts, age, available_raw)

    if source == "polymarket":
        meta = _polymarket_metadata(project_root)
        interval = str(meta.get("interval") or "")
        available_raw = cadence_to_seconds(interval) if interval else 3600
        history_dir = project_root / "data" / "polymarket" / "prices_history"
        files = list(history_dir.glob("*.parquet"))
        if not files:
            return result(False, "missing", "blocked: Polymarket history missing", available_raw=available_raw)
        latest_ts = _ensure_utc_datetime(max((_read_latest_timestamp(path) for path in files), default=None))
        age = (now - latest_ts).total_seconds() if latest_ts else None
        if available_raw > requirement.raw_cadence_seconds and requirement.raw_cadence_seconds > 0:
            return result(False, "missing", f"blocked: Polymarket only has {_title_interval(available_raw)} history", latest_ts, age, available_raw)
        if latest_ts and age is not None and age > requirement.freshness_sla_seconds:
            return result(False, "stale", f"blocked: Polymarket {_title_interval(available_raw)} history stale", latest_ts, age, available_raw)
        return result(True, "fresh", f"ready: Polymarket {_title_interval(available_raw)} history fresh", latest_ts, age, available_raw)

    if source == "binance":
        if requirement.feed_type == "funding":
            meta = _binance_funding_metadata(project_root)
            latest_ts = _ensure_utc_datetime(_parse_iso_ts(meta.get("last_refresh")))
            if latest_ts is None:
                return result(False, "missing", "blocked: Binance funding history missing", available_raw=8 * 3600)
            age = (now - latest_ts).total_seconds()
            if age > requirement.freshness_sla_seconds:
                return result(False, "stale", "blocked: Binance funding history stale", latest_ts, age, 8 * 3600)
            return result(True, "fresh", "ready: Binance funding history fresh", latest_ts, age, 8 * 3600)

        meta = _binance_bars_metadata(project_root)
        available_raw = cadence_to_seconds(meta.get("interval")) if meta.get("interval") else 0
        klines_dir = project_root / "data" / "binance" / "klines" / "1m"
        missing = [symbol for symbol in requirement.instruments if not any((klines_dir / f"{symbol}{ext}").exists() for ext in (".parquet", ".csv"))]
        if missing:
            return result(False, "missing", f"blocked: Binance intraday bars missing for {missing[0]}", available_raw=available_raw)
        sample = _sample_data_file(klines_dir, requirement.instruments)
        latest_ts = _ensure_utc_datetime(_read_latest_timestamp(sample) if sample else _parse_iso_ts(meta.get("last_refresh")))
        if latest_ts is None:
            return result(False, "missing", "blocked: Binance intraday bars have no timestamp", available_raw=available_raw)
        age = (now - latest_ts).total_seconds()
        if available_raw <= 0:
            available_raw = 60
        if available_raw > requirement.raw_cadence_seconds and requirement.raw_cadence_seconds > 0:
            return result(False, "missing", f"blocked: Binance only has {_title_interval(available_raw)} bars", latest_ts, age, available_raw)
        if age > requirement.freshness_sla_seconds:
            return result(False, "stale", f"blocked: Binance {_title_interval(available_raw)} bars stale", latest_ts, age, available_raw)
        return result(True, "fresh", f"ready: Binance {_title_interval(available_raw)} bars fresh", latest_ts, age, available_raw)

    if source == "betfair":
        meta = _betfair_market_books_metadata(project_root)
        available_raw = cadence_to_seconds(meta.get("interval")) if meta.get("interval") else 60
        books_dir = project_root / "data" / "betfair" / "market_books"
        files = list(books_dir.glob("*.parquet")) + list(books_dir.glob("*.csv")) + list(books_dir.glob("*.jsonl"))
        if requirement.instruments:
            files = [path for path in files if path.stem in requirement.instruments]
        if not files:
            return result(False, "missing", "blocked: Betfair execution feed missing", available_raw=available_raw)
        latest_ts = _ensure_utc_datetime(max((_read_latest_timestamp(path) for path in files), default=None) or _parse_iso_ts(meta.get("last_refresh")))
        if latest_ts is None:
            return result(False, "missing", "blocked: Betfair execution feed has no timestamp", available_raw=available_raw)
        age = (now - latest_ts).total_seconds()
        if available_raw > requirement.raw_cadence_seconds and requirement.raw_cadence_seconds > 0:
            return result(
                False,
                "missing",
                f"blocked: Betfair only has {_title_interval(available_raw)} snapshots but model needs {_title_interval(requirement.raw_cadence_seconds)} raw data",
                latest_ts,
                age,
                available_raw,
            )
        if age > requirement.freshness_sla_seconds:
            return result(False, "stale", f"blocked: Betfair {_title_interval(available_raw)} execution feed stale", latest_ts, age, available_raw)
        return result(True, "fresh", f"ready: Betfair {_title_interval(available_raw)} execution feed fresh", latest_ts, age, available_raw)

    return result(False, "missing", f"blocked: Unknown data source {source}")


def assess_paper_data_readiness(
    contract: PaperDataContract,
    project_root: Path,
    *,
    contract_proven: bool = True,
    contract_source: str | None = None,
) -> DataReadinessResult:
    scheduler_running = refresh_scheduler_running(project_root)
    if not contract_proven:
        return DataReadinessResult(
            ready=False,
            blocking_reason="blocked: paper data contract unproven",
            requirement_statuses=[],
            contract=contract,
            status="unproven",
            contract_proven=False,
            contract_source=contract_source,
            scheduler_running=scheduler_running,
        )
    statuses = [_inspect_requirement(requirement, project_root) for requirement in contract.requirements]
    failing_required = [status for status in statuses if not status.ready and not status.requirement.optional]
    stale_required = [status for status in failing_required if status.status == "stale"]
    missing_required = [status for status in failing_required if status.status == "missing"]
    if failing_required:
        grace_deadline_at = min(
            (status.grace_deadline_at for status in stale_required if status.grace_deadline_at),
            default=None,
        )
        within_grace = any(status.within_grace for status in stale_required)
        if contract.cross_venue_required:
            reason = "blocked: cross-venue contract has missing or stale required feeds"
        else:
            reason = failing_required[0].message
        return DataReadinessResult(
            ready=False,
            blocking_reason=reason,
            requirement_statuses=statuses,
            contract=contract,
            status="missing" if missing_required else "stale",
            grace_deadline_at=grace_deadline_at,
            within_grace=within_grace,
            contract_proven=contract_proven,
            contract_source=contract_source,
            scheduler_running=scheduler_running,
        )
    return DataReadinessResult(
        ready=True,
        blocking_reason="ready for paper trading",
        requirement_statuses=statuses,
        contract=contract,
        status="fresh",
        contract_proven=contract_proven,
        contract_source=contract_source,
        scheduler_running=scheduler_running,
    )


def _interval_for_requirement(requirement: DataRequirement) -> int:
    venue_min = {
        "alpaca": int(getattr(config, "FACTORY_DATA_MIN_POLL_SECONDS_ALPACA", 60) or 60),
        "binance": int(getattr(config, "FACTORY_DATA_MIN_POLL_SECONDS_BINANCE", 60) or 60),
        "polymarket": int(getattr(config, "FACTORY_DATA_MIN_POLL_SECONDS_POLYMARKET", 60) or 60),
        "yahoo": int(getattr(config, "FACTORY_DATA_MIN_POLL_SECONDS_YAHOO", 900) or 900),
        "betfair": int(getattr(config, "FACTORY_DATA_MIN_POLL_SECONDS_BETFAIR", 60) or 60),
    }.get(requirement.source, 300)

    if requirement.feed_type == "funding":
        feed_target = int(getattr(config, "FACTORY_DATA_FEED_POLL_SECONDS_FUNDING", 300) or 300)
    elif requirement.feed_type == "quotes":
        feed_target = int(getattr(config, "FACTORY_DATA_FEED_POLL_SECONDS_QUOTES", 60) or 60)
    elif requirement.required_bar_seconds <= 60:
        feed_target = int(getattr(config, "FACTORY_DATA_FEED_POLL_SECONDS_BARS_1M", 60) or 60)
    else:
        feed_target = int(getattr(config, "FACTORY_DATA_FEED_POLL_SECONDS_DEFAULT", 300) or 300)
    return max(venue_min, min(feed_target, max(requirement.required_bar_seconds, 60)))


def build_refresh_plan(project_root: Path) -> List[RefreshTaskPlan]:
    factory_root = Path(getattr(config, "FACTORY_ROOT", "data/factory"))
    if not factory_root.is_absolute():
        factory_root = project_root / factory_root
    registry = FactoryRegistry(str(factory_root))
    tasks: Dict[str, RefreshTaskPlan] = {}

    active_stages = {
        "data_check",
        "goldfish_run",
        "walkforward",
        "stress",
        "shadow",
        "paper",
        "canary_ready",
        "live_ready",
        "approved_live",
    }
    for lineage in registry.lineages():
        if not lineage.active or lineage.current_stage not in active_stages:
            continue
        genome = registry.load_genome(lineage.lineage_id)
        if genome is None:
            continue
        contract = build_paper_data_contract(genome.parameters, target_venues=lineage.target_venues)
        for requirement in contract.requirements:
            task = refresh_task_for_requirement(requirement)
            if task is None:
                continue

            existing = tasks.get(task.task_id)
            if existing is None or task.interval_seconds < existing.interval_seconds:
                tasks[task.task_id] = task

    if not tasks:
        return [
            RefreshTaskPlan("yahoo_daily", "scripts/refresh_yahoo_data.py", ["--days", "7"], 900, "bars", "yahoo"),
            RefreshTaskPlan("alpaca_bars_quotes", "scripts/refresh_alpaca_data.py", ["--days", "2", "--timeframe", "1Min"], 300, "bars", "alpaca"),
            RefreshTaskPlan("binance_funding", "scripts/refresh_binance_funding.py", [], 900, "funding", "binance"),
            RefreshTaskPlan("polymarket_history_1h", "scripts/fetch_polymarket_history.py", ["--interval", "1h"], 900, "prediction_history", "polymarket"),
        ]
    return sorted(tasks.values(), key=lambda item: (item.interval_seconds, item.task_id))


def runner_interval_for_portfolio(portfolio_id: str, registry: FactoryRegistry) -> float:
    intervals: List[int] = []
    for lineage in registry.lineages():
        if not lineage.active or portfolio_id not in lineage.target_portfolios:
            continue
        genome = registry.load_genome(lineage.lineage_id)
        contract = build_paper_data_contract(
            genome.parameters if genome else {},
            target_venues=lineage.target_venues,
        )
        for requirement in contract.requirements:
            intervals.append(max(30, min(requirement.required_bar_seconds, requirement.freshness_sla_seconds or requirement.required_bar_seconds or 60)))
    if not intervals:
        return 300.0
    return float(min(intervals))
