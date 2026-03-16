"""Provenance package — Task 02 Goldfish integration scaffolding.

Provides the stable boundary between AgenticTrading experiment records
and the Goldfish durable provenance backend.

When FACTORY_ENABLE_GOLDFISH_PROVENANCE=false (default), all provenance
writes are no-ops and local registry remains the sole store. When enabled,
Goldfish is expected to be the authoritative record backend.
"""
from factory.provenance.goldfish_client import (
    GoldfishClient,
    GoldfishUnavailableError,
    NullGoldfishClient,
    ProvenanceService,
)
from factory.provenance.goldfish_mapper import (
    GoldfishRunMetadata,
    build_evaluation_run_metadata,
    build_learning_note_metadata,
    build_promotion_metadata,
    build_retirement_metadata,
)
from factory.provenance.lineage_projection import ProvenanceRef

__all__ = [
    "GoldfishClient",
    "GoldfishUnavailableError",
    "NullGoldfishClient",
    "ProvenanceService",
    "GoldfishRunMetadata",
    "build_evaluation_run_metadata",
    "build_learning_note_metadata",
    "build_promotion_metadata",
    "build_retirement_metadata",
    "ProvenanceRef",
]
