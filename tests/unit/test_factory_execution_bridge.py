from __future__ import annotations

from typing import Dict

import config
from factory.execution_bridge import FactoryExecutionBridge
from factory.runtime_lanes import decide_runtime_lane_policy
from factory.execution_manager import RuntimePortfolioSpec


class _DummyManager:
    def __init__(self) -> None:
        self.running: Dict[str, int] = {}
        self.starts: list[str] = []
        self.status_overrides: Dict[str, Dict[str, object]] = {}

    def status(self, portfolio_id: str):
        if portfolio_id in self.status_overrides:
            return dict(self.status_overrides[portfolio_id])
        pid = self.running.get(portfolio_id)
        return {
            "running": pid is not None,
            "pid": pid,
            "heartbeat": None,
            "runtime_status": "running" if pid is not None else "",
            "publish_status": "publishing" if pid is not None else "",
            "health_status": "healthy" if pid is not None else "",
            "issue_codes": [],
        }

    def start(self, portfolio_id: str):
        self.starts.append(portfolio_id)
        self.running[portfolio_id] = len(self.starts) + 1000
        return {"ok": True, "pid": self.running[portfolio_id]}


def test_runtime_lane_policy_prefers_challenger_when_isolated_lane_is_in_progress():
    prefer_challenger, reason = decide_runtime_lane_policy(
        [
            {
                "lineage_id": "family:champion",
                "role": "champion",
                "current_stage": "paper",
                "strict_gate_pass": True,
                "execution_health_status": "warning",
                "iteration_status": "champion",
                "fitness_score": 7.0,
            },
            {
                "lineage_id": "family:challenger:1",
                "role": "paper_challenger",
                "current_stage": "shadow",
                "strict_gate_pass": True,
                "execution_health_status": "warning",
                "iteration_status": "prepare_isolated_lane",
                "fitness_score": 5.0,
            },
        ]
    )

    assert prefer_challenger is True
    assert reason == "isolated_lane_progress"


def test_runtime_lane_policy_prefers_challenger_for_first_paper_read_of_positive_candidate(monkeypatch):
    monkeypatch.setattr(config, "FACTORY_RUNTIME_FIRST_READ_MIN_ROI_PCT", 1.0)
    monkeypatch.setattr(config, "FACTORY_RUNTIME_FIRST_READ_MIN_RESEARCH_TRADES", 25)
    prefer_challenger, reason = decide_runtime_lane_policy(
        [
            {
                "lineage_id": "family:champion",
                "role": "champion",
                "current_stage": "paper",
                "strict_gate_pass": True,
                "execution_health_status": "healthy",
                "iteration_status": "champion",
                "fitness_score": 7.0,
                "live_paper_trade_count": 40,
                "live_paper_days": 10,
            },
            {
                "lineage_id": "family:challenger:1",
                "role": "paper_challenger",
                "current_stage": "shadow",
                "strict_gate_pass": False,
                "execution_health_status": "warning",
                "iteration_status": "new_candidate",
                "fitness_score": 5.0,
                "monthly_roi_pct": 3.5,
                "trade_count": 40,
                "live_paper_trade_count": 0,
                "live_paper_days": 0,
            },
        ]
    )

    assert prefer_challenger is True
    assert reason == "paper_qualification_needed"


def test_runtime_lane_policy_prefers_challenger_when_incumbent_trade_is_stalled(monkeypatch):
    monkeypatch.setattr(config, "FACTORY_RUNTIME_FIRST_READ_MIN_ROI_PCT", 1.0)
    monkeypatch.setattr(config, "FACTORY_RUNTIME_FIRST_READ_MIN_RESEARCH_TRADES", 25)
    prefer_challenger, reason = decide_runtime_lane_policy(
        [
            {
                "lineage_id": "family:champion",
                "role": "champion",
                "current_stage": "paper",
                "strict_gate_pass": True,
                "execution_health_status": "warning",
                "execution_issue_codes": ["trade_stalled", "stalled_model"],
                "iteration_status": "champion",
                "fitness_score": 7.0,
                "live_paper_trade_count": 7,
                "live_paper_days": 2,
            },
            {
                "lineage_id": "family:challenger:1",
                "role": "paper_challenger",
                "current_stage": "shadow",
                "strict_gate_pass": False,
                "execution_health_status": "warning",
                "iteration_status": "new_candidate",
                "fitness_score": 5.0,
                "monthly_roi_pct": 2.2,
                "trade_count": 31,
                "live_paper_trade_count": 0,
                "live_paper_days": 0,
            },
        ]
    )

    assert prefer_challenger is True
    assert reason == "incumbent_trade_stalled"


