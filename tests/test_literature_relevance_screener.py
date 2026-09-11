from __future__ import annotations

from backend.db.init_db import init_database
from backend.db.session import connect
from backend.services.literature_relevance_screener import screen_literature_relevance
from backend.services.llm_full_extraction_planner import build_full_llm_extraction_plan


def test_screening_marks_core_hzo_and_planner_filters_core(tmp_path):
    db_path = tmp_path / "screening.sqlite3"
    init_database(db_path)
    text = (
        "The 10 nm Hf0.5Zr0.5O2 film was deposited by ALD on TiN and annealed at 500 C. "
        "The orthorhombic phase shows ferroelectric 2Pr of 40 uC/cm2."
    )
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO papers (paper_id, title, doi, year, paper_type) VALUES (?, ?, ?, ?, ?)",
            ("paper_1", "HZO ferroelectric capacitor", "10.1/hzo", 2025, "experimental"),
        )
        conn.execute(
            """
            INSERT INTO pdf_files (
                pdf_id, file_name, file_path, sha256, file_size, parse_status, is_duplicate, paper_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("pdf_1", "hzo.pdf", "/tmp/hzo.pdf", "abc", 100, "parsed", 0, "paper_1"),
        )
        conn.execute(
            """
            INSERT INTO parsed_pages (page_id, paper_id, pdf_id, page_number, text, char_count)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("page_1", "paper_1", "pdf_1", 1, text, len(text)),
        )
        conn.execute(
            """
            INSERT INTO document_chunks (
                chunk_id, paper_id, pdf_id, chunk_index, page_number, text,
                char_count, token_count, contains_hfo2_keyword,
                contains_process_keyword, contains_property_keyword, is_high_value
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("chunk_1", "paper_1", "pdf_1", 1, 1, text, len(text), len(text.split()), 1, 1, 1, 1),
        )
        conn.commit()

    stats = screen_literature_relevance(llm_mode="off", reset=True, db_path=db_path, output_dir=tmp_path)

    assert stats["tier_counts"]["core"] == 1
    plan = build_full_llm_extraction_plan(output_dir=tmp_path, relevance_tiers=["core"], db_path=db_path)
    assert plan["chunks"] == 1
    assert plan["relevance_tiers"] == ["core"]
