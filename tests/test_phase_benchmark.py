from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.services.phase_benchmark import (
    BenchmarkThresholds,
    assert_zero_group_leakage,
    build_phase_benchmark,
    build_validation_selection_frame,
    parse_review_phase_labels,
)


ANNOTATION_FIELDS = [
    "annotation_id",
    "paper_group",
    "doi",
    "paper_id",
    "title",
    "pdf_id",
    "page_number",
    "chunk_id",
    "material_system",
    "material_name",
    "zr_fraction",
    "characterization_method",
    "phase_evidence_text",
    "phase_evidence_source",
    "review_phase_label",
    "review_phase_scope",
    "review_sample_conditions",
    "review_source_type",
    "adjudication_status",
    "annotator",
    "review_notes",
]


def _annotation(annotation_id: str, doi: str, **updates: str) -> dict[str, str]:
    row = {
        "annotation_id": annotation_id,
        "paper_group": f"doi:{doi}",
        "doi": doi,
        "paper_id": f"paper_{annotation_id}",
        "title": f"Paper {annotation_id}",
        "pdf_id": f"pdf_{annotation_id}",
        "page_number": "3",
        "chunk_id": f"chunk_{annotation_id}",
        "material_system": "HZO",
        "material_name": "Hf0.5Zr0.5O2",
        "zr_fraction": "0.5",
        "characterization_method": "GIXRD",
        "phase_evidence_text": "GIXRD identifies m, o, and t phase contributions.",
        "phase_evidence_source": "direct",
        "review_phase_label": "m+o+t",
        "review_phase_scope": "mixed_unquantified",
        "review_sample_conditions": json.dumps({"film_thickness_nm": 10, "annealing_temperature_c": 500}),
        "review_source_type": "primary_experiment",
        "adjudication_status": "adjudicated",
        "annotator": "annotator_1",
        "review_notes": "fixture",
    }
    row.update(updates)
    return row


def _write_csv(path: Path, rows: list[dict[str, str]], fields: list[str] = ANNOTATION_FIELDS) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_phase_label_parser_rejects_unresolved_or_foreign_labels() -> None:
    assert parse_review_phase_labels("o+t") == ("o", "t")
    assert parse_review_phase_labels("unresolved") == ()
    assert parse_review_phase_labels("o+c") == ()


def test_pending_queue_produces_not_ready_report_without_metrics(tmp_path: Path) -> None:
    annotations = tmp_path / "annotations.csv"
    row = _annotation("a", "10.1000/a", adjudication_status="pending_human_annotation")
    _write_csv(annotations, [row])
    summary = build_phase_benchmark(annotations, tmp_path / "out")
    assert summary["status"] == "not_ready"
    assert summary["accepted_rows"] == 0
    assert summary["metrics_status"] == "not_computed"
    assert summary["exclusion_reason_counts"]["not_adjudicated"] == 1
    assert "locked_validation_groups_provided" in summary["blockers"]


def test_adjudicated_rows_are_split_only_by_whole_doi_group(tmp_path: Path) -> None:
    annotations = tmp_path / "annotations.csv"
    _write_csv(
        annotations,
        [
            _annotation("a1", "10.1000/train"),
            _annotation("a2", "10.1000/train", chunk_id="chunk_train_2"),
            _annotation("b1", "10.1000/validation"),
        ],
    )
    summary = build_phase_benchmark(
        annotations,
        tmp_path / "out",
        locked_validation_groups=["10.1000/validation"],
        thresholds=BenchmarkThresholds(
            min_total_paper_groups=2,
            min_train_groups_per_phase=1,
            min_validation_groups_per_phase=1,
        ),
    )
    assert summary["status"] == "ready_for_training"
    assert summary["train_paper_groups"] == 1
    assert summary["locked_validation_paper_groups"] == 1
    with Path(summary["train_path"]).open(encoding="utf-8-sig", newline="") as handle:
        train = list(csv.DictReader(handle))
    with Path(summary["locked_validation_path"]).open(encoding="utf-8-sig", newline="") as handle:
        validation = list(csv.DictReader(handle))
    assert {row["paper_group"] for row in train} == {"doi:10.1000/train"}
    assert {row["paper_group"] for row in validation} == {"doi:10.1000/validation"}


def test_review_computation_and_unstructured_conditions_are_excluded(tmp_path: Path) -> None:
    annotations = tmp_path / "annotations.csv"
    _write_csv(
        annotations,
        [
            _annotation("review", "10.1000/review", review_source_type="review_summary"),
            _annotation(
                "computed",
                "10.1000/computed",
                review_source_type="primary_computation",
                review_phase_scope="computed_structure",
            ),
            _annotation("bad_json", "10.1000/json", review_sample_conditions="10 nm, 500 C"),
        ],
    )
    summary = build_phase_benchmark(annotations, tmp_path / "out")
    assert summary["accepted_rows"] == 0
    assert summary["exclusion_reason_counts"]["not_primary_experiment"] == 2
    assert summary["exclusion_reason_counts"]["review_sample_conditions_must_be_json_object"] == 1


def test_group_leakage_guard_rejects_manual_partition_collision() -> None:
    with pytest.raises(ValueError, match="leakage"):
        assert_zero_group_leakage(
            [
                {"paper_group": "doi:10.1000/a", "partition": "train"},
                {"paper_group": "doi:10.1000/a", "partition": "locked_validation"},
            ]
        )


def test_selection_frame_never_assigns_a_partition(tmp_path: Path) -> None:
    inventory = tmp_path / "inventory.csv"
    fields = [
        "paper_group", "doi", "paper_id", "title", "material_systems", "observations",
        "m_observations", "o_observations", "t_observations",
        "quality_gate_pass_observations", "is_review",
    ]
    _write_csv(
        inventory,
        [
            {
                "paper_group": "doi:10.1000/a", "doi": "10.1000/a", "paper_id": "p1",
                "title": "A", "material_systems": "HZO", "observations": "10",
                "m_observations": "1", "o_observations": "4", "t_observations": "2",
                "quality_gate_pass_observations": "7", "is_review": "0",
            }
        ],
        fields,
    )
    rows = build_validation_selection_frame(inventory)
    assert rows[0]["phase_stratum"] == "m+o+t"
    assert rows[0]["selection_status"] == "human_selection_required"
    assert rows[0]["selected_partition"] == ""


def test_pipeline_entrypoint_can_be_executed_directly() -> None:
    project_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(project_root / "pipelines" / "63_build_phase_benchmark.py"), "--help"],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "human-adjudicated" in result.stdout

