"""Sandbox module for validating and loading LLM-generated model code."""

from __future__ import annotations

import ast
import importlib.util
import logging
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from factory.model_protocol import (
    ALLOWED_IMPORTS,
    BANNED_NAMES,
    VALID_SOURCES,
    validate_model_instance,
)

logger = logging.getLogger(__name__)


def validate_code(source_code: str) -> list[str]:
    """Parse source code, run AST-based security checks, return list of errors."""
    errors: list[str] = []

    try:
        tree = ast.parse(source_code)
    except SyntaxError as e:
        return [f"Syntax error: {e}"]

    # Check for __import__ as string anywhere in source
    if "__import__" in source_code:
        errors.append("__import__ is banned in model source")

    # AST visitor for imports and calls
    class SecurityVisitor(ast.NodeVisitor):
        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                top_module = alias.name.split(".")[0]
                if top_module not in ALLOWED_IMPORTS:
                    errors.append(f"Disallowed import: {top_module}")
            self.generic_visit(node)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if node.module is None:
                return
            top_module = node.module.split(".")[0]
            if top_module not in ALLOWED_IMPORTS:
                errors.append(f"Disallowed import from: {top_module}")
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            name: str | None = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name and name in BANNED_NAMES:
                errors.append(f"Banned call: {name}")
            self.generic_visit(node)

    visitor = SecurityVisitor()
    try:
        visitor.visit(tree)
    except Exception as e:
        logger.exception("AST visit failed")
        errors.append(f"AST validation error: {e}")

    return errors


def load_model_from_code(code_path: str | Path, class_name: str) -> Any:
    """Load a model class from a Python file, validate and instantiate it."""
    path = Path(code_path)
    if not path.exists():
        raise ValueError(f"File not found: {path}")

    try:
        source = path.read_text()
    except Exception as e:
        raise ValueError(f"Failed to read {path}: {e}") from e

    val_errors = validate_code(source)
    if val_errors:
        raise ValueError("Code validation failed:\n" + "\n".join(val_errors))

    try:
        module_name = f"model_{path.stem}_{uuid.uuid4().hex[:8]}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Cannot create spec for {path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        cls = getattr(module, class_name, None)
        if cls is None:
            raise ValueError(f"Class '{class_name}' not found in {path}")

        instance = cls()

        inst_errors = validate_model_instance(instance)
        if inst_errors:
            raise ValueError("Model instance validation failed:\n" + "\n".join(inst_errors))

        return instance
    except ValueError:
        raise
    except Exception as e:
        logger.exception("load_model_from_code failed")
        raise ValueError(f"Failed to load model: {e}") from e


def _make_synthetic_ohlcv(rows: int) -> pd.DataFrame:
    """Create a synthetic OHLCV DataFrame for protocol testing."""
    np.random.seed(42)
    close = 100 * np.exp(np.cumsum(0.001 * np.random.standard_normal(rows)))
    close = np.maximum(close, 1.0)
    high = close * (1 + 0.01 * np.random.random(rows))
    low = close * (1 - 0.01 * np.random.random(rows))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    volume = np.random.randint(1000, 100000, rows).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}
    )


def test_model_protocol(model: Any, sample_rows: int = 50) -> list[str]:
    """Test a model instance against the StrategyModel protocol."""
    errors: list[str] = []

    try:
        df = _make_synthetic_ohlcv(sample_rows)
    except Exception as e:
        return [f"Failed to create synthetic data: {e}"]

    try:
        model.configure({})
    except Exception as e:
        errors.append(f"configure() raised: {e}")
        return errors

    try:
        rd = model.required_data()
        if not isinstance(rd, dict):
            errors.append("required_data() must return dict")
        elif "source" not in rd:
            errors.append("required_data() must include 'source'")
        elif rd.get("source") not in VALID_SOURCES:
            errors.append(
                f"required_data() source must be in {sorted(VALID_SOURCES)}, got {rd.get('source')!r}"
            )
    except Exception as e:
        errors.append(f"required_data() raised: {e}")
        return errors

    try:
        model.fit(df)
    except Exception as e:
        errors.append(f"fit() raised: {e}")
        return errors

    try:
        pred = model.predict(df)
        if not isinstance(pred, pd.Series):
            errors.append(f"predict() must return pd.Series, got {type(pred)}")
        elif len(pred) != len(df):
            errors.append(f"predict() length {len(pred)} != df length {len(df)}")
        else:
            invalid = pred[~pred.isin([-1, 0, 1])]
            if len(invalid) > 0:
                errors.append(f"predict() contains invalid values: {invalid.unique().tolist()[:5]}")
    except Exception as e:
        errors.append(f"predict() raised: {e}")
        return errors

    try:
        size = model.position_size(1, 10000.0)
        if not isinstance(size, (int, float)):
            errors.append(f"position_size() must return float, got {type(size)}")
        elif size < 0:
            errors.append(f"position_size() returned negative: {size}")
    except Exception as e:
        errors.append(f"position_size() raised: {e}")

    return errors


def sandbox_load_and_test(
    code_path: str | Path, class_name: str
) -> tuple[Any | None, list[str]]:
    """Load a model from code and run protocol tests. Returns (model, errors)."""
    try:
        model = load_model_from_code(code_path, class_name)
    except Exception as e:
        logger.debug("load_model_from_code failed: %s", e)
        return (None, [str(e)])

    try:
        errors = test_model_protocol(model)
        if errors:
            return (model, errors)
        return (model, [])
    except Exception as e:
        logger.exception("test_model_protocol failed")
        return (None, [str(e)])