def test_execution_bridge_starts_enabled_targets_and_skips_disabled(monkeypatch):
    manager = _DummyManager()
    monkeypatch.setattr(config, "FACTORY_EXECUTION_AUTOSTART_ENABLED", True)
    monkeypatch.setattr(config, "AGENTIC_FACTORY_MODE", "full")

    specs = {
        "betfair_core": RuntimePortfolioSpec(
            portfolio_id="betfair_core",
            label="Betfair Core",
            control_mode="local_managed",
            enabled=True,
        ),
        "cascade_alpha": RuntimePortfolioSpec(
            portfolio_id="cascade_alpha",
            label="Cascade Alpha",
            control_mode="local_managed",
            enabled=False,
        ),
    }

    def _fake_get_spec(portfolio_id: str):
        if portfolio_id not in specs:
            raise KeyError(portfolio_id)
        return specs[portfolio_id]

    monkeypatch.setattr("factory.execution_bridge.get_runtime_portfolio_spec", _fake_get_spec)

    bridge = FactoryExecutionBridge(process_manager=manager)
    state = {
        "lineages": [
            {
                "lineage_id": "betfair_prediction_value_league:challenger:1",
                "family_id": "betfair_prediction_value_league",
                "active": True,
                "current_stage": "paper",
                "role": "paper_challenger",
                "target_portfolios": ["betfair_core", "betfair_execution_book"],
            },
            {
                "lineage_id": "binance_cascade_regime:challenger:1",
                "family_id": "binance_cascade_regime",
                "active": True,
                "current_stage": "shadow",
                "role": "shadow_challenger",
                "target_portfolios": ["cascade_alpha"],
            },
        ]
    }

    payload = bridge.sync(state)

    assert payload["desired_portfolio_count"] == 2
    assert "betfair_core" in manager.starts
    statuses = {item["portfolio_id"]: item for item in payload["targets"]}
    assert statuses["betfair_core"]["status"] == "started"
    assert statuses["betfair_core"]["running"] is True
    assert statuses["betfair_core"]["requested_targets"] == ["betfair_core", "betfair_execution_book"]
    assert statuses["cascade_alpha"]["status"] == "runner_disabled"


def test_execution_bridge_respects_hard_stop(monkeypatch):
    manager = _DummyManager()
    monkeypatch.setattr(config, "FACTORY_EXECUTION_AUTOSTART_ENABLED", True)
    monkeypatch.setattr(config, "AGENTIC_FACTORY_MODE", "hard_stop")

    spec = RuntimePortfolioSpec(
        portfolio_id="betfair_core",
        label="Betfair Core",
        control_mode="local_managed",
        enabled=True,
    )
    monkeypatch.setattr("factory.execution_bridge.get_runtime_portfolio_spec", lambda portfolio_id: spec)

    bridge = FactoryExecutionBridge(process_manager=manager)
    payload = bridge.sync(
        {
            "lineages": [
                {
                    "lineage_id": "betfair_prediction_value_league:challenger:2",
                    "family_id": "betfair_prediction_value_league",
                    "active": True,
                    "current_stage": "paper",
                    "role": "paper_challenger",
                    "target_portfolios": ["betfair_core"],
                }
            ]
        }
    )

    assert manager.starts == []
    assert payload["targets"][0]["status"] == "factory_influence_paused"


