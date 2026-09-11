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
    caption_asset_id TEXT,
    bbox_json TEXT,
    width INTEGER,
    height INTEGER,
    extraction_status TEXT DEFAULT 'ok',
    error_message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(pdf_id, page_number, asset_type, asset_index)
);

CREATE TABLE IF NOT EXISTS pdf_asset_links (
    link_id TEXT PRIMARY KEY,
    paper_id TEXT,
    pdf_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    image_asset_id TEXT NOT NULL,
    caption_asset_id TEXT NOT NULL,
    relation_type TEXT NOT NULL DEFAULT 'caption_of',
    geometry_distance REAL,
    confidence REAL NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(image_asset_id, caption_asset_id)
);

CREATE TABLE IF NOT EXISTS pdf_equations (
    equation_id TEXT PRIMARY KEY,
    paper_id TEXT,
    pdf_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    equation_index INTEGER NOT NULL,
    equation_label TEXT,
    raw_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    latex_text TEXT,
    variables_json TEXT NOT NULL DEFAULT '[]',
    candidate_kind TEXT NOT NULL DEFAULT 'display_equation',
    equation_role TEXT DEFAULT 'unknown',
    bbox_json TEXT,
    image_path TEXT,
    confidence REAL,
    extraction_method TEXT NOT NULL,
    extraction_status TEXT DEFAULT 'candidate',
    error_message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(pdf_id, page_number, equation_index)
);

CREATE TABLE IF NOT EXISTS pdf_equation_scans (
    pdf_id TEXT PRIMARY KEY,
    equation_count INTEGER NOT NULL DEFAULT 0,
    scan_status TEXT NOT NULL,
    error_message TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS multimodal_extractions (
    extraction_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    paper_id TEXT,
    pdf_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    model_name TEXT NOT NULL,
    ontology_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending_review',
    confidence REAL,
    error_message TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_type, source_id, model_name, ontology_version)
);

CREATE TABLE IF NOT EXISTS multimodal_extraction_runs (
    run_id TEXT PRIMARY KEY,
    extraction_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    paper_id TEXT,
    pdf_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    model_name TEXT NOT NULL,
    ontology_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    run_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence REAL,
    error_message TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS multimodal_asset_queue (
    queue_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    paper_id TEXT,
    pdf_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    file_path TEXT,
    context_text TEXT NOT NULL,
    asset_quality_score REAL NOT NULL DEFAULT 0.5,
    quality_reason TEXT,
    priority_score REAL NOT NULL,
    priority_tier TEXT NOT NULL,
    queue_status TEXT NOT NULL,
    content_hash TEXT,
    duplicate_of_source_id TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_type, source_id)
);

CREATE TABLE IF NOT EXISTS multimodal_evidence (
    evidence_id TEXT PRIMARY KEY,
    extraction_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    paper_id TEXT,
    pdf_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    observation_type TEXT NOT NULL,
    description TEXT NOT NULL,
    property_name TEXT,
    raw_value_text TEXT,
    value REAL,
    value_min REAL,
    value_max REAL,
    unit TEXT,
    material_ref TEXT,
    sample_ref TEXT,
    series_or_panel TEXT,
    condition_text TEXT,
    value_origin TEXT NOT NULL DEFAULT 'unclear',
    evidence_scope TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence_text TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'needs_human_review',
    benchmark_tier TEXT NOT NULL DEFAULT 'all_traceable',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(extraction_id) REFERENCES multimodal_extractions(extraction_id)
);

CREATE TABLE IF NOT EXISTS computation_jobs (
    job_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    candidate_id TEXT,
    task_family TEXT NOT NULL,
    engine TEXT NOT NULL,
    objective TEXT,
    priority_score REAL,
    status TEXT NOT NULL DEFAULT 'prepared',
    execution_mode TEXT NOT NULL DEFAULT 'prepare_only',
    safety_status TEXT NOT NULL DEFAULT 'prepared_no_execution',
    work_dir TEXT NOT NULL,
    input_manifest_json TEXT NOT NULL,
    cloud_payload_json TEXT,
    result_json TEXT,
    error_message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    submitted_at TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS computation_results (
    result_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    task_id TEXT,
    result_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    quality_status TEXT NOT NULL,
    source_path TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(job_id) REFERENCES computation_jobs(job_id)
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

CREATE INDEX IF NOT EXISTS idx_computation_jobs_status_family
ON computation_jobs(status, task_family);

CREATE INDEX IF NOT EXISTS idx_computation_results_job
ON computation_results(job_id);

CREATE INDEX IF NOT EXISTS idx_pdf_equations_pdf_page
ON pdf_equations(pdf_id, page_number);

CREATE INDEX IF NOT EXISTS idx_pdf_asset_links_image
ON pdf_asset_links(image_asset_id, confidence DESC);

CREATE INDEX IF NOT EXISTS idx_multimodal_extractions_source
ON multimodal_extractions(source_type, source_id, status);

CREATE INDEX IF NOT EXISTS idx_multimodal_extraction_runs_source
ON multimodal_extraction_runs(source_type, source_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_multimodal_asset_queue_priority
ON multimodal_asset_queue(queue_status, priority_tier, priority_score DESC);

CREATE INDEX IF NOT EXISTS idx_multimodal_evidence_pdf_page
ON multimodal_evidence(pdf_id, page_number, observation_type);
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
        "caption_asset_id": "ALTER TABLE pdf_visual_assets ADD COLUMN caption_asset_id TEXT",
        "bbox_json": "ALTER TABLE pdf_visual_assets ADD COLUMN bbox_json TEXT",
        "width": "ALTER TABLE pdf_visual_assets ADD COLUMN width INTEGER",
        "height": "ALTER TABLE pdf_visual_assets ADD COLUMN height INTEGER",
        "extraction_status": "ALTER TABLE pdf_visual_assets ADD COLUMN extraction_status TEXT DEFAULT 'ok'",
        "error_message": "ALTER TABLE pdf_visual_assets ADD COLUMN error_message TEXT",
    },
    "pdf_equations": {
        "candidate_kind": "ALTER TABLE pdf_equations ADD COLUMN candidate_kind TEXT NOT NULL DEFAULT 'display_equation'",
    },
    "multimodal_asset_queue": {
        "asset_quality_score": "ALTER TABLE multimodal_asset_queue ADD COLUMN asset_quality_score REAL NOT NULL DEFAULT 0.5",
        "quality_reason": "ALTER TABLE multimodal_asset_queue ADD COLUMN quality_reason TEXT",
    },
    "multimodal_evidence": {
        "value_origin": "ALTER TABLE multimodal_evidence ADD COLUMN value_origin TEXT NOT NULL DEFAULT 'unclear'",
        "benchmark_tier": "ALTER TABLE multimodal_evidence ADD COLUMN benchmark_tier TEXT NOT NULL DEFAULT 'all_traceable'",
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

    with sqlite3.connect(target, timeout=60) as conn:
        conn.execute("PRAGMA busy_timeout = 60000")
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
