"""StrategyModel protocol for uniform interaction with trading models."""

from __future__ import annotations

import inspect
from typing import Protocol, TypedDict, runtime_checkable

import pandas as pd

__all__ = [
    "StrategyModel",
    "DataRequirement",
    "ALLOWED_IMPORTS",
    "BANNED_NAMES",
    "VALID_SOURCES",
    "validate_model_instance",
]


@runtime_checkable
class StrategyModel(Protocol):
    """Protocol that all LLM-generated and template trading models must implement."""

    def name(self) -> str:
        """Returns the model's human-readable name."""
        ...

    def configure(self, genome: dict) -> None:
        """Applies genome parameters to the model."""
        ...

    def required_data(self) -> dict:
        """Returns data requirements: source, instruments, fields."""
        ...

    def fit(self, df: pd.DataFrame) -> None:
        """Trains the model on historical data."""
        ...

    def predict(self, df: pd.DataFrame) -> pd.Series:
        """Returns signals: +1 (long), 0 (flat), -1 (short)."""
        ...

    def position_size(self, signal: int, equity: float) -> float:
        """Returns the notional position size."""
        ...


class DataRequirement(TypedDict):
    """Data requirement structure returned by required_data()."""

    source: str
    instruments: list[str]
    fields: list[str]


ALLOWED_IMPORTS = frozenset({
    "numpy", "pandas", "scipy", "sklearn", "hmmlearn", "statsmodels",
    "ta", "math", "statistics", "collections", "dataclasses", "typing",
    "logging", "functools", "itertools", "json", "datetime", "enum", "abc",
})

BANNED_NAMES = frozenset({
    "exec", "eval", "compile", "__import__", "open", "subprocess",
    "os", "sys", "shutil", "pathlib", "socket", "requests",
    "urllib", "http", "importlib", "ctypes", "pickle", "shelve", "glob",
})

VALID_SOURCES = frozenset({
    "yahoo", "binance", "betfair", "polymarket", "alpaca",
})

# Expected: (method_name, param_count_excluding_self)
_SIGNATURES = [
    ("name", 0),
    ("configure", 1),
    ("required_data", 0),
    ("fit", 1),
    ("predict", 1),
    ("position_size", 2),
]


def validate_model_instance(obj: object) -> list[str]:
    """
    Check whether an object satisfies the StrategyModel protocol.

    Verifies all required methods exist and have correct signatures.
    Returns a list of error strings (empty if valid).
    """
    errors: list[str] = []

    for method_name, expected_param_count in _SIGNATURES:
        if not hasattr(obj, method_name):
            errors.append(f"Missing method: {method_name}")
            continue

        attr = getattr(obj, method_name)
        if not callable(attr):
            errors.append(f"{method_name} is not callable")
            continue

        try:
            sig = inspect.signature(attr)
        except (ValueError, TypeError):
            errors.append(f"Cannot inspect signature of {method_name}")
            continue

        params = [p for p in sig.parameters.values() if p.name != "self"]
        if len(params) != expected_param_count:
            errors.append(
                f"{method_name}: expected {expected_param_count} parameter(s), got {len(params)}"
            )

    return errors
