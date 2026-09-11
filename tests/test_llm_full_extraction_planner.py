from __future__ import annotations

from backend.db.init_db import init_database
from backend.db.session import connect
from backend.services.llm_full_extraction_planner import build_full_llm_extraction_plan


def test_build_full_llm_extraction_plan_exports_callable_queue(tmp_path):
    db_path = tmp_path / "planner.sqlite3"
    init_database(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO papers (paper_id, title, doi, year, paper_type) VALUES (?, ?, ?, ?, ?)",
            ("paper_1", "HZO oxygen vacancy interface paper", "10.1/hzo", 2026, "experimental"),
        )
        text = "Hf0.5Zr0.5O2 TiN interface oxygen vacancy 2Pr orthorhombic ALD anneal."
        conn.execute(
            """
            INSERT INTO document_chunks (
                chunk_id, paper_id, pdf_id, chunk_index, page_number, text,
                char_count, token_count, is_high_value
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "chunk_1",
                "paper_1",
                "pdf_1",
                1,
                2,
                text,
                len(text),
                len(text.split()),
                1,
            ),
        )
        conn.commit()

    stats = build_full_llm_extraction_plan(output_dir=tmp_path, db_path=db_path)

    assert stats["chunks"] == 1
    assert stats["papers"] == 1
    assert "--chunk-list" in stats["dry_run_command"]
    assert (tmp_path / "full_hzo_llm_high_value_chunks.csv").exists()
