#!/usr/bin/env python3
"""
Bulk Stock Data Downloader for NEBULA
======================================
Downloads 5 years of daily OHLCV data for S&P 500 constituents,
major indices, sector ETFs, VIX, and Treasury yields from Yahoo Finance.

Stores data as individual Parquet files in data/yahoo/ohlcv/{TICKER}.parquet

Usage:
    python scripts/download_stock_data.py [--years 5] [--output-dir data/yahoo]
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Major indices and ETFs to always include
CORE_TICKERS = [
    "SPY", "QQQ", "DIA", "IWM", "VTI", "VOO",
    "XLF", "XLK", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLRE", "XLB", "XLC",
    "GLD", "SLV", "TLT", "HYG", "LQD",
    "ARKK", "SOXX", "SMH", "XBI",
]

VIX_AND_YIELDS = ["^VIX", "^GSPC", "^DJI", "^IXIC", "^TNX", "^FVX", "^TYX"]


def fetch_sp500_tickers() -> list[str]:
    """Fetch current S&P 500 constituents. Tries Wikipedia first, falls back to curated list."""
    try:
        tables = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            attrs={"id": "constituents"},
            header=0,
        )
        sp500_table = tables[0]
        tickers = sp500_table["Symbol"].str.replace(".", "-", regex=False).tolist()
        logger.info("Fetched %d S&P 500 tickers from Wikipedia", len(tickers))
        return tickers
    except Exception as e:
        logger.warning("Wikipedia fetch failed (%s), using curated S&P 500 list", e)
        return _CURATED_SP500


_CURATED_SP500 = [
    "AAPL", "ABBV", "ABT", "ACN", "ADBE", "ADI", "ADM", "ADP", "ADSK", "AEE",
    "AEP", "AES", "AFL", "AIG", "AIZ", "AJG", "AKAM", "ALB", "ALGN", "ALK",
    "ALL", "ALLE", "AMAT", "AMCR", "AMD", "AME", "AMGN", "AMP", "AMT", "AMZN",
    "ANET", "ANSS", "AON", "AOS", "APA", "APD", "APH", "APTV", "ARE", "ATO",
    "ATVI", "AVB", "AVGO", "AVY", "AWK", "AXP", "AZO", "BA", "BAC", "BAX",
    "BBWI", "BBY", "BDX", "BEN", "BF-B", "BIIB", "BIO", "BK", "BKNG", "BKR",
    "BLK", "BMY", "BR", "BRK-B", "BRO", "BSX", "BWA", "BXP", "C", "CAG",
    "CAH", "CARR", "CAT", "CB", "CBOE", "CBRE", "CCI", "CCL", "CDAY", "CDNS",
    "CDW", "CE", "CEG", "CF", "CFG", "CHD", "CHRW", "CHTR", "CI", "CINF",
    "CL", "CLX", "CMA", "CMCSA", "CME", "CMG", "CMI", "CMS", "CNC", "CNP",
    "COF", "COO", "COP", "COST", "CPB", "CPRT", "CPT", "CRL", "CRM", "CSCO",
    "CSGP", "CSX", "CTAS", "CTLT", "CTRA", "CTSH", "CTVA", "CVS", "CVX", "CZR",
    "D", "DAL", "DD", "DE", "DFS", "DG", "DGX", "DHI", "DHR", "DIS",
    "DISH", "DLR", "DLTR", "DOV", "DOW", "DPZ", "DRI", "DTE", "DUK", "DVA",
    "DVN", "DXC", "DXCM", "EA", "EBAY", "ECL", "ED", "EFX", "EIX", "EL",
    "EMN", "EMR", "ENPH", "EOG", "EPAM", "EQIX", "EQR", "EQT", "ES", "ESS",
    "ETN", "ETR", "ETSY", "EVRG", "EW", "EXC", "EXPD", "EXPE", "EXR", "F",
    "FANG", "FAST", "FBHS", "FCX", "FDS", "FDX", "FE", "FFIV", "FIS", "FISV",
    "FITB", "FLT", "FMC", "FOX", "FOXA", "FRC", "FRT", "FTNT", "FTV", "GD",
    "GE", "GILD", "GIS", "GL", "GLW", "GM", "GNRC", "GOOG", "GOOGL", "GPC",
    "GPN", "GRMN", "GS", "GWW", "HAL", "HAS", "HBAN", "HCA", "HD", "HOLX",
    "HON", "HPE", "HPQ", "HRL", "HSIC", "HST", "HSY", "HUM", "HWM", "IBM",
    "ICE", "IDXX", "IEX", "IFF", "ILMN", "INCY", "INTC", "INTU", "INVH", "IP",
    "IPG", "IQV", "IR", "IRM", "ISRG", "IT", "ITW", "IVZ", "J", "JBHT",
    "JCI", "JKHY", "JNJ", "JNPR", "JPM", "K", "KDP", "KEY", "KEYS", "KHC",
    "KIM", "KLAC", "KMB", "KMI", "KMX", "KO", "KR", "L", "LDOS", "LEN",
    "LH", "LHX", "LIN", "LKQ", "LLY", "LMT", "LNC", "LNT", "LOW", "LRCX",
    "LUMN", "LUV", "LVS", "LW", "LYB", "LYV", "MA", "MAA", "MAR", "MAS",
    "MCD", "MCHP", "MCK", "MCO", "MDLZ", "MDT", "MET", "META", "MGM", "MHK",
    "MKC", "MKTX", "MLM", "MMC", "MMM", "MNST", "MO", "MOH", "MOS", "MPC",
    "MPWR", "MRK", "MRNA", "MRO", "MS", "MSCI", "MSFT", "MSI", "MTB", "MTCH",
    "MTD", "MU", "NCLH", "NDAQ", "NDSN", "NEE", "NEM", "NFLX", "NI", "NKE",
    "NOC", "NOW", "NRG", "NSC", "NTAP", "NTRS", "NUE", "NVDA", "NVR", "NWL",
    "NWS", "NWSA", "NXPI", "O", "ODFL", "OGN", "OKE", "OMC", "ON", "ORCL",
    "ORLY", "OTIS", "OXY", "PARA", "PAYC", "PAYX", "PCAR", "PCG", "PEAK", "PEG",
    "PEP", "PFE", "PFG", "PG", "PGR", "PH", "PHM", "PKG", "PKI", "PLD",
    "PM", "PNC", "PNR", "PNW", "POOL", "PPG", "PPL", "PRU", "PSA", "PSX",
    "PTC", "PVH", "PWR", "PXD", "PYPL", "QCOM", "QRVO", "RCL", "RE", "REG",
    "REGN", "RF", "RHI", "RJF", "RL", "RMD", "ROK", "ROL", "ROP", "ROST",
    "RSG", "RTX", "SBAC", "SBNY", "SBUX", "SCHW", "SEE", "SHW", "SIVB", "SJM",
    "SLB", "SNA", "SNPS", "SO", "SPG", "SPGI", "SRE", "STE", "STT", "STX",
    "STZ", "SWK", "SWKS", "SYF", "SYK", "SYY", "T", "TAP", "TDG", "TDY",
    "TECH", "TEL", "TER", "TFC", "TFX", "TGT", "TMO", "TMUS", "TPR", "TRGP",
    "TRMB", "TROW", "TRV", "TSCO", "TSLA", "TSN", "TT", "TTWO", "TXN", "TXT",
    "TYL", "UAL", "UDR", "UHS", "ULTA", "UNH", "UNP", "UPS", "URI", "USB",
    "V", "VFC", "VICI", "VLO", "VMC", "VNO", "VRSK", "VRSN", "VRTX", "VTR",
    "VTRS", "VZ", "WAB", "WAT", "WBA", "WBD", "WDC", "WEC", "WELL", "WFC",
    "WHR", "WM", "WMB", "WMT", "WRB", "WRK", "WST", "WTW", "WY", "WYNN",
    "XEL", "XOM", "XRAY", "XYL", "YUM", "ZBH", "ZBRA", "ZION", "ZTS",
]


def download_ticker(ticker: str, start: str, end: str, output_dir: Path) -> bool:
    """Download OHLCV data for a single ticker and save as Parquet."""
    try:
        import yfinance as yf

        data = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=False)
        if data is None or len(data) == 0:
            logger.warning("No data for %s", ticker)
            return False

        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        safe_name = ticker.replace("^", "_").replace("/", "_")
        out_path = output_dir / f"{safe_name}.parquet"
        data.to_parquet(out_path, engine="pyarrow")
        logger.debug("Saved %s: %d rows -> %s", ticker, len(data), out_path)
        return True
    except Exception as e:
        logger.warning("Failed to download %s: %s", ticker, e)
        return False


def main():
    parser = argparse.ArgumentParser(description="Bulk download stock data for NEBULA")
    parser.add_argument("--years", type=int, default=5, help="Years of history (default: 5)")
    parser.add_argument("--output-dir", type=str, default=None, help="Output directory (default: data/yahoo)")
    parser.add_argument("--skip-sp500", action="store_true", help="Skip S&P 500 constituents, only download core tickers")
    parser.add_argument("--batch-size", type=int, default=20, help="Tickers per batch before sleeping")
    parser.add_argument("--batch-delay", type=float, default=2.0, help="Seconds to sleep between batches")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    output_dir = Path(args.output_dir) if args.output_dir else project_root / "data" / "yahoo" / "ohlcv"
    output_dir.mkdir(parents=True, exist_ok=True)

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=args.years * 365)).strftime("%Y-%m-%d")

    logger.info("Download range: %s to %s (%d years)", start_date, end_date, args.years)

    # Build ticker universe
    tickers = list(set(CORE_TICKERS + VIX_AND_YIELDS))
    if not args.skip_sp500:
        sp500 = fetch_sp500_tickers()
        tickers = list(set(tickers + sp500))

    tickers.sort()
    logger.info("Total tickers to download: %d", len(tickers))

    # Save components list
    meta_dir = output_dir.parent
    meta_dir.mkdir(parents=True, exist_ok=True)

    if not args.skip_sp500:
        sp500_path = meta_dir / "sp500_components.json"
        with open(sp500_path, "w") as f:
            json.dump({"tickers": fetch_sp500_tickers(), "fetched_at": datetime.now().isoformat()}, f, indent=2)
        logger.info("Saved S&P 500 components to %s", sp500_path)

    # Download in batches
    success = 0
    failed = 0
    skipped = 0

    for i, ticker in enumerate(tickers):
        safe_name = ticker.replace("^", "_").replace("/", "_")
        out_path = output_dir / f"{safe_name}.parquet"

        if out_path.exists():
            logger.debug("Skipping %s (already exists)", ticker)
            skipped += 1
            continue

        ok = download_ticker(ticker, start_date, end_date, output_dir)
        if ok:
            success += 1
        else:
            failed += 1

        if (i + 1) % args.batch_size == 0:
            logger.info("Progress: %d/%d (success=%d, failed=%d, skipped=%d)", i + 1, len(tickers), success, failed, skipped)
            time.sleep(args.batch_delay)

    # Save metadata
    metadata = {
        "download_timestamp": datetime.now().isoformat(),
        "start_date": start_date,
        "end_date": end_date,
        "years": args.years,
        "total_tickers": len(tickers),
        "success": success,
        "failed": failed,
        "skipped": skipped,
        "output_dir": str(output_dir),
    }
    meta_path = meta_dir / "metadata.json"
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)

    logger.info("Download complete: %d success, %d failed, %d skipped", success, failed, skipped)
    logger.info("Data stored in: %s", output_dir)
    logger.info("Metadata: %s", meta_path)

    if failed > 0:
        logger.warning("To retry failed tickers, run again (existing files are skipped)")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
