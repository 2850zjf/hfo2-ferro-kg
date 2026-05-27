from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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
    table_parse_status TEXT DEFAULT 'pending',
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

CREATE TABLE IF NOT EXISTS rag_jobs (
    job_id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    use_llm INTEGER DEFAULT 1,
    llm_model TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    answer_markdown TEXT,
    error_message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS manual_annotations (
    annotation_id TEXT PRIMARY KEY,
    link_id TEXT NOT NULL,
    paper_id TEXT,
    pdf_id TEXT,
    page_number INTEGER,
    annotation_status TEXT NOT NULL DEFAULT 'unchecked',
    corrected_material_json TEXT NOT NULL,
    corrected_sample_json TEXT NOT NULL,
    corrected_phase_json TEXT NOT NULL,
    corrected_property_json TEXT NOT NULL,
    corrected_evidence_text TEXT,
    corrected_context_quality TEXT,
    reviewer_notes TEXT,
    applied_to_source INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(link_id)
);

CREATE TABLE IF NOT EXISTS ontology_versions (
    ontology_version TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    bundle_path TEXT NOT NULL,
    report_path TEXT NOT NULL,
    entity_count INTEGER NOT NULL,
    relation_count INTEGER NOT NULL,
    property_count INTEGER NOT NULL,
    checksum TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS literature_candidates (
    candidate_id TEXT PRIMARY KEY,
    doi TEXT,
    title TEXT NOT NULL,
    authors TEXT,
    journal TEXT,
    publisher TEXT,
    year INTEGER,
    abstract TEXT,
    source TEXT NOT NULL,
    source_url TEXT,
    oa_status TEXT,
    oa_url TEXT,
    pdf_url TEXT,
    download_status TEXT DEFAULT 'not_downloaded',
    downloaded_pdf_path TEXT,
    downloaded_pdf_sha256 TEXT,
    downloaded_pdf_pages INTEGER,
    downloaded_pdf_size INTEGER,
    download_quality_score REAL,
    download_error TEXT,
    query TEXT,
    match_score REAL DEFAULT 0,
    status TEXT DEFAULT 'discovered',
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS benchmark_extractions (
    extraction_id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL,
    pdf_id TEXT NOT NULL,
    chunk_id TEXT,
    page_number INTEGER,
    source_type TEXT DEFAULT 'text',
    payload_json TEXT NOT NULL,
    model_name TEXT,
    confidence REAL,
    status TEXT DEFAULT 'pending',
    error_message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chunk_id, source_type)
);

CREATE TABLE IF NOT EXISTS pdf_visual_assets (
    asset_id TEXT PRIMARY KEY,
    paper_id TEXT,
    pdf_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    asset_type TEXT NOT NULL,
    asset_index INTEGER NOT NULL,
    file_path TEXT,
    caption_text TEXT,
    bbox_json TEXT,
    width INTEGER,
    height INTEGER,
    extraction_status TEXT DEFAULT 'ok',
    error_message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(pdf_id, page_number, asset_type, asset_index)
);
"""


INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_document_chunks_high_value_ontology_scan
ON document_chunks(is_high_value, pdf_id, chunk_index);

CREATE INDEX IF NOT EXISTS idx_extraction_candidates_chunk_ontology
ON extraction_candidates(chunk_id, ontology_version);

CREATE INDEX IF NOT EXISTS idx_extraction_candidates_ontology_status
ON extraction_candidates(ontology_version, status);

CREATE INDEX IF NOT EXISTS idx_reviewed_facts_status_property
ON reviewed_facts(review_status, fact_type);

CREATE INDEX IF NOT EXISTS idx_manual_annotations_link
ON manual_annotations(link_id);

CREATE INDEX IF NOT EXISTS idx_manual_annotations_status
ON manual_annotations(annotation_status);
"""


MIGRATIONS = {
    "pdf_files": {
        "blank_page_count": "ALTER TABLE pdf_files ADD COLUMN blank_page_count INTEGER DEFAULT 0",
        "low_text_page_count": "ALTER TABLE pdf_files ADD COLUMN low_text_page_count INTEGER DEFAULT 0",
        "is_duplicate": "ALTER TABLE pdf_files ADD COLUMN is_duplicate INTEGER DEFAULT 0",
        "duplicate_of_pdf_id": "ALTER TABLE pdf_files ADD COLUMN duplicate_of_pdf_id TEXT",
        "table_parse_status": "ALTER TABLE pdf_files ADD COLUMN table_parse_status TEXT DEFAULT 'pending'",
    },
    "document_chunks": {
        "contains_hfo2_keyword": "ALTER TABLE document_chunks ADD COLUMN contains_hfo2_keyword INTEGER DEFAULT 0",
        "contains_process_keyword": "ALTER TABLE document_chunks ADD COLUMN contains_process_keyword INTEGER DEFAULT 0",
        "is_high_value": "ALTER TABLE document_chunks ADD COLUMN is_high_value INTEGER DEFAULT 0",
    },
    "literature_candidates": {
        "download_status": "ALTER TABLE literature_candidates ADD COLUMN download_status TEXT DEFAULT 'not_downloaded'",
        "downloaded_pdf_path": "ALTER TABLE literature_candidates ADD COLUMN downloaded_pdf_path TEXT",
        "downloaded_pdf_sha256": "ALTER TABLE literature_candidates ADD COLUMN downloaded_pdf_sha256 TEXT",
        "downloaded_pdf_pages": "ALTER TABLE literature_candidates ADD COLUMN downloaded_pdf_pages INTEGER",
        "downloaded_pdf_size": "ALTER TABLE literature_candidates ADD COLUMN downloaded_pdf_size INTEGER",
        "download_quality_score": "ALTER TABLE literature_candidates ADD COLUMN download_quality_score REAL",
        "download_error": "ALTER TABLE literature_candidates ADD COLUMN download_error TEXT",
    },
    "benchmark_extractions": {
        "source_type": "ALTER TABLE benchmark_extractions ADD COLUMN source_type TEXT DEFAULT 'text'",
        "model_name": "ALTER TABLE benchmark_extractions ADD COLUMN model_name TEXT",
        "confidence": "ALTER TABLE benchmark_extractions ADD COLUMN confidence REAL",
        "status": "ALTER TABLE benchmark_extractions ADD COLUMN status TEXT DEFAULT 'pending'",
        "error_message": "ALTER TABLE benchmark_extractions ADD COLUMN error_message TEXT",
    },
    "pdf_visual_assets": {
        "file_path": "ALTER TABLE pdf_visual_assets ADD COLUMN file_path TEXT",
        "caption_text": "ALTER TABLE pdf_visual_assets ADD COLUMN caption_text TEXT",
        "bbox_json": "ALTER TABLE pdf_visual_assets ADD COLUMN bbox_json TEXT",
        "width": "ALTER TABLE pdf_visual_assets ADD COLUMN width INTEGER",
        "height": "ALTER TABLE pdf_visual_assets ADD COLUMN height INTEGER",
        "extraction_status": "ALTER TABLE pdf_visual_assets ADD COLUMN extraction_status TEXT DEFAULT 'ok'",
        "error_message": "ALTER TABLE pdf_visual_assets ADD COLUMN error_message TEXT",
    },
    "rag_jobs": {
        "use_llm": "ALTER TABLE rag_jobs ADD COLUMN use_llm INTEGER DEFAULT 1",
        "llm_model": "ALTER TABLE rag_jobs ADD COLUMN llm_model TEXT",
        "answer_markdown": "ALTER TABLE rag_jobs ADD COLUMN answer_markdown TEXT",
        "error_message": "ALTER TABLE rag_jobs ADD COLUMN error_message TEXT",
        "started_at": "ALTER TABLE rag_jobs ADD COLUMN started_at TEXT",
        "completed_at": "ALTER TABLE rag_jobs ADD COLUMN completed_at TEXT",
        "updated_at": "ALTER TABLE rag_jobs ADD COLUMN updated_at TEXT DEFAULT CURRENT_TIMESTAMP",
    },
    "manual_annotations": {
        "paper_id": "ALTER TABLE manual_annotations ADD COLUMN paper_id TEXT",
        "pdf_id": "ALTER TABLE manual_annotations ADD COLUMN pdf_id TEXT",
        "page_number": "ALTER TABLE manual_annotations ADD COLUMN page_number INTEGER",
        "corrected_context_quality": "ALTER TABLE manual_annotations ADD COLUMN corrected_context_quality TEXT",
        "applied_to_source": "ALTER TABLE manual_annotations ADD COLUMN applied_to_source INTEGER DEFAULT 0",
        "updated_at": "ALTER TABLE manual_annotations ADD COLUMN updated_at TEXT DEFAULT CURRENT_TIMESTAMP",
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
            blank_page_count INTEGER DEFAULT 0,
            low_text_page_count INTEGER DEFAULT 0,
            parse_status TEXT DEFAULT 'pending',
            parse_quality_score REAL,
            ocr_needed INTEGER DEFAULT 0,
            table_parse_status TEXT DEFAULT 'pending',
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
        "table_parse_status",
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
        elif column == "table_parse_status":
            select_parts.append("'pending' AS table_parse_status")
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
        conn.executescript(INDEX_SQL)
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
