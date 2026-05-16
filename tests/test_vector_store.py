from __future__ import annotations

from backend.db.init_db import init_database
from backend.db.session import connect
from backend.services.vector_store import build_lightweight_index, search_vector_index


def test_tfidf_vector_index_returns_relevant_chunk(tmp_path, monkeypatch):
    db_path = tmp_path / "vector.sqlite3"
    index_path = tmp_path / "tfidf_index.pkl"
    monkeypatch.setattr("backend.services.vector_store.INDEX_PATH", index_path)
    init_database(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO papers (paper_id, title, doi) VALUES (?, ?, ?)",
            ("paper_1", "HZO paper", "10.1/hzo"),
        )
        conn.execute(
            """
            INSERT INTO document_chunks (
                chunk_id, paper_id, pdf_id, page_number, section, chunk_index, text,
                char_count, token_count, contains_hfo2_keyword, contains_property_keyword,
                is_high_value
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "chunk_1",
                "paper_1",
                "pdf_1",
                2,
                "results",
                0,
                "Hf0.5Zr0.5O2 showed orthorhombic Pca21 phase and 2Pr of 40 uC/cm2.",
                76,
                10,
                1,
                1,
                1,
            ),
        )
        conn.commit()

    stats = build_lightweight_index(db_path=db_path)
    hits = search_vector_index("HZO 2Pr orthorhombic")

    assert stats["indexed_chunks"] == 1
    assert hits
    assert hits[0]["chunk_id"] == "chunk_1"
