from __future__ import annotations

import json

from backend.db.init_db import init_database
from backend.db.session import connect
from backend.services.manual_annotation_service import (
    get_annotation_item,
    init_manual_annotation_tables,
    list_annotation_items,
    save_annotation,
)


def test_manual_annotation_can_be_saved_and_applied(tmp_path):
    db_path = tmp_path / "manual.sqlite3"
    init_database(db_path)
    init_manual_annotation_tables(db_path=db_path)
    material = {"canonical_name": "HZO", "material_family": "HZO"}
    sample = {"film_thickness_nm": 10, "device_stack": "TiN/HZO/TiN"}
    phase = {"phase_name": "orthorhombic"}
    prop = {
        "property_name": "double_remanent_polarization_2Pr",
        "value": 30,
        "unit": "uC/cm2",
        "evidence_text": "2Pr reached 30 uC/cm2.",
    }
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO papers (paper_id, title, doi) VALUES (?, ?, ?)",
            ("paper_1", "Manual annotation paper", "10.1/manual"),
        )
        conn.execute(
            """
            INSERT INTO sample_property_links (
                link_id, source_kind, source_id, paper_id, pdf_id, page_number,
                sample_id, material_json, sample_json, phase_json, property_json,
                evidence_text, variable_roles_json, context_quality, context_score,
                linkage_method, linker_version, llm_usage_json, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "link_manual",
                "reviewed_fact",
                "fact_1",
                "paper_1",
                "pdf_1",
                3,
                "sample_1",
                json.dumps(material),
                json.dumps(sample),
                json.dumps(phase),
                json.dumps(prop),
                "2Pr reached 30 uC/cm2.",
                "{}",
                "partial",
                0.7,
                "test",
                "test",
                "{}",
                "linked",
            ),
        )
        conn.commit()

    rows = list_annotation_items(db_path=db_path)
    assert rows[0]["link_id"] == "link_manual"
    assert rows[0]["manual_status"] == "unchecked"

    prop["value"] = 32
    prop["normalized_value"] = 32
    saved = save_annotation(
        link_id="link_manual",
        annotation_status="fixed",
        material=material,
        sample=sample,
        phase=phase,
        prop=prop,
        evidence_text="2Pr reached 32 uC/cm2.",
        context_quality="strong",
        reviewer_notes="Corrected value from source sentence.",
        apply_to_source=True,
        db_path=db_path,
    )
    assert saved["annotation_status"] == "fixed"
    item = get_annotation_item("link_manual", db_path=db_path)
    assert item is not None
    assert item["property"]["value"] == 32
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT property_json, context_quality FROM sample_property_links WHERE link_id=?",
            ("link_manual",),
        ).fetchone()
    assert json.loads(row["property_json"])["value"] == 32
    assert row["context_quality"] == "strong"
