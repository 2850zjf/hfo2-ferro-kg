from __future__ import annotations

import sqlite3

from backend.db.init_db import init_database, list_tables


def test_init_database_creates_core_tables(tmp_path):
    db_path = tmp_path / "hfo2_ferrokg.sqlite3"

    init_database(db_path)

    tables = set(list_tables(db_path))
    assert "papers" in tables
    assert "pdf_files" in tables
    assert "parsed_pages" in tables
    assert "document_chunks" in tables
    assert "extraction_candidates" in tables
    assert "ontology_versions" in tables
    assert "pipeline_runs" in tables
    assert "reviewed_facts" in tables


def test_pdf_files_requires_core_fields(tmp_path):
    db_path = tmp_path / "hfo2_ferrokg.sqlite3"
    init_database(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO pdf_files (pdf_id, file_name, file_path, sha256, file_size)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("pdf-1", "example.pdf", "data/raw_pdfs/example.pdf", "abc", 123),
        )
        row = conn.execute(
            "SELECT parse_status, ocr_needed FROM pdf_files WHERE pdf_id = ?",
            ("pdf-1",),
        ).fetchone()

    assert row == ("pending", 0)
