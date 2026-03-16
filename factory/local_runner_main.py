"""CLI entrypoint for embedded portfolio runners: python -m factory.local_runner_main --portfolio <id>."""

from __future__ import annotations

import argparse
import logging
import sys

from factory.local_runner_base import LocalPortfolioRunner, StubLocalRunner

logger = logging.getLogger(__name__)


def get_runner(portfolio_id: str) -> LocalPortfolioRunner:
    """Return the runner implementation for this portfolio.

    Resolution order:
    1. If the lineage genome has model_code_path -> DynamicModelRunner
    2. Legacy family-based routing for existing families
    3. GenericSignalRunner fallback
    """
    # Try to resolve from registry (genome-driven)
    try:
        from pathlib import Path

        import config

        from factory.family_classifier import family_runtime_venue, is_equity_family, load_family_config
        from factory.registry import FactoryRegistry

        project_root = Path(__file__).resolve().parent.parent
        factory_root = Path(getattr(config, "FACTORY_ROOT", "data/factory"))
        if not factory_root.is_absolute():
            factory_root = project_root / factory_root

        registry = FactoryRegistry(str(factory_root))

        # Find lineage targeting this portfolio
        for lineage in registry.lineages():
            if not lineage.active:
                continue
            if portfolio_id in lineage.target_portfolios:
                family_cfg = load_family_config(project_root, lineage.family_id)
                runtime_venue = family_runtime_venue(family_cfg) if family_cfg else None

                genome = registry.load_genome(lineage.lineage_id)
                if genome is not None:
                    code_path = str(genome.parameters.get("model_code_path") or "").strip()
                    class_name = str(genome.parameters.get("model_class_name") or "").strip()
                    if code_path and class_name and Path(code_path).exists():
                        from factory.runners.dynamic_runner import DynamicModelRunner

                        runtime_ds = "alpaca" if runtime_venue == "alpaca" else None
                        logger.info(
                            "Using DynamicModelRunner for %s (model: %s, runtime_data_source: %s)",
                            portfolio_id, class_name, runtime_ds or "model-default",
                        )
                        return DynamicModelRunner(
                            portfolio_id,
                            model_code_path=code_path,
                            class_name=class_name,
                            genome_params=dict(genome.parameters),
                            runtime_data_source=runtime_ds,
                        )
                # Check venue for legacy routing
                venues = set(lineage.target_venues)
                if "binance" in venues:
                    from factory.runners.funding_runner import FundingContrarianRunner

                    logger.info("Using FundingContrarianRunner for %s (venue: binance)", portfolio_id)
                    return FundingContrarianRunner(portfolio_id)
                if venues.intersection({"yahoo_stocks", "yahoo", "alpaca_stocks", "alpaca"}):
                    from factory.runners.hmm_runner import HMMRegimeRunner

                    logger.info("Using HMMRegimeRunner for %s (venue: stocks)", portfolio_id)
                    return HMMRegimeRunner(portfolio_id)
                break  # found lineage, use fallback
    except Exception as e:
        logger.warning("Registry-based runner resolution failed for %s: %s", portfolio_id, e)

    # Legacy hardcoded fallback for known portfolios
    _LEGACY_RUNNER_MAP = {
        "alpaca_paper": "hmm_regime",
        "contrarian_legacy": "funding_contrarian",
        "cascade_alpha": "funding_contrarian",
        "hedge_validation": "funding_contrarian",
        "hedge_research": "funding_contrarian",
    }
    runner_type = _LEGACY_RUNNER_MAP.get(portfolio_id, "generic")

    if runner_type == "hmm_regime":
        from factory.runners.hmm_runner import HMMRegimeRunner

        logger.info("Using HMMRegimeRunner for %s (legacy)", portfolio_id)
        return HMMRegimeRunner(portfolio_id)
    if runner_type == "funding_contrarian":
        from factory.runners.funding_runner import FundingContrarianRunner

        logger.info("Using FundingContrarianRunner for %s (legacy)", portfolio_id)
        return FundingContrarianRunner(portfolio_id)

    from factory.runners.generic_runner import GenericSignalRunner

    logger.info("Using GenericSignalRunner for %s (fallback)", portfolio_id)
    return GenericSignalRunner(portfolio_id)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    parser = argparse.ArgumentParser(description="Run embedded paper portfolio runner")
    parser.add_argument("--portfolio", required=True, help="Portfolio ID")
    parser.add_argument("--interval", type=float, default=60.0, help="Cycle interval seconds")
    args = parser.parse_args()
    runner = get_runner(args.portfolio)
    runner.run(cycle_interval_sec=args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
