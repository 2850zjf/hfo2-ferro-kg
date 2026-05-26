from __future__ import annotations

import json

from backend.db.init_db import init_database
from backend.db.session import connect
from backend.services.rag_job_service import create_rag_job, run_rag_job


def test_rag_job_persists_answer(tmp_path):
    db_path = tmp_path / "rag_job.sqlite3"
    init_database(db_path)
    payload = {
        "material": {"canonical_name": "Hf0.5Zr0.5O2", "material_family": "HZO"},
        "property": {
            "property_name": "double_remanent_polarization_2Pr",
            "value": 25,
            "unit": "uC/cm2",
            "evidence_text": "The measured 2Pr was 25 uC/cm2.",
        },
    }
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO papers (paper_id, title, doi) VALUES (?, ?, ?)",
            ("paper_job", "Persistent RAG paper", "10.1/job"),
        )
        conn.execute(
            """
            INSERT INTO reviewed_facts (
                fact_id, paper_id, pdf_id, page_number, fact_type, payload_json, review_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "fact_job",
                "paper_job",
                "pdf_job",
                2,
                "ferroelectric_property",
                json.dumps(payload),
                "approved",
            ),
        )
        conn.commit()

    job = create_rag_job("HZO 2Pr", use_llm=False, db_path=db_path)
    finished = run_rag_job(job["job_id"], db_path=db_path)

    assert finished["status"] == "completed"
    assert "10.1/job" in finished["answer_markdown"]
