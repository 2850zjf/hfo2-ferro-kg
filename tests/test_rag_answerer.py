from __future__ import annotations

import json

from backend.db.init_db import init_database
from backend.db.session import connect
from backend.services.rag_answerer import answer_question


def test_rag_answer_uses_evidence_and_does_not_invent(tmp_path):
    db_path = tmp_path / "rag.sqlite3"
    init_database(db_path)
    payload = {
        "material": {"canonical_name": "Hf0.5Zr0.5O2", "material_family": "HZO"},
        "sample": {},
        "property": {
            "property_name": "double_remanent_polarization_2Pr",
            "value": 40,
            "unit": "μC/cm²",
            "evidence_text": "The HZO capacitor showed 2Pr of 40 μC/cm² after cycling.",
        },
        "preaudit": {"extraction_source": "llm", "confidence": 0.8},
    }
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO papers (paper_id, title, doi) VALUES (?, ?, ?)",
            ("paper_1", "HZO evidence paper", "10.1/hzo"),
        )
        conn.execute(
            """
            INSERT INTO reviewed_facts (
                fact_id, paper_id, pdf_id, page_number, fact_type, payload_json, review_status
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
                "preapproved_machine",
            ),
        )
        conn.commit()

    answer = answer_question("HZO 的 2Pr 是多少？", db_path=db_path)
    no_answer = answer_question("La 掺杂 HfO2 的退火温度是多少？", db_path=db_path)

    assert "10.1/hzo" in answer
    assert "2Pr" in answer
    assert "当前数据库没有足够证据" in no_answer