def test_execution_bridge_prefers_lane_selected_candidate_contexts(monkeypatch):
    manager = _DummyManager()
    monkeypatch.setattr(config, "FACTORY_EXECUTION_AUTOSTART_ENABLED", False)
    monkeypatch.setattr(config, "AGENTIC_FACTORY_MODE", "full")

    spec = RuntimePortfolioSpec(
        portfolio_id="contrarian_legacy",
        label="Contrarian Legacy",
        control_mode="local_managed",
        enabled=True,
    )
    monkeypatch.setattr("factory.execution_bridge.get_runtime_portfolio_spec", lambda portfolio_id: spec)
    monkeypatch.setattr(
        "factory.execution_bridge.candidate_context_refs_for_portfolio",
        lambda portfolio_id: [
            {
                "family_id": "binance_funding_contrarian",
                "lineage_id": "binance_funding_contrarian:challenger:1",
                "role": "paper_challenger",
                "current_stage": "paper",
                "runtime_lane_kind": "isolated_challenger",
                "runtime_lane_reason": "family_replacement_pressure",
                "runtime_target_portfolio": "factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-1",
                "canonical_target_portfolio": "contrarian_legacy",
                "suppressed_sibling_count": 2,
            }
        ],
    )

    bridge = FactoryExecutionBridge(process_manager=manager)
    payload = bridge.sync(
        {
            "lineages": [
                {
                    "lineage_id": "binance_funding_contrarian:champion",
                    "family_id": "binance_funding_contrarian",
                    "active": True,
                    "current_stage": "paper",
                    "role": "champion",
                    "target_portfolios": ["contrarian_legacy"],
                },
                {
                    "lineage_id": "binance_funding_contrarian:challenger:1",
                    "family_id": "binance_funding_contrarian",
                    "active": True,
                    "current_stage": "paper",
                    "role": "paper_challenger",
                    "target_portfolios": ["contrarian_legacy"],
                },
            ]
        }
    )

    target = payload["targets"][0]
    assert target["portfolio_id"] == "factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-1"
    assert target["canonical_portfolio_id"] == "contrarian_legacy"
    assert target["active_lineage_count"] == 1
    assert target["lineage_ids"] == ["binance_funding_contrarian:challenger:1"]
    assert target["lineages"][0]["runtime_lane_kind"] == "isolated_challenger"
    assert target["status"] == "autostart_disabled"


def test_execution_bridge_autostarts_isolated_challenger_runtime_alias(monkeypatch):
    manager = _DummyManager()
    monkeypatch.setattr(config, "FACTORY_EXECUTION_AUTOSTART_ENABLED", True)
    monkeypatch.setattr(config, "AGENTIC_FACTORY_MODE", "full")

    spec = RuntimePortfolioSpec(
        portfolio_id="factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-1",
        label="Contrarian Legacy",
        control_mode="local_managed",
        enabled=True,
        canonical_portfolio_id="contrarian_legacy",
    )
    monkeypatch.setattr("factory.execution_bridge.get_runtime_portfolio_spec", lambda portfolio_id: spec)
    monkeypatch.setattr(
        "factory.execution_bridge.candidate_context_refs_for_portfolio",
        lambda portfolio_id: [
            {
                "family_id": "binance_funding_contrarian",
                "lineage_id": "binance_funding_contrarian:challenger:1",
                "role": "paper_challenger",
                "current_stage": "paper",
                "runtime_lane_kind": "isolated_challenger",
                "runtime_lane_reason": "family_replacement_pressure",
                "runtime_target_portfolio": "factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-1",
                "canonical_target_portfolio": "contrarian_legacy",
                "suppressed_sibling_count": 2,
            }
        ],
    )

    bridge = FactoryExecutionBridge(process_manager=manager)
    payload = bridge.sync(
        {
            "lineages": [
                {
                    "lineage_id": "binance_funding_contrarian:champion",
                    "family_id": "binance_funding_contrarian",
                    "active": True,
                    "current_stage": "paper",
                    "role": "champion",
                    "target_portfolios": ["contrarian_legacy"],
                },
                {
                    "lineage_id": "binance_funding_contrarian:challenger:1",
                    "family_id": "binance_funding_contrarian",
                    "active": True,
                    "current_stage": "paper",
                    "role": "paper_challenger",
                    "target_portfolios": ["contrarian_legacy"],
                },
            ]
        }
    )

    assert manager.starts == ["factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-1"]
    assert payload["targets"][0]["status"] == "started"
    assert payload["targets"][0]["activation_status"] == "started"


