from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from backend.services.phase_benchmark import BenchmarkThresholds
from backend.services.phase_competition_workflow import run_phase_competition_workflow


def _database(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE papers (
            paper_id TEXT PRIMARY KEY, doi TEXT, title TEXT, is_review INTEGER
        );
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
    for index, (doi, phase) in enumerate(
        [("10.1000/train", "orthorhombic"), ("10.1000/validation", "monoclinic")],
        start=1,
    ):
        paper_id = f"p{index}"
        chunk_id = f"c{index}"
        conn.execute(
            "INSERT INTO papers VALUES (?, ?, ?, ?)",
            (paper_id, doi, f"Paper {index}", 0),
        )
        conn.execute(
            "INSERT INTO document_chunks VALUES (?, ?)",
            (
                chunk_id,
                f"A 10 nm-thick HZO film was deposited by ALD and annealed at 500 °C for 30 s in N2. "
                f"GIXRD identifies the {phase} phase.",
            ),
        )
        conn.execute(
            "INSERT INTO sample_property_links VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"l{index}",
                "reviewed_fact",
                f"f{index}",
                paper_id,
                f"pdf{index}",
                chunk_id,
                3,
                f"s{index}",
                json.dumps({"material_family": "HZO", "zr_fraction": 0.5}),
                json.dumps(
                    {
                        "film_thickness_nm": 10,
                        "deposition_method": "ALD",
                        "annealing_temperature_c": 500,
                        "annealing_time_s": 30,
                        "annealing_atmosphere": "N2",
                    }
                ),
                json.dumps({"phase_name": phase, "characterization_method": "GIXRD"}),
                json.dumps({"property_name": "phase", "value": phase}),
                f"GIXRD identifies the {phase} phase.",
                "strong",
                0.9,
                "linked",
            ),
        )
    conn.commit()
    conn.close()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _adjudicated_annotations(path: Path) -> None:
    fields = [
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
    rows = []
    for index, doi in enumerate(["10.1000/train", "10.1000/validation"], start=1):
        rows.append(
            {
                "annotation_id": f"a{index}",
                "paper_group": f"doi:{doi}",
                "doi": doi,
                "paper_id": f"p{index}",
                "title": f"Paper {index}",
                "pdf_id": f"pdf{index}",
                "page_number": "3",
                "chunk_id": f"c{index}",
                "material_system": "HZO",
                "material_name": "Hf0.5Zr0.5O2",
                "zr_fraction": "0.5",
                "characterization_method": "GIXRD",
                "phase_evidence_text": "GIXRD resolves m, o, and t phase contributions.",
                "phase_evidence_source": "direct",
                "review_phase_label": "m+o+t",
                "review_phase_scope": "mixed_unquantified",
                "review_sample_conditions": json.dumps({"film_thickness_nm": 10}),
                "review_source_type": "primary_experiment",
                "adjudication_status": "adjudicated",
                "annotator": "human_fixture",
                "review_notes": "synthetic workflow fixture",
            }
        )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_real_gate_path_completes_without_mutating_source_database(tmp_path: Path) -> None:
    db_path = tmp_path / "source.sqlite3"
    _database(db_path)
    before = _digest(db_path)
    summary = run_phase_competition_workflow(
        db_path,
        tmp_path / "workflow",
        max_candidates=10,
        max_annotation_rows=10,
    )
    assert _digest(db_path) == before
    assert summary["status"] == "completed_waiting_for_human_annotation"
    assert summary["api_credentials_used"] is False
    assert summary["model_trained"] is False
    assert summary["vasp_invoked"] is False
    assert summary["stages"]["candidate_analysis"]["status"] == "completed"
    assert summary["stages"]["annotation_queue"]["rows"] == 2
    assert summary["stages"]["benchmark_readiness"]["accepted_rows"] == 0
    assert Path(summary["summary_path"]).is_file()


def test_adjudicated_locked_path_can_reach_ready_state(tmp_path: Path) -> None:
    db_path = tmp_path / "source.sqlite3"
    annotations = tmp_path / "adjudicated.csv"
    _database(db_path)
    _adjudicated_annotations(annotations)
    summary = run_phase_competition_workflow(
        db_path,
        tmp_path / "workflow",
        annotations_path=annotations,
        locked_validation_groups=["10.1000/validation"],
        max_candidates=10,
        max_annotation_rows=10,
        thresholds=BenchmarkThresholds(
            min_total_paper_groups=2,
            min_train_groups_per_phase=1,
            min_validation_groups_per_phase=1,
        ),
    )
    assert summary["status"] == "ready_for_training"
    assert summary["stages"]["benchmark_readiness"]["accepted_rows"] == 2
    assert summary["locked_validation_groups_provided"] == 1
    assert summary["model_trained"] is False


def test_cli_entrypoint_can_be_executed_directly() -> None:
    project_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(project_root / "pipelines" / "64_run_phase_competition_workflow.py"), "--help"],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "phase-competition" in result.stdout
