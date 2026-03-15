"""Data loading module that maps model data requirements to actual disk files.

Used by the generic backtest harness and dynamic runner to load data for any venue.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_VENUE_DATA_SUBDIRS: dict[str, str] = {
    "betfair": "candidates",
    "polymarket": os.path.join("polymarket", "prices_history"),
    "binance": os.path.join("funding_history", "funding_rates"),
    "yahoo": os.path.join("yahoo", "ohlcv"),
    "alpaca": os.path.join("alpaca", "bars"),
}


def _has_data_files(path: Path) -> bool:
    """True if *path* is a directory containing at least one file."""
    if not path.is_dir():
        return False
    return any(path.iterdir())


def resolve_data_root(project_root: Path, venue: str) -> Path:
    """Resolve the actual ``data/`` root for *venue*.

    Uses ``project_root/data`` exclusively.  All venue data must
    live locally -- no external repo fallback.
    """
    return project_root / "data"


def load_data_for_requirements(data_req: dict, project_root: Path) -> pd.DataFrame:
    """Load data for the given requirements, returning a concatenated DataFrame.

    Args:
        data_req: Dict with keys: source (str), instruments (list[str]), fields (list[str])
        project_root: Project root directory

    Returns:
        DataFrame with data from the requested source.

    Raises:
        FileNotFoundError: If no data is found for the source.
    """
    source = (data_req.get("source") or "").strip().lower()
    instruments = data_req.get("instruments") or []
    # fields is accepted but we load all available; caller can filter downstream

    if source == "yahoo":
        return _load_yahoo(project_root, instruments)
    if source == "alpaca":
        return _load_alpaca(project_root, instruments)
    if source == "binance":
        return _load_binance(project_root, instruments)
    if source == "betfair":
        return _load_betfair(project_root, instruments)
    if source == "polymarket":
        return _load_polymarket(project_root)

    raise ValueError(f"Unknown source: {source}")


def _load_yahoo(project_root: Path, instruments: list[str]) -> pd.DataFrame:
    base = project_root / "data" / "yahoo" / "ohlcv"
    if not base.exists():
        raise FileNotFoundError(f"No yahoo data directory found at {base}")

    to_load = instruments if instruments else _stems_in_dir(base, (".parquet", ".csv"))
    per_symbol: dict[str, pd.DataFrame] = {}

    for instr in to_load:
        try:
            df = _read_ohlcv_file(base, instr, "yahoo")
            if df is not None and not df.empty:
                if "symbol" in df.columns:
                    df = df.drop(columns=["symbol"])
                per_symbol[instr] = df
        except Exception as e:
            logger.warning("Skipping yahoo instrument %s: %s", instr, e)

    if not per_symbol:
        raise FileNotFoundError(
            f"No yahoo data loaded from {base}. Requested instruments: {instruments or 'all'}."
        )

    if len(per_symbol) == 1:
        sym, df = next(iter(per_symbol.items()))
        df["symbol"] = sym
        return _sort_by_datetime(df)

    # Multiple instruments: build MultiIndex columns (symbol, field)
    combined = pd.concat(per_symbol, axis=1)  # level-0 = symbol, level-1 = field
    combined = combined.sort_index()
    combined = combined.ffill()
    return combined


def _load_alpaca(project_root: Path, instruments: list[str]) -> pd.DataFrame:
    base = project_root / "data" / "alpaca" / "bars"
    if not base.exists():
        raise FileNotFoundError(f"No alpaca data directory found at {base}")

    dfs: list[pd.DataFrame] = []
    to_load = instruments if instruments else _stems_in_dir(base, (".parquet", ".csv"))

    for instr in to_load:
        try:
            df = _read_ohlcv_file(base, instr, "alpaca")
            if df is not None:
                df["symbol"] = instr
                dfs.append(df)
        except Exception as e:
            logger.warning("Skipping alpaca instrument %s: %s", instr, e)

    if not dfs:
        raise FileNotFoundError(
            f"No alpaca data loaded from {base}. Requested instruments: {instruments or 'all'}."
        )

    out = pd.concat(dfs)
    return _sort_by_datetime(out)


def _read_ohlcv_file(base: Path, stem: str, source: str) -> pd.DataFrame | None:
    """Try parquet then csv. Returns None if neither exists."""
    for ext in (".parquet", ".csv"):
        p = base / f"{stem}{ext}"
        if not p.exists():
            continue
        try:
            if ext == ".parquet":
                df = pd.read_parquet(p)
            else:
                df = pd.read_csv(p)
            if pd.api.types.is_datetime64_any_dtype(df.index):
                return df
            for dt_col in ("Date", "date", "timestamp", "time"):
                if dt_col in df.columns:
                    df[dt_col] = pd.to_datetime(df[dt_col], errors="coerce")
                    df = df.dropna(subset=[dt_col])
                    df = df.set_index(dt_col)
                    return df
            return df
        except Exception as e:
            logger.warning("Failed to load %s for %s: %s", p, source, e)
    return None


def _load_binance(project_root: Path, instruments: list[str]) -> pd.DataFrame:
    base = project_root / "data" / "funding_history" / "funding_rates"
    if not base.exists():
        raise FileNotFoundError(f"No binance funding directory found at {base}")

    dfs: list[pd.DataFrame] = []
    for p in base.glob("*.csv"):
        try:
            df = pd.read_csv(p)
            if "symbol" in df.columns and instruments:
                df = df[df["symbol"].isin(instruments)]
            if not df.empty:
                dfs.append(df)
        except Exception as e:
            logger.warning("Skipping binance file %s: %s", p, e)

    if not dfs:
        raise FileNotFoundError(
            f"No binance funding data loaded from {base}. "
            f"Instruments filter: {instruments or 'none'}."
        )

    out = pd.concat(dfs, ignore_index=True)
    col_map = {
        "funding_rate": "fundingRate",
        "mark_price": "markPrice",
        "funding_time": "fundingTime",
    }
    out.rename(columns={k: v for k, v in col_map.items() if k in out.columns}, inplace=True)

    if "fundingTime" in out.columns:
        out["fundingTime"] = pd.to_datetime(out["fundingTime"], unit="ms", errors="coerce")
        out = out.dropna(subset=["fundingTime"])
        out = out.sort_values("fundingTime").reset_index(drop=True)
    return out


def _load_betfair(
    project_root: Path,
    instruments: list[str],
    *,
    max_files: int = 3,
    max_rows_per_file: int = 20_000,
) -> pd.DataFrame:
    data_root = resolve_data_root(project_root, "betfair")
    base = data_root / "candidates"
    if not base.exists():
        raise FileNotFoundError(f"No betfair candidates directory found at {base}")

    all_files = sorted(base.glob("*.jsonl"))
    if instruments:
        all_files = [p for p in all_files if p.stem in instruments]
    all_files = all_files[-max_files:]

    rows: list[dict] = []
    for p in all_files:
        file_rows = 0
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    if file_rows >= max_rows_per_file:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                        file_rows += 1
                    except json.JSONDecodeError as e:
                        logger.warning("Invalid JSON line in %s: %s", p, e)
        except Exception as e:
            logger.warning("Skipping betfair file %s: %s", p, e)

    if not rows:
        raise FileNotFoundError(
            f"No betfair data loaded from {base}. Files/instruments: {instruments or 'all'}."
        )

    out = pd.DataFrame(rows)
    # Try to set datetime index if present
    for col in ("date", "timestamp", "time", "created_at", "ts"):
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce")
            out = out.dropna(subset=[col])
            out = out.set_index(col)
            break
    return _sort_by_datetime(out)


def _load_polymarket(project_root: Path) -> pd.DataFrame:
    data_root = resolve_data_root(project_root, "polymarket")
    base = data_root / "polymarket" / "prices_history"
    if not base.exists():
        raise FileNotFoundError(f"No polymarket prices directory found at {base}")

    dfs: list[pd.DataFrame] = []
    for p in base.glob("*.parquet"):
        try:
            dfs.append(pd.read_parquet(p))
        except Exception as e:
            logger.warning("Skipping polymarket file %s: %s", p, e)

    if not dfs:
        raise FileNotFoundError(f"No polymarket data loaded from {base}.")

    out = pd.concat(dfs, ignore_index=True)
    return _sort_by_datetime(out)


def _stems_in_dir(base: Path, exts: tuple[str, ...]) -> list[str]:
    stems: set[str] = set()
    for ext in exts:
        for p in base.glob(f"*{ext}"):
            stems.add(p.stem)
    return sorted(stems)


def _sort_by_datetime(df: pd.DataFrame) -> pd.DataFrame:
    if df.index.name and pd.api.types.is_datetime64_any_dtype(df.index):
        return df.sort_index()
    for col in ("date", "timestamp", "time", "Date", "fundingTime", "created_at", "ts"):
        if col in df.columns:
            df = df.copy()
            df[col] = pd.to_datetime(df[col], errors="coerce")
            df = df.dropna(subset=[col]).sort_values(col)
            break
    return df


def available_instruments(source: str, project_root: Path) -> list[str]:
    """Return the list of available instruments for a given source."""
    source = (source or "").strip().lower()

    if source == "yahoo":
        base = project_root / "data" / "yahoo" / "ohlcv"
        return _stems_in_dir(base, (".parquet", ".csv")) if base.exists() else []

    if source == "alpaca":
        base = project_root / "data" / "alpaca" / "bars"
        return _stems_in_dir(base, (".parquet", ".csv")) if base.exists() else []

    if source == "binance":
        base = project_root / "data" / "funding_history" / "funding_rates"
        if not base.exists():
            return []
        symbols: set[str] = set()
        for p in base.glob("*.csv"):
            try:
                df = pd.read_csv(p)
                if "symbol" in df.columns:
                    symbols.update(df["symbol"].dropna().astype(str))
            except Exception as e:
                logger.warning("Skipping binance file %s: %s", p, e)
        return sorted(symbols)

    if source == "betfair":
        data_root = resolve_data_root(project_root, "betfair")
        base = data_root / "candidates"
        return _stems_in_dir(base, (".jsonl",)) if base.exists() else []

    if source == "polymarket":
        data_root = resolve_data_root(project_root, "polymarket")
        base = data_root / "polymarket" / "prices_history"
        return _stems_in_dir(base, (".parquet",)) if base.exists() else []

    return []


def venue_for_source(source: str) -> str:
    """Map source to venue name."""
    mapping = {
        "yahoo": "yahoo_stocks",
        "alpaca": "alpaca_stocks",
        "binance": "binance",
        "betfair": "betfair",
        "polymarket": "polymarket",
    }
    return mapping.get((source or "").strip().lower(), source or "unknown")


def cycle_interval_for_source(source: str) -> float:
    """Return the default cycle interval in seconds."""
    mapping = {
        "yahoo": 3600.0,
        "alpaca": 3600.0,
        "binance": 28800.0,
        "betfair": 300.0,
        "polymarket": 300.0,
    }
    return mapping.get((source or "").strip().lower(), 3600.0)