def test_execution_bridge_marks_prepared_isolated_lane_as_pending_stage(monkeypatch):
    manager = _DummyManager()
    monkeypatch.setattr(config, "FACTORY_EXECUTION_AUTOSTART_ENABLED", False)
    monkeypatch.setattr(config, "AGENTIC_FACTORY_MODE", "full")

    spec = RuntimePortfolioSpec(
        portfolio_id="contrarian_legacy",
        label="Contrarian Legacy",
        control_mode="local_managed",
        enabled=True,
    )
    monkeypatch.setattr("factory.execution_bridge.get_runtime_portfolio_spec", lambda portfolio_id: spec)
    monkeypatch.setattr("factory.execution_bridge.candidate_context_refs_for_portfolio", lambda portfolio_id: [])

    bridge = FactoryExecutionBridge(process_manager=manager)
    payload = bridge.sync(
        {
            "lineages": [
                {
                    "lineage_id": "binance_funding_contrarian:challenger:7",
                    "family_id": "binance_funding_contrarian",
                    "active": True,
                    "current_stage": "walkforward",
                    "role": "paper_challenger",
                    "iteration_status": "prepare_isolated_lane",
                    "target_portfolios": ["contrarian_legacy"],
                }
            ]
        }
    )

    target = payload["targets"][0]
    assert target["portfolio_id"] == "contrarian_legacy"
    assert target["prepared_isolated_lane"] is True
    assert target["prepared_lineage_ids"] == ["binance_funding_contrarian:challenger:7"]
    assert target["activation_status"] == "pending_stage"
    assert target["status"] == "autostart_disabled"


def test_execution_bridge_exposes_start_failed_for_isolated_alias(monkeypatch):
    class _FailingManager(_DummyManager):
        def start(self, portfolio_id: str):
            self.starts.append(portfolio_id)
            return {"ok": False, "error": "spawn_failed"}

    manager = _FailingManager()
    monkeypatch.setattr(config, "FACTORY_EXECUTION_AUTOSTART_ENABLED", True)
    monkeypatch.setattr(config, "AGENTIC_FACTORY_MODE", "full")

    spec = RuntimePortfolioSpec(
        portfolio_id="factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-2",
        label="Contrarian Legacy",
        control_mode="local_managed",
        enabled=True,
        canonical_portfolio_id="contrarian_legacy",
    )
    monkeypatch.setattr("factory.execution_bridge.get_runtime_portfolio_spec", lambda portfolio_id: spec)
    monkeypatch.setattr(
        "factory.execution_bridge.candidate_context_refs_for_portfolio",
        lambda portfolio_id: [
            {
                "family_id": "binance_funding_contrarian",
                "lineage_id": "binance_funding_contrarian:challenger:2",
                "role": "paper_challenger",
                "current_stage": "shadow",
                "runtime_lane_kind": "isolated_challenger",
                "runtime_lane_reason": "family_replacement_pressure",
                "runtime_target_portfolio": "factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-2",
                "canonical_target_portfolio": "contrarian_legacy",
                "suppressed_sibling_count": 1,
            }
        ],
    )

    bridge = FactoryExecutionBridge(process_manager=manager)
    payload = bridge.sync(
        {
            "lineages": [
                {
                    "lineage_id": "binance_funding_contrarian:challenger:2",
                    "family_id": "binance_funding_contrarian",
                    "active": True,
                    "current_stage": "shadow",
                    "role": "paper_challenger",
                    "target_portfolios": ["contrarian_legacy"],
                }
            ]
        }
    )

    target = payload["targets"][0]
    assert manager.starts == ["factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-2"]
    assert target["status"] == "start_failed"
    assert target["activation_status"] == "start_failed"


def test_execution_bridge_uses_runtime_contract_to_mark_started_alias(monkeypatch):
    manager = _DummyManager()
    manager.status_overrides[
        "factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-3"
    ] = {
        "running": True,
        "pid": 1234,
        "heartbeat": {"ts": "2026-03-12T10:00:00+00:00"},
        "runtime_status": "running",
        "publish_status": "starting",
        "health_status": "healthy",
        "issue_codes": [],
    }
    monkeypatch.setattr(config, "FACTORY_EXECUTION_AUTOSTART_ENABLED", False)
    monkeypatch.setattr(config, "AGENTIC_FACTORY_MODE", "full")

    spec = RuntimePortfolioSpec(
        portfolio_id="factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-3",
        label="Contrarian Legacy",
        control_mode="local_managed",
        enabled=True,
        canonical_portfolio_id="contrarian_legacy",
    )
    monkeypatch.setattr("factory.execution_bridge.get_runtime_portfolio_spec", lambda portfolio_id: spec)
    monkeypatch.setattr(
        "factory.execution_bridge.candidate_context_refs_for_portfolio",
        lambda portfolio_id: [
            {
                "family_id": "binance_funding_contrarian",
                "lineage_id": "binance_funding_contrarian:challenger:3",
                "role": "paper_challenger",
                "current_stage": "shadow",
                "runtime_lane_kind": "isolated_challenger",
                "runtime_lane_reason": "family_replacement_pressure",
                "runtime_target_portfolio": "factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-3",
                "canonical_target_portfolio": "contrarian_legacy",
                "suppressed_sibling_count": 0,
            }
        ],
    )

    bridge = FactoryExecutionBridge(process_manager=manager)
    payload = bridge.sync(
        {
            "lineages": [
                {
                    "lineage_id": "binance_funding_contrarian:challenger:3",
                    "family_id": "binance_funding_contrarian",
                    "active": True,
                    "current_stage": "shadow",
                    "role": "paper_challenger",
                    "target_portfolios": ["contrarian_legacy"],
                }
            ]
        }
    )

    target = payload["targets"][0]
    assert target["status"] == "running"
    assert target["activation_status"] == "started"
    assert target["publish_status"] == "starting"


