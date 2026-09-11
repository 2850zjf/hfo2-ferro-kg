from __future__ import annotations

from pathlib import Path

import yaml


def test_phase_competition_extension_has_required_comparison_entities() -> None:
    path = Path(__file__).resolve().parents[1] / "ontology" / "phase_competition_extension.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["status"] == "proposed_isolated_extension_not_merged_into_production_ontology"
    assert {
        "PhaseObservation",
        "SampleConditionProfile",
        "SampleComparison",
        "EvidenceConsistencyAssessment",
        "BenchmarkPaperPartition",
    } <= set(data["classes"])
    contract = data["benchmark_contract"]
    assert contract["group_key_priority"] == ["normalized_doi", "stable_paper_id"]
    assert contract["row_split_fallback"] == "prohibited"
    assert contract["missing_condition_semantics"] == "unknown_not_equal"


def test_phase_is_primary_and_pr_2pr_are_auxiliary() -> None:
    path = Path(__file__).resolve().parents[1] / "ontology" / "phase_competition_extension.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["scope"]["primary_outcomes"] == ["monoclinic", "orthorhombic", "tetragonal"]
    assert data["scope"]["auxiliary_targets"] == [
        "remanent_polarization_Pr",
        "double_remanent_polarization_2Pr",
    ]
    mechanism = data["computational_mechanism"]
    assert mechanism["selected_mechanism"] == "biaxial_strain_effect_on_o_t_m_relative_stability"
    assert "Does not validate a specific annealing temperature or time." in mechanism["interpretation_boundary"]

