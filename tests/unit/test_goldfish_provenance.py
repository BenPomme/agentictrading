"""Tests for Task 02: Goldfish client and provenance mapping.

Covers:
- GoldfishClient raises GoldfishUnavailableError when library not installed
- NullGoldfishClient is a safe no-op for all methods
- ProvenanceService.create() respects FACTORY_ENABLE_GOLDFISH_PROVENANCE flag
- ProvenanceService.record_evaluation() returns None when disabled
- ProvenanceService._call() logs and suppresses errors by default (fail_on_error=False)
- ProvenanceService._call() re-raises when fail_on_error=True
- GoldfishRunMetadata serializes all required correlation fields
- build_evaluation_run_metadata() maps EvaluationBundle → GoldfishRunMetadata
- build_retirement_metadata() and build_promotion_metadata() map correctly
- build_learning_note_metadata() maps LearningMemoryEntry
- ProvenanceRef serializes and deserializes round-trip
- LineageProjectionStore saves and loads ProvenanceRef
- FactoryRegistry.save_provenance_ref / load_provenance_ref integration
- Orchestrator has self.provenance attribute
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import config
from factory.contracts import (
    EvaluationBundle,
    EvaluationWindow,
    FactoryFamily,
    LearningMemoryEntry,
    LineageRecord,
    MutationBounds,
    StrategyGenome,
)
from factory.provenance.goldfish_client import (
    GoldfishClient,
    GoldfishError,
    GoldfishUnavailableError,
    NullGoldfishClient,
    ProvenanceService,
    _check_goldfish_available,
)
from factory.provenance.goldfish_mapper import (
    GoldfishLearningNote,
    GoldfishRunMetadata,
    build_evaluation_result_payload,
    build_evaluation_run_metadata,
    build_learning_note_metadata,
    build_promotion_metadata,
    build_retirement_metadata,
)
from factory.provenance.lineage_projection import LineageProjectionStore, ProvenanceRef
from factory.registry import FactoryRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp_registry(tmp_path: Path) -> FactoryRegistry:
    root = tmp_path / "data" / "factory"
    root.mkdir(parents=True, exist_ok=True)
    return FactoryRegistry(root)


def _lineage(lineage_id: str = "fam:lin:1", family_id: str = "test_family") -> LineageRecord:
    return LineageRecord(
        lineage_id=lineage_id,
        family_id=family_id,
        label="Test Lineage",
        role="shadow_challenger",
        current_stage="walkforward",
        target_portfolios=["p1"],
        target_venues=["binance"],
        hypothesis_id="h1",
        genome_id="g1",
        experiment_id="e1",
        budget_bucket="adjacent",
        budget_weight_pct=10.0,
        connector_ids=["binance_core"],
        goldfish_workspace="research/goldfish/test_family",
    )


def _genome(lineage_id: str = "fam:lin:1") -> StrategyGenome:
    return StrategyGenome(
        genome_id="g1",
        lineage_id=lineage_id,
        family_id="test_family",
        parent_genome_id=None,
        role="shadow_challenger",
        parameters={"selected_horizon_seconds": 600},
        mutation_bounds=MutationBounds(),
        scientific_domains=["econometrics"],
        budget_bucket="adjacent",
        resource_profile="local-first",
        budget_weight_pct=10.0,
    )


def _bundle(lineage_id: str = "fam:lin:1") -> EvaluationBundle:
    return EvaluationBundle(
        evaluation_id="eval-001",
        lineage_id=lineage_id,
        family_id="test_family",
        stage="walkforward",
        source="backtest",
        monthly_roi_pct=3.5,
        max_drawdown_pct=5.0,
        fitness_score=2.1,
        trade_count=80,
        paper_days=0,
    )


def _memory() -> LearningMemoryEntry:
    return LearningMemoryEntry(
        memory_id="mem-001",
        family_id="test_family",
        lineage_id="fam:lin:1",
        hypothesis_id="h1",
        outcome="retired_no_edge",
        summary="Regime model failed across all venues.",
        scientific_domains=["econometrics"],
        lead_agent_role="Director",
        tweak_count=2,
        decision_stage="walkforward",
        recommendations=["avoid cascade features"],
    )


# ---------------------------------------------------------------------------
# GoldfishClient: library unavailable
# ---------------------------------------------------------------------------

class TestGoldfishClientUnavailable:
    def test_check_availability_returns_false_when_not_installed(self):
        """goldfish is not installed in this environment — availability must be False."""
        import factory.provenance.goldfish_client as mod
        # Temporarily reset the cache, mock the import to fail, then restore.
        orig_available = mod._GOLDFISH_AVAILABLE
        orig_module = mod._goldfish_module
        mod._GOLDFISH_AVAILABLE = None
        mod._goldfish_module = None
        try:
            with patch.dict("sys.modules", {"goldfish": None}):
                result = _check_goldfish_available()
            assert result is False
        finally:
            mod._GOLDFISH_AVAILABLE = orig_available
            mod._goldfish_module = orig_module

    def test_goldfish_client_raises_unavailable_on_ensure_project(self, tmp_path):
        client = GoldfishClient(tmp_path)
        # Force library unavailable
        with patch("factory.provenance.goldfish_client._check_goldfish_available", return_value=False), \
             patch("factory.provenance.goldfish_client._GOLDFISH_AVAILABLE", False):
            with pytest.raises(GoldfishUnavailableError):
                client.ensure_project()

    def test_goldfish_client_raises_unavailable_on_create_run(self, tmp_path):
        client = GoldfishClient(tmp_path)
        with patch("factory.provenance.goldfish_client._check_goldfish_available", return_value=False), \
             patch("factory.provenance.goldfish_client._GOLDFISH_AVAILABLE", False):
            with pytest.raises(GoldfishUnavailableError):
                client.create_run(workspace_id="ws", run_id="r1", metadata={})

    def test_goldfish_client_healthcheck_returns_false_when_unavailable(self, tmp_path):
        client = GoldfishClient(tmp_path)
        with patch("factory.provenance.goldfish_client._check_goldfish_available", return_value=False):
            assert client.healthcheck() is False


# ---------------------------------------------------------------------------
# NullGoldfishClient: safe no-op
# ---------------------------------------------------------------------------

class TestNullGoldfishClient:
    def test_all_methods_are_safe(self):
        client = NullGoldfishClient()
        client.ensure_project()
        client.ensure_daemon()
        r = client.ensure_workspace(workspace_id="ws", thesis="test")
        assert isinstance(r, dict)
        assert r["workspace_id"] == "ws"
        assert r["provenance_disabled"] is True

        run_id = client.create_run(workspace_id="ws", run_id="r1", metadata={})
        assert run_id == "r1"

        record_id = client.finalize_run(run_id="r1", workspace_id="ws", result={})
        assert record_id == "r1"

        assert client.inspect_record(record_id="r1", workspace_id="ws") == {}
        assert client.list_history(workspace_id="ws") == []

        client.tag_record(record_id="r1", workspace_id="ws", tags=["retired"])
        client.log_thought(workspace_id="ws", thought="test note")

    def test_null_healthcheck_returns_false(self):
        assert NullGoldfishClient().healthcheck() is False


# ---------------------------------------------------------------------------
# ProvenanceService: feature flag gating
# ---------------------------------------------------------------------------

class TestProvenanceServiceFlagGating:
    def test_disabled_by_default(self, tmp_path):
        with patch.object(config, "FACTORY_ENABLE_GOLDFISH_PROVENANCE", False):
            svc = ProvenanceService.create(tmp_path)
        assert svc.enabled is False

    def test_uses_null_client_when_disabled(self, tmp_path):
        with patch.object(config, "FACTORY_ENABLE_GOLDFISH_PROVENANCE", False):
            svc = ProvenanceService.create(tmp_path)
        assert isinstance(svc._client, NullGoldfishClient)

    def test_uses_goldfish_client_when_enabled(self, tmp_path):
        with patch.object(config, "FACTORY_ENABLE_GOLDFISH_PROVENANCE", True):
            svc = ProvenanceService.create(tmp_path)
        assert isinstance(svc._client, GoldfishClient)
        assert svc.enabled is True

    def test_record_evaluation_returns_none_when_disabled(self, tmp_path):
        with patch.object(config, "FACTORY_ENABLE_GOLDFISH_PROVENANCE", False):
            svc = ProvenanceService.create(tmp_path)
        result = svc.record_evaluation(
            workspace_id="ws", run_id="r1",
            lineage_id="l1", family_id="f1", cycle_id="c1",
            evaluation_payload={}, correlation={},
        )
        assert result is None

    def test_record_retirement_is_noop_when_disabled(self, tmp_path):
        with patch.object(config, "FACTORY_ENABLE_GOLDFISH_PROVENANCE", False):
            svc = ProvenanceService.create(tmp_path)
        # Should not raise
        svc.record_retirement(
            workspace_id="ws", record_id="r1",
            lineage_id="l1", family_id="f1",
            reason="test", cost_summary={}, best_metrics={}, lessons=["x"],
        )

    def test_record_promotion_is_noop_when_disabled(self, tmp_path):
        with patch.object(config, "FACTORY_ENABLE_GOLDFISH_PROVENANCE", False):
            svc = ProvenanceService.create(tmp_path)
        svc.record_promotion(
            workspace_id="ws", record_id="r1",
            lineage_id="l1", family_id="f1",
            from_stage="walkforward", to_stage="stress",
            decision={"reasons": ["good perf"]},
        )

    def test_record_learning_note_is_noop_when_disabled(self, tmp_path):
        with patch.object(config, "FACTORY_ENABLE_GOLDFISH_PROVENANCE", False):
            svc = ProvenanceService.create(tmp_path)
        svc.record_learning_note(
            workspace_id="ws", lineage_id="l1", family_id="f1",
            outcome="retired", summary="s", domains=[], recommendations=[],
        )


# ---------------------------------------------------------------------------
# ProvenanceService: error surfacing
# ---------------------------------------------------------------------------

class TestProvenanceServiceErrorHandling:
    def _enabled_svc_with_mock(self, tmp_path, fail_on_error: bool = False) -> ProvenanceService:
        mock_client = MagicMock(spec=GoldfishClient)
        svc = ProvenanceService(mock_client, enabled=True, fail_on_error=fail_on_error)
        return svc, mock_client

    def test_goldfish_unavailable_logged_not_raised_when_fallback_on(self, tmp_path):
        mock_client = MagicMock()
        mock_client.create_run.side_effect = GoldfishUnavailableError("not installed")
        svc = ProvenanceService(mock_client, enabled=True, fail_on_error=False)
        # Should not raise, should log error
        result = svc.record_evaluation(
            workspace_id="ws", run_id="r1", lineage_id="l1",
            family_id="f1", cycle_id="c1", evaluation_payload={}, correlation={},
        )
        assert result is None

    def test_goldfish_unavailable_raises_when_fail_on_error(self, tmp_path):
        mock_client = MagicMock()
        mock_client.create_run.side_effect = GoldfishUnavailableError("not installed")
        svc = ProvenanceService(mock_client, enabled=True, fail_on_error=True)
        with pytest.raises(GoldfishUnavailableError):
            svc.record_evaluation(
                workspace_id="ws", run_id="r1", lineage_id="l1",
                family_id="f1", cycle_id="c1", evaluation_payload={}, correlation={},
            )

    def test_goldfish_write_error_logged_not_raised_by_default(self, tmp_path):
        from factory.provenance.goldfish_client import GoldfishWriteError
        mock_client = MagicMock()
        mock_client.create_run.side_effect = GoldfishWriteError("disk full")
        svc = ProvenanceService(mock_client, enabled=True, fail_on_error=False)
        result = svc.record_evaluation(
            workspace_id="ws", run_id="r1", lineage_id="l1",
            family_id="f1", cycle_id="c1", evaluation_payload={}, correlation={},
        )
        assert result is None

    def test_successful_mock_evaluation_returns_record_id(self, tmp_path):
        mock_client = MagicMock()
        mock_client.create_run.return_value = "r1"
        mock_client.finalize_run.return_value = "record-xyz"
        svc = ProvenanceService(mock_client, enabled=True, fail_on_error=False)
        result = svc.record_evaluation(
            workspace_id="ws", run_id="r1", lineage_id="l1",
            family_id="f1", cycle_id="c1",
            evaluation_payload={"fitness": 2.1},
            correlation={"trace_id": "t1"},
        )
        assert result == "record-xyz"
        mock_client.create_run.assert_called_once()
        mock_client.finalize_run.assert_called_once()


# ---------------------------------------------------------------------------
# GoldfishRunMetadata contracts
# ---------------------------------------------------------------------------

class TestGoldfishRunMetadata:
    def test_required_fields_in_to_dict(self):
        meta = GoldfishRunMetadata(
            run_id="run-abc",
            workspace_id="test_family",
            lineage_id="fam:lin:1",
            family_id="test_family",
            cycle_id="cycle-001",
            evaluation_id="eval-001",
            stage="walkforward",
        )
        d = meta.to_dict()
        required = [
            "run_id", "workspace_id", "lineage_id", "family_id",
            "cycle_id", "evaluation_id", "stage", "backend",
            "model_code_hash", "parameter_genome_hash",
            "dataset_fingerprint", "budget_snapshot",
            "orchestration_backend", "created_at",
        ]
        for key in required:
            assert key in d, f"Missing field: {key}"

    def test_workspace_id_equals_family_id(self):
        meta = GoldfishRunMetadata(
            run_id="r", workspace_id="fam", lineage_id="l",
            family_id="fam", cycle_id="c", evaluation_id="e", stage="s",
        )
        assert meta.workspace_id == meta.family_id


# ---------------------------------------------------------------------------
# build_evaluation_run_metadata
# ---------------------------------------------------------------------------

class TestBuildEvaluationRunMetadata:
    def test_maps_bundle_to_metadata(self):
        bundle = _bundle()
        lineage = _lineage()
        genome = _genome()
        meta = build_evaluation_run_metadata(
            bundle=bundle,
            lineage=lineage,
            genome=genome,
            cycle_id="cycle-42",
        )
        assert meta.lineage_id == lineage.lineage_id
        assert meta.family_id == lineage.family_id
        assert meta.evaluation_id == bundle.evaluation_id
        assert meta.stage == bundle.stage
        assert meta.cycle_id == "cycle-42"
        assert meta.workspace_id == lineage.family_id

    def test_genome_hash_is_deterministic(self):
        bundle = _bundle()
        lineage = _lineage()
        genome = _genome()
        m1 = build_evaluation_run_metadata(bundle=bundle, lineage=lineage, genome=genome, cycle_id="c")
        m2 = build_evaluation_run_metadata(bundle=bundle, lineage=lineage, genome=genome, cycle_id="c")
        assert m1.parameter_genome_hash == m2.parameter_genome_hash

    def test_genome_hash_none_when_genome_none(self):
        meta = build_evaluation_run_metadata(
            bundle=_bundle(), lineage=_lineage(), genome=None, cycle_id="c"
        )
        assert meta.parameter_genome_hash is None

    def test_run_id_is_deterministic(self):
        bundle = _bundle()
        lineage = _lineage()
        m1 = build_evaluation_run_metadata(bundle=bundle, lineage=lineage, cycle_id="c")
        m2 = build_evaluation_run_metadata(bundle=bundle, lineage=lineage, cycle_id="c")
        assert m1.run_id == m2.run_id

    def test_evaluation_result_payload_contains_key_fields(self):
        bundle = _bundle()
        payload = build_evaluation_result_payload(bundle)
        for key in ["evaluation_id", "stage", "monthly_roi_pct", "fitness_score", "trade_count"]:
            assert key in payload


# ---------------------------------------------------------------------------
# build_retirement_metadata
# ---------------------------------------------------------------------------

class TestBuildRetirementMetadata:
    def test_maps_lineage_to_retirement(self):
        lineage = _lineage()
        rec = build_retirement_metadata(
            lineage=lineage,
            reason="no edge after 3 tweaks",
            best_metrics={"monthly_roi_pct": 0.1},
            lessons=["avoid cascade features"],
        )
        assert rec.lineage_id == lineage.lineage_id
        assert rec.family_id == lineage.family_id
        assert rec.workspace_id == lineage.family_id
        assert rec.reason == "no edge after 3 tweaks"
        assert "avoid cascade features" in rec.lessons

    def test_retirement_to_dict_has_required_fields(self):
        rec = build_retirement_metadata(
            lineage=_lineage(), reason="x", best_metrics={}, lessons=[]
        )
        d = rec.to_dict()
        for key in ["lineage_id", "family_id", "workspace_id", "reason",
                    "best_metrics", "cost_summary", "lessons", "retired_at"]:
            assert key in d


# ---------------------------------------------------------------------------
# build_promotion_metadata
# ---------------------------------------------------------------------------

class TestBuildPromotionMetadata:
    def test_maps_lineage_to_promotion(self):
        lineage = _lineage()
        rec = build_promotion_metadata(
            lineage=lineage,
            from_stage="walkforward",
            to_stage="stress",
            decision={"reasons": ["strong perf"]},
        )
        assert rec.lineage_id == lineage.lineage_id
        assert rec.from_stage == "walkforward"
        assert rec.to_stage == "stress"
        d = rec.to_dict()
        for key in ["lineage_id", "from_stage", "to_stage", "decision", "promoted_at"]:
            assert key in d


# ---------------------------------------------------------------------------
# build_learning_note_metadata
# ---------------------------------------------------------------------------

class TestBuildLearningNoteMetadata:
    def test_maps_memory_to_note(self):
        memory = _memory()
        note = build_learning_note_metadata(memory=memory)
        assert note.lineage_id == memory.lineage_id
        assert note.family_id == memory.family_id
        assert note.outcome == memory.outcome
        assert note.workspace_id == memory.family_id

    def test_thought_text_contains_key_info(self):
        note = build_learning_note_metadata(memory=_memory())
        text = note.to_thought_text()
        assert "LEARNING" in text
        assert "retired_no_edge" in text
        assert "econometrics" in text


# ---------------------------------------------------------------------------
# ProvenanceRef contracts
# ---------------------------------------------------------------------------

class TestProvenanceRef:
    def test_to_dict_and_from_dict_roundtrip(self):
        ref = ProvenanceRef(
            lineage_id="fam:lin:1",
            family_id="test_family",
            workspace_id="test_family",
            goldfish_record_id="record-abc",
            evaluation_id="eval-001",
            cycle_id="cycle-001",
            stage="walkforward",
        )
        d = ref.to_dict()
        restored = ProvenanceRef.from_dict(d)
        assert restored.lineage_id == ref.lineage_id
        assert restored.goldfish_record_id == ref.goldfish_record_id
        assert restored.degraded is False

    def test_make_degraded_creates_ref_with_empty_record_id(self):
        ref = ProvenanceRef.make_degraded(
            lineage_id="l1", family_id="f1",
            evaluation_id="e1", cycle_id="c1",
            stage="walkforward", reason="goldfish unavailable",
        )
        assert ref.degraded is True
        assert ref.goldfish_record_id == ""
        assert ref.degraded_reason == "goldfish unavailable"


# ---------------------------------------------------------------------------
# LineageProjectionStore
# ---------------------------------------------------------------------------

class TestLineageProjectionStore:
    def test_save_and_load_roundtrip(self, tmp_path):
        store = LineageProjectionStore(tmp_path / "factory")
        ref = ProvenanceRef(
            lineage_id="fam:lin:1",
            family_id="test_family",
            workspace_id="test_family",
            goldfish_record_id="record-abc",
            evaluation_id="eval-001",
            cycle_id="cycle-001",
            stage="walkforward",
        )
        store.save(ref)
        loaded = store.load("fam:lin:1")
        assert loaded is not None
        assert loaded.goldfish_record_id == "record-abc"

    def test_load_returns_none_for_unknown(self, tmp_path):
        store = LineageProjectionStore(tmp_path / "factory")
        assert store.load("does_not_exist") is None

    def test_all_refs_returns_saved_entries(self, tmp_path):
        store = LineageProjectionStore(tmp_path / "factory")
        for i in range(3):
            store.save(ProvenanceRef(
                lineage_id=f"l{i}", family_id="f", workspace_id="f",
                goldfish_record_id=f"rec-{i}", evaluation_id=f"e{i}",
                cycle_id="c", stage="walkforward",
            ))
        refs = store.all_refs()
        assert len(refs) == 3

    def test_save_overwrites_latest(self, tmp_path):
        store = LineageProjectionStore(tmp_path / "factory")
        ref1 = ProvenanceRef(
            lineage_id="l1", family_id="f", workspace_id="f",
            goldfish_record_id="rec-1", evaluation_id="e1", cycle_id="c", stage="walkforward",
        )
        ref2 = ProvenanceRef(
            lineage_id="l1", family_id="f", workspace_id="f",
            goldfish_record_id="rec-2", evaluation_id="e2", cycle_id="c", stage="stress",
        )
        store.save(ref1)
        store.save(ref2)
        loaded = store.load("l1")
        assert loaded.goldfish_record_id == "rec-2"


# ---------------------------------------------------------------------------
# FactoryRegistry provenance integration
# ---------------------------------------------------------------------------

class TestRegistryProvenanceIntegration:
    def test_save_and_load_provenance_ref(self, tmp_path):
        registry = _tmp_registry(tmp_path)
        ref = ProvenanceRef(
            lineage_id="fam:lin:1",
            family_id="test_family",
            workspace_id="test_family",
            goldfish_record_id="record-abc",
            evaluation_id="eval-001",
            cycle_id="cycle-001",
            stage="walkforward",
        )
        registry.save_provenance_ref(ref)
        loaded = registry.load_provenance_ref("fam:lin:1")
        assert loaded is not None
        assert loaded.goldfish_record_id == "record-abc"

    def test_load_provenance_ref_returns_none_for_unknown(self, tmp_path):
        registry = _tmp_registry(tmp_path)
        assert registry.load_provenance_ref("nonexistent") is None

    def test_all_provenance_refs_returns_saved(self, tmp_path):
        registry = _tmp_registry(tmp_path)
        for i in range(2):
            registry.save_provenance_ref(ProvenanceRef(
                lineage_id=f"l{i}", family_id="f", workspace_id="f",
                goldfish_record_id=f"rec-{i}", evaluation_id=f"e{i}",
                cycle_id="c", stage="walkforward",
            ))
        assert len(registry.all_provenance_refs()) == 2

    def test_degraded_ref_is_stored_and_flagged(self, tmp_path):
        registry = _tmp_registry(tmp_path)
        ref = ProvenanceRef.make_degraded(
            lineage_id="l1", family_id="f",
            evaluation_id="e1", cycle_id="c",
            stage="walkforward", reason="goldfish unavailable",
        )
        registry.save_provenance_ref(ref)
        loaded = registry.load_provenance_ref("l1")
        assert loaded.degraded is True
        assert loaded.goldfish_record_id == ""


# ---------------------------------------------------------------------------
# Orchestrator wiring
# ---------------------------------------------------------------------------

class TestOrchestratorProvenance:
    def test_orchestrator_has_provenance_attribute(self, tmp_path):
        root = tmp_path
        (root / "data" / "factory" / "agent_runs").mkdir(parents=True, exist_ok=True)
        with patch.object(config, "FACTORY_RUNTIME_BACKEND", "legacy"), \
             patch.object(config, "FACTORY_ENABLE_MOBKIT", False), \
             patch.object(config, "FACTORY_ENABLE_GOLDFISH_PROVENANCE", False), \
             patch.object(config, "FACTORY_ROOT", str(root / "data" / "factory")), \
             patch.object(config, "FACTORY_GOLDFISH_ROOT", str(root / "research" / "goldfish")):
            from factory.orchestrator import FactoryOrchestrator
            orch = FactoryOrchestrator(root)
        assert hasattr(orch, "provenance")
        assert isinstance(orch.provenance, ProvenanceService)

    def test_orchestrator_provenance_disabled_by_default(self, tmp_path):
        root = tmp_path
        (root / "data" / "factory" / "agent_runs").mkdir(parents=True, exist_ok=True)
        with patch.object(config, "FACTORY_RUNTIME_BACKEND", "legacy"), \
             patch.object(config, "FACTORY_ENABLE_MOBKIT", False), \
             patch.object(config, "FACTORY_ENABLE_GOLDFISH_PROVENANCE", False), \
             patch.object(config, "FACTORY_ROOT", str(root / "data" / "factory")), \
             patch.object(config, "FACTORY_GOLDFISH_ROOT", str(root / "research" / "goldfish")):
            from factory.orchestrator import FactoryOrchestrator
            orch = FactoryOrchestrator(root)
        assert orch.provenance.enabled is False
