from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from backend.services.phase_contradiction_analysis import (
    PhaseObservation,
    assert_no_group_leakage,
    build_locked_split_manifest,
    build_phase_annotation_queue,
    canonical_phase_labels,
    find_phase_contradiction_candidates,
    load_phase_observations,
    open_readonly_database,
    select_phase_evidence,
    suggest_missing_conditions,
)


def _observation(
    observation_id: str,
    doi: str,
    phase: str,
    *,
    method: str = "XRD",
    evidence: str | None = None,
    conditions: dict | None = None,
    quality_flags: tuple[str, ...] = (),
) -> PhaseObservation:
    if evidence is None:
        evidence = f"XRD identifies the {phase} phase."
    sample_conditions = {
        "film_thickness_nm": 10.0,
        "deposition_method": "ALD",
        "substrate": "Si",
        "top_electrode": "TiN",
        "bottom_electrode": "TiN",
        "device_stack": "TiN/HZO/TiN",
        "annealing_temperature_c": 500.0,
        "annealing_time_s": 30.0,
        "annealing_atmosphere": "N2",
        "strain_state": "biaxial compressive",
        "sample_form": "capacitor",
        "device_type": "FeCAP",
    }
    sample_conditions.update(conditions or {})
    return PhaseObservation(
        observation_id=observation_id,
        paper_id=f"paper_{observation_id}",
        paper_group=f"doi:{doi}",
        doi=doi,
        title=f"Paper {observation_id}",
        pdf_id=f"pdf_{observation_id}",
        chunk_id=f"chunk_{observation_id}",
        page_number=3,
        source_kind="reviewed_fact",
        source_id=f"fact_{observation_id}",
        sample_id=f"sample_{observation_id}",
        material_system="HZO",
        material_name="Hf0.5Zr0.5O2",
        zr_fraction=0.5,
        conditions=sample_conditions,
        phase_raw=phase,
        phase_labels=canonical_phase_labels(phase),
        characterization_method=method,
        property_name="remanent_polarization_Pr",
        property_value=20.0,
        property_unit="μC/cm²",
        property_condition="pristine",
        evidence_text=evidence,
        context_quality="strong",
        context_score=0.92,
        quality_flags=quality_flags,
        paper_is_review=False,
    )


def test_phase_normalization_separates_target_and_vague_labels() -> None:
    assert canonical_phase_labels("orthorhombic/tetragonal Pca21") == ("o", "t")
    assert canonical_phase_labels("monoclinic P21/c") == ("m",)
    assert canonical_phase_labels("ferroelectric phase") == ()
    assert canonical_phase_labels("non-monoclinic phase") == ()
    assert canonical_phase_labels("without tetragonal phase") == ()


def test_phase_evidence_can_be_recovered_only_from_same_chunk() -> None:
    text, source = select_phase_evidence(
        ("o",),
        "The remanent polarization is 20 μC/cm².",
        "The remanent polarization is 20 μC/cm². GIXRD identifies the orthorhombic phase.",
    )
    assert source == "chunk_recovered"
    assert "orthorhombic" in text


def test_missing_condition_suggestions_remain_review_candidates() -> None:
    suggestions = suggest_missing_conditions(
        {},
        "A 10 nm-thick HZO film was deposited by ALD and annealed at 500 °C for 30 s in N2.",
    )
    assert suggestions["film_thickness_nm"]["value"] == 10.0
    assert suggestions["deposition_method"]["value"] == "ALD"
    assert suggestions["annealing_temperature_c"]["value"] == 500.0
    assert suggestions["annealing_time_s"]["value"] == 30.0
    assert suggestions["annealing_atmosphere"]["value"] == "N2"
    assert all(item["confidence"] == "candidate_needs_human_review" for item in suggestions.values())
    false_positive_guard = suggest_missing_conditions(
        {},
        "Annealed HZO films show Hf 4f, Zr 3d, and O 1s features. Co electrodes were grown by DC sputtering.",
    )
    assert "annealing_time_s" not in false_positive_guard
    assert "deposition_method" not in false_positive_guard


def test_similar_cross_doi_incompatible_phases_are_scientific_candidate() -> None:
    candidates = find_phase_contradiction_candidates(
        [_observation("a", "10.1000/a", "orthorhombic"), _observation("b", "10.1000/b", "monoclinic")]
    )
    assert len(candidates) == 1
    assert candidates[0].classification == "scientific_contradiction_candidate"
    assert candidates[0].nominal_similarity == 1.0
    assert candidates[0].observation_a.paper_group != candidates[0].observation_b.paper_group


def test_characterization_scope_difference_is_not_called_true_contradiction() -> None:
    candidates = find_phase_contradiction_candidates(
        [
            _observation("a", "10.1000/a", "orthorhombic", method="GIXRD"),
            _observation("b", "10.1000/b", "tetragonal", method="HRTEM"),
        ]
    )
    assert candidates[0].classification == "condition_explained_difference"
    assert "characterization_scope_difference" in candidates[0].comparison_reasons