def test_execution_bridge_enforces_per_family_runtime_lane_cap(monkeypatch):
    manager = _DummyManager()
    monkeypatch.setattr(config, "FACTORY_EXECUTION_AUTOSTART_ENABLED", False)
    monkeypatch.setattr(config, "AGENTIC_FACTORY_MODE", "full")
    monkeypatch.setattr(config, "FACTORY_RUNTIME_MAX_ACTIVE_PAPER_LANES", 6)
    monkeypatch.setattr(config, "FACTORY_RUNTIME_MAX_ACTIVE_PAPER_LANES_PER_FAMILY", 2)

    def _fake_get_spec(portfolio_id: str):
        return RuntimePortfolioSpec(
            portfolio_id=portfolio_id,
            label=portfolio_id,
            control_mode="local_managed",
            enabled=True,
        )

    monkeypatch.setattr("factory.execution_bridge.get_runtime_portfolio_spec", _fake_get_spec)
    monkeypatch.setattr(
        "factory.execution_bridge.candidate_context_refs_for_portfolio",
        lambda portfolio_id: [
            {
                "family_id": "binance_funding_contrarian",
                "lineage_id": f"binance_funding_contrarian:{portfolio_id}",
                "role": "champion" if portfolio_id == "contrarian_legacy" else "paper_challenger",
                "current_stage": "paper",
                "runtime_lane_kind": "primary_incumbent" if portfolio_id == "contrarian_legacy" else "isolated_challenger",
                "runtime_lane_reason": "family_primary_incumbent",
                "runtime_target_portfolio": portfolio_id,
                "canonical_target_portfolio": portfolio_id,
                "suppressed_sibling_count": 0,
                "fitness_score": 5.0 if portfolio_id == "hedge_validation" else (4.0 if portfolio_id == "contrarian_legacy" else 3.0),
            }
        ],
    )

    bridge = FactoryExecutionBridge(process_manager=manager)
    payload = bridge.sync(
        {
            "lineages": [
                {
                    "lineage_id": "binance_funding_contrarian:champion",
                    "family_id": "binance_funding_contrarian",
                    "active": True,
                    "current_stage": "paper",
                    "role": "champion",
                    "target_portfolios": [
                        "contrarian_legacy",
                        "hedge_validation",
                        "hedge_research",
                    ],
                }
            ]
        }
    )

    target_ids = sorted(item["portfolio_id"] for item in payload["targets"])
    assert len(target_ids) == 2
    assert payload["suppressed_portfolio_count"] == 1
    suppressed = sorted(item["portfolio_id"] for item in payload["suppressed_targets"])
    assert suppressed == ["contrarian_legacy"]


