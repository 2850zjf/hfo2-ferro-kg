from __future__ import annotations

import json

from backend.core.config import get_settings
from backend.db.init_db import init_database
from backend.db.session import connect
from backend.services.quality_validator import validate_local_results


def test_validate_local_results_flags_hfo2_domain_anomaly(tmp_path):
    db_path = tmp_path / "quality.sqlite3"
    init_database(db_path)
    payload = {
        "material": {"canonical_name": "HfO2", "material_family": "HfO2"},
        "sample": {},
        "property": {
            "property_name": "remanent_polarization_Pr",
            "value": 124.5,
            "unit": "μC/cm²",
            "normalized_value": 124.5,
            "normalized_unit": "μC/cm²",
            "evidence_text": "A Pr of 124.5 μC/cm² was reported.",
        },
    }
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO pdf_files (pdf_id, file_name, file_path, sha256, file_size, paper_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "pdf_1",
                "a.pdf",
                str(get_settings().pdf_root / "a.pdf"),
                "abc",
                10,
                "paper_1",
            ),
        )
        conn.execute(
            """
            INSERT INTO reviewed_facts (
                fact_id, paper_id, pdf_id, chunk_id, page_number, fact_type,
                payload_json, review_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "fact_1",
                "paper_1",
                "pdf_1",
                "chunk_1",
                2,
                "ferroelectric_property",
                json.dumps(payload),
                "preapproved_machine",
            ),
        )
        conn.commit()

    report = validate_local_results(db_path=db_path)

    assert report["domain_anomalies"][0]["fact_id"] == "fact_1"
    assert report["missing_evidence_fact_ids"] == []
    assert report["quality_gate"]["publication_ready"] is False
