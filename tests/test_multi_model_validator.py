from __future__ import annotations

import json

from backend.db.init_db import init_database
from backend.db.session import connect
from backend.services.multi_model_validator import run_multi_model_validation


def test_multi_model_validation_reports_agreement_accuracy(tmp_path):
    db_path = tmp_path / "model_validation.sqlite3"
    init_database(db_path)
    payload = {
        "material": {"canonical_name": "Hf0.5Zr0.5O2", "material_family": "HZO"},
        "sample": {"device_stack": "TiN/HZO/TiN", "film_thickness_nm": 10},
        "property": {
            "property_name": "double_remanent_polarization_2Pr",
            "value": 40,
            "unit": "uC/cm2",
            "normalized_value": 40,
            "normalized_unit": "μC/cm²",
            "evidence_text": "The TiN/HZO/TiN capacitor showed 2Pr of 40 uC/cm2 after wake-up.",
        },
        "ontology_context": {
            "context_quality": "strong",
            "context_score": 0.9,
            "missing_context_labels": [],
        },
    }
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO papers (paper_id, title, doi) VALUES (?, ?, ?)",
            ("paper_1", "HZO validation paper", "10.1/hzo"),
        )
        conn.execute(
            """
            INSERT INTO reviewed_facts (
                fact_id, paper_id, pdf_id, page_number, fact_type,
                payload_json, review_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "fact_1",
                "paper_1",
                "pdf_1",
                4,
                "ferroelectric_property",
                json.dumps(payload),
                "needs_human_review",
            ),
        )
        conn.commit()

    stats = run_multi_model_validation(limit=10, db_path=db_path)

    assert stats["facts"] == 1
    assert stats["models"] == 4
    names = {row["model_name"] for row in stats["summary"]}
    assert {"evidence_consistency", "ontology_relation", "domain_range", "ensemble"} <= names
    assert all("agreement_accuracy" in row for row in stats["summary"])