def test_execution_bridge_allows_multi_target_same_family_same_lane(monkeypatch):
    manager = _DummyManager()
    monkeypatch.setattr(config, "FACTORY_EXECUTION_AUTOSTART_ENABLED", False)
    monkeypatch.setattr(config, "AGENTIC_FACTORY_MODE", "full")
    monkeypatch.setattr(config, "FACTORY_RUNTIME_MAX_ACTIVE_PAPER_LANES", 10)
    monkeypatch.setattr(config, "FACTORY_RUNTIME_MAX_ACTIVE_PAPER_LANES_PER_FAMILY", 3)

    def _fake_get_spec(portfolio_id: str):
        return RuntimePortfolioSpec(
            portfolio_id=portfolio_id,
            label=portfolio_id,
            control_mode="local_managed",
            enabled=True,
        )

    monkeypatch.setattr("factory.execution_bridge.get_runtime_portfolio_spec", _fake_get_spec)

    def _fake_candidate_context_refs(portfolio_id: str):
        return [
            {
                "family_id": "binance_funding_contrarian",
                "lineage_id": "binance_funding_contrarian:challenger:1",
                "role": "paper_challenger",
                "current_stage": "paper",
                "runtime_lane_kind": "primary_incumbent",
                "runtime_lane_reason": "family_primary_incumbent",
                "runtime_target_portfolio": portfolio_id,
                "canonical_target_portfolio": portfolio_id,
                "suppressed_sibling_count": 0,
                "strict_gate_pass": True,
            }
        ]

    monkeypatch.setattr("factory.execution_bridge.candidate_context_refs_for_portfolio", _fake_candidate_context_refs)

    bridge = FactoryExecutionBridge(process_manager=manager)
    payload = bridge.sync(
        {
            "lineages": [
                {
                    "lineage_id": "binance_funding_contrarian:challenger:1",
                    "family_id": "binance_funding_contrarian",
                    "active": True,
                    "current_stage": "paper",
                    "role": "paper_challenger",
                    "target_portfolios": ["contrarian_legacy", "hedge_validation", "hedge_research"],
                }
            ]
        }
    )

    target_ids = sorted(item["portfolio_id"] for item in payload["targets"])
    assert target_ids == ["contrarian_legacy", "hedge_research", "hedge_validation"]
    assert payload["suppressed_portfolio_count"] == 0


def test_execution_bridge_enforces_global_runtime_lane_cap(monkeypatch):
    manager = _DummyManager()
    monkeypatch.setattr(config, "FACTORY_EXECUTION_AUTOSTART_ENABLED", False)
    monkeypatch.setattr(config, "AGENTIC_FACTORY_MODE", "full")
    monkeypatch.setattr(config, "FACTORY_RUNTIME_MAX_ACTIVE_PAPER_LANES", 1)
    monkeypatch.setattr(config, "FACTORY_RUNTIME_MAX_ACTIVE_PAPER_LANES_PER_FAMILY", 2)

    def _fake_get_spec(portfolio_id: str):
        return RuntimePortfolioSpec(
            portfolio_id=portfolio_id,
            label=portfolio_id,
            control_mode="local_managed",
            enabled=True,
        )

    monkeypatch.setattr("factory.execution_bridge.get_runtime_portfolio_spec", _fake_get_spec)
    monkeypatch.setattr(
        "factory.execution_bridge.candidate_context_refs_for_portfolio",
        lambda portfolio_id: [
            {
                "family_id": "binance_funding_contrarian" if "contrarian" in portfolio_id else "binance_cascade_regime",
                "lineage_id": f"lineage:{portfolio_id}",
                "role": "paper_challenger",
                "current_stage": "paper",
                "runtime_lane_kind": "isolated_challenger",
                "runtime_lane_reason": "family_replacement_pressure",
                "runtime_target_portfolio": portfolio_id,
                "canonical_target_portfolio": portfolio_id,
                "suppressed_sibling_count": 0,
                "fitness_score": 5.0 if "contrarian" in portfolio_id else 1.0,
            }
        ],
    )

    bridge = FactoryExecutionBridge(process_manager=manager)
    payload = bridge.sync(
        {
            "lineages": [
                {
                    "lineage_id": "binance_funding_contrarian:challenger:1",
                    "family_id": "binance_funding_contrarian",
                    "active": True,
                    "current_stage": "paper",
                    "role": "paper_challenger",
                    "target_portfolios": ["contrarian_legacy"],
                },
                {
                    "lineage_id": "binance_cascade_regime:challenger:1",
                    "family_id": "binance_cascade_regime",
                    "active": True,
                    "current_stage": "paper",
                    "role": "paper_challenger",
                    "target_portfolios": ["cascade_alpha"],
                },
            ]
        }
    )

    assert len(payload["targets"]) == 1
    assert payload["targets"][0]["portfolio_id"] == "contrarian_legacy"


