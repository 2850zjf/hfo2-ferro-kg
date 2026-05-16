from __future__ import annotations

import json

import pandas as pd

from backend.db.init_db import init_database
from backend.db.session import connect
from backend.services.graph_builder import build_graph


def test_build_graph_exports_property_evidence_chain(tmp_path):
    db_path = tmp_path / "kg.sqlite3"
    out_dir = tmp_path / "graph"
    init_database(db_path)
    payload = {
        "material": {"canonical_name": "Hf0.5Zr0.5O2", "material_family": "HZO"},
        "sample": {"device_stack": "TiN/HZO/TiN", "film_thickness_nm": 10},
        "property": {
            "property_name": "double_remanent_polarization_2Pr",
            "value": 40,
            "unit": "μC/cm²",
            "normalized_value": 40,
            "normalized_unit": "μC/cm²",
            "evidence_text": "The TiN/HZO/TiN capacitor showed 2Pr of 40 μC/cm².",
        },
        "phases": [{"phase_name": "orthorhombic", "space_group": "Pca21"}],
        "devices": [],
        "preaudit": {"extraction_source": "llm", "confidence": 0.8},
    }
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO papers (paper_id, title, doi, year) VALUES (?, ?, ?, ?)",
            ("paper_1", "HZO paper", "10.1/test", 2025),
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

    stats = build_graph(output_dir=out_dir, db_path=db_path)

    edges = pd.read_csv(out_dir / "edges.csv")
    assert stats["nodes"] >= 5
    assert "SUPPORTED_BY" in set(edges["type"])
    assert "FROM_PAPER" in set(edges["type"])
