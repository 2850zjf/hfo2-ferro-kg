from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.core.config import get_settings


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS papers (
    paper_id TEXT PRIMARY KEY,
    doi TEXT,
    title TEXT,
    authors TEXT,
    journal TEXT,
    year INTEGER,
    publisher TEXT,
    paper_type TEXT DEFAULT 'unknown',
    is_review INTEGER DEFAULT 0,
    source_pdf_id TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pdf_files (
    pdf_id TEXT PRIMARY KEY,
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    page_count INTEGER,
    text_page_count INTEGER DEFAULT 0,
    blank_page_count INTEGER DEFAULT 0,
    low_text_page_count INTEGER DEFAULT 0,
    parse_status TEXT DEFAULT 'pending',
    parse_quality_score REAL,
    ocr_needed INTEGER DEFAULT 0,
    is_duplicate INTEGER DEFAULT 0,
    duplicate_of_pdf_id TEXT,
    paper_id TEXT,
    error_message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(file_path),
    FOREIGN KEY(paper_id) REFERENCES papers(paper_id)
);

CREATE TABLE IF NOT EXISTS parsed_pages (
    page_id TEXT PRIMARY KEY,
    paper_id TEXT,
    pdf_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    text TEXT NOT NULL,
    char_count INTEGER NOT NULL,
    parse_status TEXT DEFAULT 'ok',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(pdf_id, page_number),
    FOREIGN KEY(paper_id) REFERENCES papers(paper_id),
    FOREIGN KEY(pdf_id) REFERENCES pdf_files(pdf_id)
);

CREATE TABLE IF NOT EXISTS document_chunks (
    chunk_id TEXT PRIMARY KEY,
    paper_id TEXT,
    pdf_id TEXT NOT NULL,
    page_number INTEGER,
    section TEXT DEFAULT 'unknown',
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    char_count INTEGER NOT NULL,
    token_count INTEGER,
    contains_table INTEGER DEFAULT 0,
    contains_formula INTEGER DEFAULT 0,
    contains_hfo2_keyword INTEGER DEFAULT 0,
    contains_process_keyword INTEGER DEFAULT 0,
    contains_property_keyword INTEGER DEFAULT 0,
    is_high_value INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(paper_id) REFERENCES papers(paper_id),
    FOREIGN KEY(pdf_id) REFERENCES pdf_files(pdf_id)
);

CREATE TABLE IF NOT EXISTS pdf_tables (
    table_id TEXT PRIMARY KEY,
    paper_id TEXT,
    pdf_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    table_index INTEGER NOT NULL,
    row_count INTEGER NOT NULL,
    col_count INTEGER NOT NULL,
    table_json TEXT NOT NULL,
    table_text TEXT NOT NULL,
    extraction_status TEXT DEFAULT 'ok',
    error_message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(pdf_id, page_number, table_index),
    FOREIGN KEY(paper_id) REFERENCES papers(paper_id),
    FOREIGN KEY(pdf_id) REFERENCES pdf_files(pdf_id)
);

CREATE TABLE IF NOT EXISTS pdf_table_errors (
    error_id TEXT PRIMARY KEY,
    pdf_id TEXT NOT NULL,
    page_number INTEGER,
    error_message TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(pdf_id) REFERENCES pdf_files(pdf_id)
);

CREATE TABLE IF NOT EXISTS extraction_candidates (
    candidate_id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL,
    pdf_id TEXT NOT NULL,
    chunk_id TEXT,
    page_number INTEGER,
    payload_json TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    ontology_version TEXT NOT NULL,
    confidence REAL,
    status TEXT DEFAULT 'pending',
    error_message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reviewed_facts (
    fact_id TEXT PRIMARY KEY,
    candidate_id TEXT,
    paper_id TEXT NOT NULL,
    pdf_id TEXT NOT NULL,
    chunk_id TEXT,
    page_number INTEGER,
    fact_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    review_status TEXT NOT NULL,
    reviewer_notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(candidate_id) REFERENCES extraction_candidates(candidate_id)
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    step_name TEXT NOT NULL,
    status TEXT NOT NULL,
    stats_json TEXT NOT NULL,
    message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


MIGRATIONS = {
    "pdf_files": {
        "blank_page_count": "ALTER TABLE pdf_files ADD COLUMN blank_page_count INTEGER DEFAULT 0",
        "low_text_page_count": "ALTER TABLE pdf_files ADD COLUMN low_text_page_count INTEGER DEFAULT 0",
        "is_duplicate": "ALTER TABLE pdf_files ADD COLUMN is_duplicate INTEGER DEFAULT 0",
        "duplicate_of_pdf_id": "ALTER TABLE pdf_files ADD COLUMN duplicate_of_pdf_id TEXT",
    },
    "document_chunks": {
        "contains_hfo2_keyword": "ALTER TABLE document_chunks ADD COLUMN contains_hfo2_keyword INTEGER DEFAULT 0",
        "contains_process_keyword": "ALTER TABLE document_chunks ADD COLUMN contains_process_keyword INTEGER DEFAULT 0",
        "is_high_value": "ALTER TABLE document_chunks ADD COLUMN is_high_value INTEGER DEFAULT 0",
    },
}


def _index_columns(conn: sqlite3.Connection, table: str, index_name: str) -> list[str]:
    return [row[2] for row in conn.execute(f"PRAGMA index_info({index_name})").fetchall()]


def _pdf_files_needs_rebuild(conn: sqlite3.Connection) -> bool:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='pdf_files'"
    ).fetchall()
    if not rows:
        return False
    for index in conn.execute("PRAGMA index_list(pdf_files)").fetchall():
        index_name = index[1]
        is_unique = bool(index[2])
        if is_unique and _index_columns(conn, "pdf_files", index_name) == ["sha256"]:
            return True
    return False


def _rebuild_pdf_files_table(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE pdf_files RENAME TO pdf_files_old")
    conn.execute(
        """
        CREATE TABLE pdf_files (
            pdf_id TEXT PRIMARY KEY,
            file_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            page_count INTEGER,
            text_page_count INTEGER DEFAULT 0,
            parse_status TEXT DEFAULT 'pending',
            parse_quality_score REAL,
            ocr_needed INTEGER DEFAULT 0,
            is_duplicate INTEGER DEFAULT 0,
            duplicate_of_pdf_id TEXT,
            paper_id TEXT,
            error_message TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(file_path),
            FOREIGN KEY(paper_id) REFERENCES papers(paper_id)
        )
        """
    )
    existing = {
        row[1] for row in conn.execute("PRAGMA table_info(pdf_files_old)").fetchall()
    }
    columns = [
        "pdf_id",
        "file_name",
        "file_path",
        "sha256",
        "file_size",
        "page_count",
        "text_page_count",
        "blank_page_count",
        "low_text_page_count",
        "parse_status",
        "parse_quality_score",
        "ocr_needed",
        "is_duplicate",
        "duplicate_of_pdf_id",
        "paper_id",
        "error_message",
        "created_at",
    ]
    select_parts = []
    for column in columns:
        if column in existing:
            select_parts.append(column)
        elif column in {
            "is_duplicate",
            "ocr_needed",
            "text_page_count",
            "blank_page_count",
            "low_text_page_count",
        }:
            select_parts.append(f"0 AS {column}")
        else:
            select_parts.append(f"NULL AS {column}")
    conn.execute(
        f"""
        INSERT OR IGNORE INTO pdf_files ({", ".join(columns)})
        SELECT {", ".join(select_parts)}
        FROM pdf_files_old
        """
    )
    conn.execute("DROP TABLE pdf_files_old")


def _apply_lightweight_migrations(conn: sqlite3.Connection) -> None:
    if _pdf_files_needs_rebuild(conn):
        _rebuild_pdf_files_table(conn)
    for table, columns in MIGRATIONS.items():
        existing = {
            row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for column, sql in columns.items():
            if column not in existing:
                conn.execute(sql)


def init_database(db_path: str | Path | None = None) -> Path:
    target = Path(db_path) if db_path else get_settings().db_path
    target.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(target) as conn:
        conn.executescript(SCHEMA_SQL)
        _apply_lightweight_migrations(conn)
        conn.commit()

    return target


def list_tables(db_path: str | Path | None = None) -> list[str]:
    target = Path(db_path) if db_path else get_settings().db_path
    with sqlite3.connect(target) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    return [row[0] for row in rows]


if __name__ == "__main__":
    created = init_database()
    print(f"Initialized database: {created}")