def test_execution_bridge_prefers_unqualified_isolated_challenger_over_already_qualified_one(monkeypatch):
    manager = _DummyManager()
    monkeypatch.setattr(config, "FACTORY_EXECUTION_AUTOSTART_ENABLED", False)
    monkeypatch.setattr(config, "AGENTIC_FACTORY_MODE", "full")
    monkeypatch.setattr(config, "FACTORY_RUNTIME_MAX_ACTIVE_PAPER_LANES", 1)
    monkeypatch.setattr(config, "FACTORY_RUNTIME_MAX_ACTIVE_PAPER_LANES_PER_FAMILY", 2)

    def _fake_get_spec(portfolio_id: str):
        return RuntimePortfolioSpec(
            portfolio_id=portfolio_id,
            label=portfolio_id,
            control_mode="local_managed",
            enabled=True,
        )

    monkeypatch.setattr("factory.execution_bridge.get_runtime_portfolio_spec", _fake_get_spec)
    monkeypatch.setattr(
        "factory.execution_bridge.candidate_context_refs_for_portfolio",
        lambda portfolio_id: [
            {
                "family_id": "binance_funding_contrarian" if "contrarian" in portfolio_id else "binance_cascade_regime",
                "lineage_id": f"lineage:{portfolio_id}",
                "role": "paper_challenger",
                "current_stage": "paper",
                "runtime_lane_kind": "isolated_challenger",
                "runtime_lane_reason": "family_replacement_pressure",
                "runtime_target_portfolio": portfolio_id,
                "canonical_target_portfolio": portfolio_id,
                "suppressed_sibling_count": 0,
                "iteration_status": (
                    "isolated_lane_first_assessment_passed"
                    if portfolio_id == "contrarian_legacy"
                    else "isolated_lane_active"
                ),
                "fitness_score": 10.0 if portfolio_id == "contrarian_legacy" else 4.0,
            }
        ],
    )

    bridge = FactoryExecutionBridge(process_manager=manager)
    payload = bridge.sync(
        {
            "lineages": [
                {
                    "lineage_id": "binance_funding_contrarian:challenger:1",
                    "family_id": "binance_funding_contrarian",
                    "active": True,
                    "current_stage": "paper",
                    "role": "paper_challenger",
                    "target_portfolios": ["contrarian_legacy"],
                },
                {
                    "lineage_id": "binance_cascade_regime:challenger:1",
                    "family_id": "binance_cascade_regime",
                    "active": True,
                    "current_stage": "paper",
                    "role": "paper_challenger",
                    "target_portfolios": ["cascade_alpha"],
                },
            ]
        }
    )

    assert len(payload["targets"]) == 1
    assert payload["targets"][0]["portfolio_id"] == "cascade_alpha"


def test_execution_bridge_prefers_fresh_isolated_challenger_over_stale_started_alias(monkeypatch):
    manager = _DummyManager()
    monkeypatch.setattr(config, "FACTORY_EXECUTION_AUTOSTART_ENABLED", False)
    monkeypatch.setattr(config, "AGENTIC_FACTORY_MODE", "full")
    monkeypatch.setattr(config, "FACTORY_RUNTIME_MAX_ACTIVE_PAPER_LANES", 1)
    monkeypatch.setattr(config, "FACTORY_RUNTIME_MAX_ACTIVE_PAPER_LANES_PER_FAMILY", 2)
    monkeypatch.setattr(config, "FACTORY_RUNTIME_ALIAS_STALE_HOURS", 4.0)

    def _fake_get_spec(portfolio_id: str):
        return RuntimePortfolioSpec(
            portfolio_id=portfolio_id,
            label=portfolio_id,
            control_mode="local_managed",
            enabled=True,
        )

    monkeypatch.setattr("factory.execution_bridge.get_runtime_portfolio_spec", _fake_get_spec)
    monkeypatch.setattr(
        "factory.execution_bridge.candidate_context_refs_for_portfolio",
        lambda portfolio_id: [
            {
                "family_id": "binance_funding_contrarian" if "contrarian" in portfolio_id else "binance_cascade_regime",
                "lineage_id": f"lineage:{portfolio_id}",
                "role": "paper_challenger",
                "current_stage": "paper",
                "runtime_lane_kind": "isolated_challenger",
                "runtime_lane_reason": "family_replacement_pressure",
                "runtime_target_portfolio": portfolio_id,
                "canonical_target_portfolio": portfolio_id,
                "suppressed_sibling_count": 0,
                "iteration_status": "isolated_lane_active",
                "activation_status": "started" if portfolio_id == "contrarian_legacy" else "",
                "alias_runner_running": False,
                "live_paper_trade_count": 0,
                "live_paper_realized_pnl": 0.0,
                "execution_issue_codes": [],
                "execution_validation": {
                    "runtime_age_hours": 6.0 if portfolio_id == "contrarian_legacy" else 0.5,
                    "has_execution_signal": False,
                },
                "fitness_score": 10.0 if portfolio_id == "contrarian_legacy" else 4.0,
            }
        ],
    )

    bridge = FactoryExecutionBridge(process_manager=manager)
    payload = bridge.sync(
        {
            "lineages": [
                {
                    "lineage_id": "binance_funding_contrarian:challenger:1",
                    "family_id": "binance_funding_contrarian",
                    "active": True,
                    "current_stage": "paper",
                    "role": "paper_challenger",
                    "target_portfolios": ["contrarian_legacy"],
                },
                {
                    "lineage_id": "binance_cascade_regime:challenger:1",
                    "family_id": "binance_cascade_regime",
                    "active": True,
                    "current_stage": "paper",
                    "role": "paper_challenger",
                    "target_portfolios": ["cascade_alpha"],
                },
            ]
        }
    )

    assert len(payload["targets"]) == 1
    assert payload["targets"][0]["portfolio_id"] == "cascade_alpha"