def test_missing_evidence_routes_pair_to_extraction_review() -> None:
    bad = _observation(
        "b",
        "10.1000/b",
        "monoclinic",
        evidence="",
        quality_flags=("missing_evidence_text",),
    )
    candidates = find_phase_contradiction_candidates([_observation("a", "10.1000/a", "orthorhombic"), bad])
    assert candidates[0].classification == "extraction_review_needed"
    assert candidates[0].comparison_reasons == ("missing_evidence_text",)


def test_same_doi_is_never_paired_and_split_is_group_locked() -> None:
    a = _observation("a", "10.1000/same", "orthorhombic")
    b = replace(_observation("b", "10.1000/same", "monoclinic"), paper_id="paper_alias")
    c = _observation("c", "10.1000/validation", "tetragonal")
    assert find_phase_contradiction_candidates([a, b]) == []
    manifest = build_locked_split_manifest([a, b, c], ["10.1000/validation"])
    by_group = {row["paper_group"]: row["partition"] for row in manifest}
    assert by_group["doi:10.1000/same"] == "train"
    assert by_group["doi:10.1000/validation"] == "locked_validation"
    with pytest.raises(ValueError, match="leakage"):
        assert_no_group_leakage(
            [
                {"paper_group": "doi:10.1000/a", "partition": "train"},
                {"paper_group": "doi:10.1000/a", "partition": "locked_validation"},
            ]
        )


def test_annotation_queue_deduplicates_same_paper_chunk_and_phase() -> None:
    a = replace(
        _observation("a", "10.1000/a", "orthorhombic"),
        phase_evidence_text="XRD identifies the orthorhombic phase.",
        phase_evidence_source="direct",
    )
    b = replace(a, observation_id="b", source_id="fact_b", property_name="coercive_field_Ec")
    queue = build_phase_annotation_queue([a, b], max_rows=None)
    assert len(queue) == 1
    assert queue[0]["adjudication_status"] == "pending_human_annotation"


def test_database_loader_is_read_only_and_preserves_traceability(tmp_path: Path) -> None:
    db_path = tmp_path / "fixture.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE papers (paper_id TEXT PRIMARY KEY, doi TEXT, title TEXT, is_review INTEGER);
        CREATE TABLE document_chunks (chunk_id TEXT PRIMARY KEY, text TEXT);
        CREATE TABLE sample_property_links (
            link_id TEXT, source_kind TEXT, source_id TEXT, paper_id TEXT,
            pdf_id TEXT, chunk_id TEXT, page_number INTEGER, sample_id TEXT,
            material_json TEXT, sample_json TEXT, phase_json TEXT,
            property_json TEXT, evidence_text TEXT, context_quality TEXT,
            context_score REAL, status TEXT
        );
        """
    )
    conn.execute("INSERT INTO papers VALUES (?, ?, ?, ?)", ("p1", "https://doi.org/10.1000/X", "Fixture", 0))
    conn.execute(
        "INSERT INTO document_chunks VALUES (?, ?)",
        ("chunk1", "The Pr is 20 μC/cm². GIXRD identifies the orthorhombic phase."),
    )
    conn.execute(
        "INSERT INTO sample_property_links VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "l1", "reviewed_fact", "f1", "p1", "pdf1", "chunk1", 7, "s1",
            json.dumps({"material_family": "HZO", "zr_fraction": 0.5}),
            json.dumps({"film_thickness_nm": 10, "deposition_method": "ALD"}),
            json.dumps({"phase_name": "orthorhombic", "characterization_method": "XRD"}),
            json.dumps({"property_name": "Pr", "value": 20, "unit": "μC/cm²"}),
            "The Pr is 20 μC/cm².", "strong", 0.9, "linked",
        ),
    )
    conn.commit()
    conn.close()

    observations = load_phase_observations(db_path)
    assert len(observations) == 1
    assert observations[0].paper_group == "doi:10.1000/x"
    assert observations[0].page_number == 7
    assert observations[0].evidence_text.startswith("The Pr")
    assert observations[0].phase_evidence_source == "chunk_recovered"
    assert "orthorhombic" in observations[0].phase_evidence_text
    assert "phase_not_supported_by_evidence_text" not in observations[0].quality_flags
    with open_readonly_database(db_path) as readonly:
        with pytest.raises(sqlite3.OperationalError):
            readonly.execute("INSERT INTO papers VALUES ('p2', '', '', 0)")


def test_pipeline_entrypoint_can_be_executed_directly() -> None:
    project_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(project_root / "pipelines" / "62_analyze_phase_contradictions.py"), "--help"],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "read-only SQLite" in result.stdout
