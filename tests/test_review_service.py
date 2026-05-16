from __future__ import annotations

import json

import pytest

from backend.db.init_db import init_database
from backend.db.session import connect
from backend.services.review_service import (
    export_approved_facts,
    list_review_facts,
    update_review_status,
)


def test_review_service_updates_status_and_exports_approved(tmp_path):
    db_path = tmp_path / "review.sqlite3"
    init_database(db_path)
    payload = {
        "material": {"canonical_name": "Hf0.5Zr0.5O2", "material_family": "HZO"},
        "sample": {"device_stack": "TiN/HZO/TiN"},
        "property": {
            "property_name": "double_remanent_polarization_2Pr",
            "raw_property_name": "2Pr",
            "value": 40,
            "unit": "μC/cm²",
            "normalized_value": 40,
            "normalized_unit": "μC/cm²",
            "confidence": 0.8,
            "evidence_text": "The 2Pr value was 40 μC/cm².",
        },
    }
    with connect(db_path) as conn:
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
                4,
                "ferroelectric_property",
                json.dumps(payload),
                "needs_human_review",
            ),
        )
        conn.commit()

    update_review_status("fact_1", "approved", "checked against source", db_path=db_path)
    facts = list_review_facts(status="approved", db_path=db_path)
    export_path = export_approved_facts(tmp_path / "approved.csv", db_path=db_path)

    assert facts[0]["review_status"] == "approved"
    assert facts[0]["property_name"] == "double_remanent_polarization_2Pr"
    assert "checked against source" in export_path.read_text(encoding="utf-8-sig")


def test_review_service_rejects_unknown_status(tmp_path):
    db_path = tmp_path / "review.sqlite3"
    init_database(db_path)

    with pytest.raises(ValueError):
        update_review_status("missing", "published", db_path=db_path)