def test_execution_bridge_keeps_publishing_isolated_alias_ahead_of_fresh_lane(monkeypatch):
    manager = _DummyManager()
    monkeypatch.setattr(config, "FACTORY_EXECUTION_AUTOSTART_ENABLED", False)
    monkeypatch.setattr(config, "AGENTIC_FACTORY_MODE", "full")
    monkeypatch.setattr(config, "FACTORY_RUNTIME_MAX_ACTIVE_PAPER_LANES", 1)
    monkeypatch.setattr(config, "FACTORY_RUNTIME_MAX_ACTIVE_PAPER_LANES_PER_FAMILY", 2)
    monkeypatch.setattr(config, "FACTORY_RUNTIME_ALIAS_STALE_HOURS", 4.0)

    def _fake_get_spec(portfolio_id: str):
        return RuntimePortfolioSpec(
            portfolio_id=portfolio_id,
            label=portfolio_id,
            control_mode="local_managed",
            enabled=True,
        )

    monkeypatch.setattr("factory.execution_bridge.get_runtime_portfolio_spec", _fake_get_spec)
    monkeypatch.setattr(
        "factory.execution_bridge.candidate_context_refs_for_portfolio",
        lambda portfolio_id: [
            {
                "family_id": "binance_funding_contrarian" if "contrarian" in portfolio_id else "binance_cascade_regime",
                "lineage_id": f"lineage:{portfolio_id}",
                "role": "paper_challenger",
                "current_stage": "paper",
                "runtime_lane_kind": "isolated_challenger",
                "runtime_lane_reason": "family_replacement_pressure",
                "runtime_target_portfolio": portfolio_id,
                "canonical_target_portfolio": portfolio_id,
                "suppressed_sibling_count": 0,
                "iteration_status": "isolated_lane_active",
                "activation_status": "running" if portfolio_id == "contrarian_legacy" else "",
                "alias_runner_running": portfolio_id == "contrarian_legacy",
                "live_paper_trade_count": 2 if portfolio_id == "contrarian_legacy" else 0,
                "live_paper_realized_pnl": 5.0 if portfolio_id == "contrarian_legacy" else 0.0,
                "execution_issue_codes": [],
                "execution_validation": {
                    "runtime_age_hours": 6.0 if portfolio_id == "contrarian_legacy" else 0.5,
                    "has_execution_signal": portfolio_id == "contrarian_legacy",
                },
                "fitness_score": 10.0 if portfolio_id == "contrarian_legacy" else 4.0,
            }
        ],
    )

    bridge = FactoryExecutionBridge(process_manager=manager)
    payload = bridge.sync(
        {
            "lineages": [
                {
                    "lineage_id": "binance_funding_contrarian:challenger:1",
                    "family_id": "binance_funding_contrarian",
                    "active": True,
                    "current_stage": "paper",
                    "role": "paper_challenger",
                    "target_portfolios": ["contrarian_legacy"],
                },
                {
                    "lineage_id": "binance_cascade_regime:challenger:1",
                    "family_id": "binance_cascade_regime",
                    "active": True,
                    "current_stage": "paper",
                    "role": "paper_challenger",
                    "target_portfolios": ["cascade_alpha"],
                },
            ]
        }
    )

    assert len(payload["targets"]) == 1
    assert payload["targets"][0]["portfolio_id"] == "contrarian_legacy"
