#!/usr/bin/env python3
"""Optimize all champion families with available data using Optuna TPE.

Runs locally (TASK_LOCAL) -- no LLM tokens consumed.
Iterates families, discovers data, runs optimization, writes results.

Usage:
    python3 scripts/optimize_all_champions.py
    python3 scripts/optimize_all_champions.py --n-trials 100
    python3 scripts/optimize_all_champions.py --families hmm_regime_adaptive,binance_funding_contrarian
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = PROJECT_ROOT / "data" / "backtest_results"
YAHOO_DIR = PROJECT_ROOT / "data" / "yahoo" / "ohlcv"
KLINES_DIR = PROJECT_ROOT / "data" / "funding_history" / "klines"

FAMILY_CONFIG = {
    "hmm_regime_adaptive": {
        "data_source": "yahoo",
        "default_tickers": "SPY,QQQ,AAPL,MSFT,AMZN,GOOGL,META,NVDA,TSLA,JPM",
        "description": "HMM regime detection on stocks (Yahoo OHLCV)",
    },
    "binance_funding_contrarian": {
        "data_source": "binance_klines",
        "default_tickers": "auto",
        "description": "Funding-rate contrarian on crypto (Binance klines)",
    },
    "binance_cascade_regime": {
        "data_source": "binance_klines",
        "default_tickers": "auto",
        "description": "Cascade regime on crypto (Binance klines)",
    },
}


def _has_results(family_id: str) -> bool:
    """Check if optimization results already exist for a family."""
    family_dir = RESULTS_DIR / family_id
    if not family_dir.exists():
        return False
    optuna_files = list(family_dir.glob("*_optuna_results.json"))
    return len(optuna_files) > 0


def _has_data(family_id: str) -> bool:
    """Check if historical data exists for a family."""
    cfg = FAMILY_CONFIG.get(family_id, {})
    source = cfg.get("data_source", "")
    if source == "yahoo":
        return YAHOO_DIR.exists() and len(list(YAHOO_DIR.glob("*.parquet"))) > 10
    if source == "binance_klines":
        return KLINES_DIR.exists() and len(list(KLINES_DIR.glob("*.csv"))) > 5
    return False


def _get_top_tickers(family_id: str, max_tickers: int = 10) -> str:
    cfg = FAMILY_CONFIG.get(family_id, {})
    if cfg.get("default_tickers") == "auto":
        if cfg.get("data_source") == "binance_klines":
            csvs = sorted(glob.glob(str(KLINES_DIR / "*.csv")))
            symbols = [Path(f).stem for f in csvs[:max_tickers]]
            return ",".join(symbols)
    return cfg.get("default_tickers", "SPY,QQQ")


def _run_optimization(family_id: str, n_trials: int, force: bool) -> dict:
    """Run batch_backtest.py --optimize for a given family."""
    if not force and _has_results(family_id):
        logger.info("[%s] Already has results, skipping (use --force to re-run)", family_id)
        return {"family": family_id, "status": "skipped", "reason": "results_exist"}

    if not _has_data(family_id):
        logger.warning("[%s] No data available, skipping", family_id)
        return {"family": family_id, "status": "skipped", "reason": "no_data"}

    tickers = _get_top_tickers(family_id)
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "batch_backtest.py"),
        "--optimize",
        "--family", family_id,
        "--tickers", tickers,
        "--n-trials", str(n_trials),
    ]

    logger.info("[%s] Running: %s", family_id, " ".join(cmd[-6:]))
    start = time.time()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,
            cwd=str(PROJECT_ROOT),
        )
        elapsed = time.time() - start
        success = result.returncode == 0

        if not success:
            logger.error("[%s] Failed (exit %d) in %.1fs", family_id, result.returncode, elapsed)
            logger.error("[%s] stderr: %s", family_id, result.stderr[-500:] if result.stderr else "")
        else:
            logger.info("[%s] Completed in %.1fs", family_id, elapsed)

        return {
            "family": family_id,
            "status": "success" if success else "failed",
            "elapsed_seconds": round(elapsed, 1),
            "tickers": tickers,
            "n_trials": n_trials,
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        logger.error("[%s] Timed out after 1 hour", family_id)
        return {"family": family_id, "status": "timeout"}
    except Exception as e:
        logger.error("[%s] Error: %s", family_id, e)
        return {"family": family_id, "status": "error", "error": str(e)}


def _load_best_results(family_id: str) -> list[dict]:
    """Load best optimization results for a family."""
    family_dir = RESULTS_DIR / family_id
    if not family_dir.exists():
        return []
    results = []
    for fp in sorted(family_dir.glob("*_optuna_results.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            best = data.get("best_metrics", {})
            results.append({
                "ticker": fp.stem.replace("_optuna_results", ""),
                "roi_pct": best.get("total_return_pct", best.get("total_return", 0)),
                "sharpe": best.get("sharpe", 0),
                "params": data.get("best_params", {}),
            })
        except Exception:
            continue
    return results


def main():
    parser = argparse.ArgumentParser(description="Optimize all champion families")
    parser.add_argument("--n-trials", type=int, default=50, help="Optuna trials per ticker (default: 50)")
    parser.add_argument("--families", default=None, help="Comma-separated family IDs (default: all with data)")
    parser.add_argument("--force", action="store_true", help="Re-run even if results exist")
    args = parser.parse_args()

    if args.families:
        families = [f.strip() for f in args.families.split(",")]
    else:
        families = list(FAMILY_CONFIG.keys())

    logger.info("Optimizing %d families: %s", len(families), families)

    run_results = []
    for fam in families:
        if fam not in FAMILY_CONFIG:
            logger.warning("Unknown family %s, skipping", fam)
            continue
        result = _run_optimization(fam, n_trials=args.n_trials, force=args.force)
        run_results.append(result)

    print("\n" + "=" * 80)
    print("OPTIMIZATION SUMMARY")
    print("=" * 80)

    for r in run_results:
        fam = r["family"]
        status = r["status"]
        elapsed = r.get("elapsed_seconds", "N/A")
        print(f"  {fam:35} | {status:10} | {elapsed}s")

        if status == "success":
            best = _load_best_results(fam)
            positive = [b for b in best if (b.get("roi_pct") or 0) > 0]
            print(f"    -> {len(best)} tickers optimized, {len(positive)} with positive ROI")
            for b in sorted(best, key=lambda x: x.get("roi_pct", 0), reverse=True)[:3]:
                roi = b.get("roi_pct", 0)
                sharpe = b.get("sharpe", 0)
                print(f"       {b['ticker']:12} ROI: {roi:+.1f}%  Sharpe: {sharpe:.2f}")

    print("=" * 80)

    summary_path = RESULTS_DIR / "optimization_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "runs": run_results,
    }
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    logger.info("Summary written to %s", summary_path)


if __name__ == "__main__":
    main()
