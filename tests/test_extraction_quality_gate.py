from __future__ import annotations

from backend.db.init_db import init_database
from backend.db.session import connect
from backend.services.extraction_quality_gate import apply_excluded_candidate_gate


def test_quality_gate_previews_then_removes_only_selected_ontology(tmp_path):
    db_path = tmp_path / "quality.sqlite3"
    csv_path = tmp_path / "excluded.csv"
    csv_path.write_text("chunk_id\nchunk_bio\n", encoding="utf-8")
    init_database(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO extraction_candidates (
                candidate_id, paper_id, pdf_id, chunk_id, page_number, payload_json,
                extractor_version, ontology_version, confidence, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("cand_v23", "paper", "pdf", "chunk_bio", 1, "{}", "test", "hfo2-ferrokg-v2.3", 0.5, "needs_human_review"),
        )
        conn.execute(
            """
            INSERT INTO extraction_candidates (
                candidate_id, paper_id, pdf_id, chunk_id, page_number, payload_json,
                extractor_version, ontology_version, confidence, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("cand_v1", "paper", "pdf", "chunk_bio", 1, "{}", "test", "hfo2-ferrokg-v1", 0.5, "needs_human_review"),
        )
        conn.execute(
            """
            INSERT INTO reviewed_facts (
                fact_id, candidate_id, paper_id, pdf_id, chunk_id, page_number,
                fact_type, payload_json, review_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("fact_v23", "cand_v23", "paper", "pdf", "chunk_bio", 1, "application_claim", "{}", "needs_human_review"),
        )
        conn.commit()

    preview = apply_excluded_candidate_gate(csv_path, apply=False, db_path=db_path)
    applied = apply_excluded_candidate_gate(csv_path, apply=True, db_path=db_path)

    assert preview["candidate_rows_matched"] == 1
    assert preview["reviewed_fact_rows_matched"] == 1
    assert applied["applied"] is True
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM extraction_candidates WHERE candidate_id='cand_v23'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM reviewed_facts WHERE fact_id='fact_v23'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM extraction_candidates WHERE candidate_id='cand_v1'").fetchone()[0] == 1
