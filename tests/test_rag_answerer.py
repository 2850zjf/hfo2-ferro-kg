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


def test_rag_range_answer_reports_evidence_tiers(tmp_path):
    db_path = tmp_path / "rag_tiers.sqlite3"
    init_database(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO papers (paper_id, title, doi) VALUES (?, ?, ?)",
            ("paper_1", "HZO tier paper", "10.1/tier"),
        )
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
                linker_version TEXT NOT NULL,
                llm_usage_json TEXT,
                status TEXT DEFAULT 'linked'
            )
            """
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
                "link_1",
                "reviewed_fact",
                "fact_1",
                "paper_1",
                "pdf_1",
                5,
                "sample_1",
                json.dumps({"canonical_name": "Hf0.5Zr0.5O2", "material_family": "HZO"}),
                json.dumps({"film_thickness_nm": 10, "device_stack": "TiN/HZO/TiN"}),
                json.dumps({"phase_name": "orthorhombic"}),
                json.dumps(
                    {
                        "property_name": "double_remanent_polarization_2Pr",
                        "value": 30,
                        "unit": "μC/cm²",
                        "evidence_text": "2Pr reached 30 μC/cm².",
                    }
                ),
                "2Pr reached 30 μC/cm².",
                "{}",
                "strong",
                0.9,
                "test",
                "test",
                "{}",
                "linked",
            ),
        )
        conn.commit()

    answer = answer_question("HZO 的 2Pr 范围是多少？", db_path=db_path)

    assert "strong_only" in answer
    assert "fact_id：link_1" in answer
    assert "10.1/tier" in answer
