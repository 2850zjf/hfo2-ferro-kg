from __future__ import annotations

import csv
import json

from backend.db.init_db import init_database
from backend.db.session import connect
from backend.services.paper_prototype_eval import evaluate_paper_prototype


def _insert_sample_link(db_path):
    init_database(db_path)
    material = {"canonical_name": "Hf0.5Zr0.5O2", "material_family": "HZO"}
    sample = {
        "film_thickness_nm": 10,
        "deposition_method": "ALD",
        "annealing_temperature_c": 500,
        "annealing_time_s": 30,
        "annealing_atmosphere": "N2",
        "electrode_stack": "TiN/HZO/TiN",
        "device_type": "capacitor",
    }
    phase = {"phase_name": "orthorhombic"}
    prop = {
        "property_name": "double_remanent_polarization_2Pr",
        "normalized_value": 40,
        "normalized_unit": "uC/cm2",
        "evidence_text": "The 10 nm HZO capacitor showed 2Pr of 40 uC/cm2.",
    }
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE sample_property_links (
                link_id TEXT PRIMARY KEY,
                source_kind TEXT NOT NULL,
                source_id TEXT NOT NULL,
                paper_id TEXT NOT NULL,
                pdf_id TEXT NOT NULL,
                chunk_id TEXT,
                page_number INTEGER,
                sample_id TEXT NOT NULL,
                material_json TEXT NOT NULL,
                sample_json TEXT NOT NULL,
                phase_json TEXT NOT NULL,
                property_json TEXT NOT NULL,
                evidence_text TEXT,
                variable_roles_json TEXT NOT NULL,
                context_quality TEXT NOT NULL,
                context_score REAL NOT NULL,
                linkage_method TEXT NOT NULL,
                linker_version TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO papers (paper_id, title, doi, year, paper_type) VALUES (?, ?, ?, ?, ?)",
            ("paper_1", "HZO paper", "10.1000/hzo", 2026, "experimental"),
        )
        conn.execute(
            """
            INSERT INTO sample_property_links (
                link_id, source_kind, source_id, paper_id, pdf_id, chunk_id,
                page_number, sample_id, material_json, sample_json, phase_json,
                property_json, evidence_text, variable_roles_json, context_quality,
                context_score, linkage_method, linker_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "link_1",
                "reviewed_fact",
                "fact_1",
                "paper_1",
                "pdf_1",
                "chunk_1",
                3,
                "sample_1",
                json.dumps(material),
                json.dumps(sample),
                json.dumps(phase),
                json.dumps(prop),
                prop["evidence_text"],
                "{}",
                "strong",
                0.9,
                "rules",
                "test",
            ),
        )
        conn.commit()


def test_evaluate_paper_prototype_creates_template(tmp_path):
    db_path = tmp_path / "eval.sqlite3"
    _insert_sample_link(db_path)
    gold_path = tmp_path / "gold.csv"

    stats = evaluate_paper_prototype(
        gold_set_path=gold_path,
        output_dir=tmp_path,
        sample_size=1,
        db_path=db_path,
    )

    assert stats["status"] == "template_created"
    assert stats["rows"] == 1
    with gold_path.open("r", encoding="utf-8-sig", newline="") as fh:
        row = next(csv.DictReader(fh))
    assert row["link_id"] == "link_1"
    assert row["pred_target_property"] == "double_remanent_polarization_2Pr"
    assert row["gold_target_property"] == ""


def test_evaluate_paper_prototype_scores_gold_rows(tmp_path):
    db_path = tmp_path / "eval.sqlite3"
    _insert_sample_link(db_path)
    gold_path = tmp_path / "gold.csv"
    evaluate_paper_prototype(gold_set_path=gold_path, output_dir=tmp_path, sample_size=1, db_path=db_path)

    with gold_path.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    row = rows[0]
    row["gold_status"] = "correct"
    for key, value in list(row.items()):
        if key.startswith("pred_"):
            row[key.replace("pred_", "gold_", 1)] = value
    with gold_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(row.keys()))
        writer.writeheader()
        writer.writerows(rows)

    stats = evaluate_paper_prototype(
        gold_set_path=gold_path,
        output_dir=tmp_path,
        sample_size=1,
        db_path=db_path,
    )

    assert stats["status"] == "ok"
    assert stats["annotated_rows"] == 1
    assert stats["field_f1"] == 1.0
    assert stats["sample_property_linking_accuracy"] == 1.0
    assert (tmp_path / "paper_prototype_eval_summary.json").exists()
    assert (tmp_path / "paper_prototype_eval_details.csv").exists()
    assert (tmp_path / "paper_prototype_eval_report.md").exists()
