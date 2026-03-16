"""CLI entrypoint for embedded portfolio runners: python -m factory.local_runner_main --portfolio <id>."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from factory.local_runner_base import LocalPortfolioRunner

logger = logging.getLogger(__name__)


def get_runner(portfolio_id: str) -> LocalPortfolioRunner:
    """Return the runner implementation for this portfolio.

    Resolution order:
    1. If the lineage genome has model_code_path -> DynamicModelRunner
    2. Legacy family-based routing for existing families (Binance funding)
    3. HMM fallback for stock portfolios
    4. GenericSignalRunner fallback
    """
    try:
        import config
        from factory.family_classifier import family_runtime_venue, load_family_config
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
                
                # Legacy dispatch for funding rate strategies (which use custom runner)
                venues = set(lineage.target_venues)
                if "binance" in venues:
                    from factory.runners.funding_runner import FundingContrarianRunner
                    logger.info("Using FundingContrarianRunner for %s (venue: binance)", portfolio_id)
                    return FundingContrarianRunner(portfolio_id)
                
                # If stocks/Alpaca but no model code, use HMMRegimeModel via DynamicModelRunner
                if venues.intersection({"yahoo_stocks", "yahoo", "alpaca_stocks", "alpaca"}):
                    from factory.runners.dynamic_runner import DynamicModelRunner
                    
                    model_path = project_root / "research/goldfish/hmm_regime_adaptive/model.py"
                    if model_path.exists():
                        logger.info("Using DynamicModelRunner+HMMRegimeModel for %s (venue: stocks, fallback)", portfolio_id)
                        return DynamicModelRunner(
                            portfolio_id,
                            model_code_path=str(model_path),
                            class_name="HMMRegimeModel",
                            runtime_data_source="alpaca" if "alpaca" in venues else "yahoo"
                        )
                break
    except Exception as e:
        logger.warning("Registry-based runner resolution failed for %s: %s", portfolio_id, e)

    # Legacy fallback map
    # Note: We replaced 'hmm_regime' with DynamicModelRunner+HMMRegimeModel above if it hits the registry.
    # This map is only for portfolios NOT in the registry or if registry load fails.
    if portfolio_id in {"alpaca_paper"}:
        from factory.runners.dynamic_runner import DynamicModelRunner
        model_path = Path("research/goldfish/hmm_regime_adaptive/model.py")
        if model_path.exists():
             logger.info("Using DynamicModelRunner+HMMRegimeModel for %s (legacy fallback)", portfolio_id)
             return DynamicModelRunner(
                portfolio_id,
                model_code_path=str(model_path),
                class_name="HMMRegimeModel",
                runtime_data_source="alpaca"
            )

    if portfolio_id in {"contrarian_legacy", "cascade_alpha", "hedge_validation", "hedge_research"}:
        from factory.runners.funding_runner import FundingContrarianRunner
        logger.info("Using FundingContrarianRunner for %s (legacy fallback)", portfolio_id)
        return FundingContrarianRunner(portfolio_id)

    from factory.runners.generic_runner import GenericSignalRunner
    logger.info("Using GenericSignalRunner for %s (final fallback)", portfolio_id)
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
