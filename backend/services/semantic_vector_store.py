from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np

from backend.core.config import PROJECT_ROOT, get_llm_api_key, get_settings
from backend.db.session import connect
from backend.services.llm_extractor import normalize_base_url, non_evidence_reason
from backend.services.llm_quota_guard import is_llm_budget_error
from backend.services.pipeline_log import record_pipeline_run


SEMANTIC_DB_PATH = PROJECT_ROOT / "data" / "vector_index" / "semantic_index.sqlite3"
DEFAULT_EMBEDDING_MODEL = "text-embedding-v4"
DEFAULT_DIMENSIONS = 1024
DEFAULT_SOURCE_TYPES = ("chunk", "table", "figure_caption", "equation", "multimodal_evidence")
EmbedBatch = Callable[[list[str]], list[list[float]]]


def init_semantic_store(index_path: Path | None = None) -> Path:
    path = index_path or SEMANTIC_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS semantic_documents (
                doc_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                paper_id TEXT,
                pdf_id TEXT,
                page_number INTEGER,
                title TEXT,
                doi TEXT,
                section TEXT,
                evidence_tier TEXT NOT NULL DEFAULT 'all_traceable',
                source_status TEXT,
                text TEXT NOT NULL,
                text_sha256 TEXT NOT NULL,
                model TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                vector BLOB NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source_type, source_id, model, dimensions)
            );
            CREATE INDEX IF NOT EXISTS idx_semantic_documents_model
            ON semantic_documents(model, dimensions, source_type);
            CREATE TABLE IF NOT EXISTS semantic_index_meta (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
    return path


def collect_semantic_documents(
    db_path: Path | None = None,
    source_types: Iterable[str] = DEFAULT_SOURCE_TYPES,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    selected = set(source_types)
    documents: list[dict[str, Any]] = []
    with connect(db_path) as conn:
        if "chunk" in selected:
            has_relevance = _has_table(conn, "literature_relevance_screenings")
            relevance_join = (
                "LEFT JOIN literature_relevance_screenings lrs ON lrs.pdf_id = dc.pdf_id"
                if has_relevance
                else ""
            )
            relevance_filter = (
                "AND COALESCE(lrs.final_tier, 'core') != 'irrelevant' "
                "AND COALESCE(lrs.extraction_policy, 'benchmark_full') != 'exclude'"
                if has_relevance
                else ""
            )
            rows = conn.execute(
                f"""
                SELECT dc.chunk_id AS source_id, dc.paper_id, dc.pdf_id, dc.page_number,
                       dc.section, dc.text, p.title, p.doi
                FROM document_chunks dc
                JOIN pdf_files pf ON pf.pdf_id = dc.pdf_id
                LEFT JOIN papers p ON p.paper_id = dc.paper_id
                {relevance_join}
                WHERE dc.is_high_value = 1
                  AND pf.parse_status = 'parsed'
                  AND COALESCE(pf.is_duplicate, 0) = 0
                  {relevance_filter}
                ORDER BY dc.pdf_id, dc.chunk_index
                """
            ).fetchall()
            for row in rows:
                text = str(row["text"] or "").strip()
                if len(text) < 40 or non_evidence_reason(text):
                    continue
                documents.append(_document_record("chunk", row, text, evidence_tier="all_traceable"))

        if "table" in selected:
            rows = conn.execute(
                """
                SELECT pt.table_id AS source_id, pt.paper_id, pt.pdf_id, pt.page_number,
                       'table' AS section, pt.table_text AS text, pt.extraction_status AS source_status,
                       p.title, p.doi
                FROM pdf_tables pt LEFT JOIN papers p ON p.paper_id = pt.paper_id
                WHERE pt.extraction_status = 'ok' AND length(trim(pt.table_text)) >= 20
                ORDER BY pt.pdf_id, pt.page_number, pt.table_index
                """
            ).fetchall()
            documents.extend(
                _document_record("table", row, f"[TABLE]\n{row['text']}", evidence_tier="strong_partial")
                for row in rows
            )

        if "figure_caption" in selected:
            rows = conn.execute(
                """
                SELECT asset_id AS source_id, paper_id, pdf_id, page_number,
                       'figure_caption' AS section, caption_text AS text,
                       extraction_status AS source_status
                FROM pdf_visual_assets
                WHERE asset_type = 'figure_caption_geometry'
                  AND length(trim(COALESCE(caption_text, ''))) >= 20
                ORDER BY pdf_id, page_number, asset_index
                """
            ).fetchall()
            paper_meta = _paper_metadata(conn, {str(row["paper_id"]) for row in rows if row["paper_id"]})
            for row in rows:
                merged = dict(row)
                merged.update(paper_meta.get(str(row["paper_id"]), {}))
                documents.append(
                    _document_record(
                        "figure_caption",
                        merged,
                        f"[FIGURE CAPTION]\n{row['text']}",
                        evidence_tier="strong_partial",
                    )
                )

        if "equation" in selected:
            rows = conn.execute(
                """
                SELECT pe.equation_id AS source_id, pe.paper_id, pe.pdf_id, pe.page_number,
                       'equation' AS section,
                       COALESCE(NULLIF(pe.latex_text, ''), pe.normalized_text) AS text,
                       pe.extraction_status AS source_status, p.title, p.doi
                FROM pdf_equations pe LEFT JOIN papers p ON p.paper_id = pe.paper_id
                WHERE pe.extraction_status != 'rejected_non_equation'
                  AND length(trim(COALESCE(NULLIF(pe.latex_text, ''), pe.normalized_text))) >= 5
                ORDER BY pe.pdf_id, pe.page_number, pe.equation_index
                """
            ).fetchall()
            documents.extend(
                _document_record(
                    "equation",
                    row,
                    f"[EQUATION CANDIDATE]\n{row['text']}",
                    evidence_tier=("strong_only" if row["source_status"] == "semantic_candidate" else "all_traceable"),
                )
                for row in rows
            )

        if "multimodal_evidence" in selected:
            rows = conn.execute(
                """
                SELECT me.evidence_id AS source_id, me.paper_id, me.pdf_id, me.page_number,
                       me.source_type AS section, me.benchmark_tier AS evidence_tier,
                       me.review_status AS source_status, p.title, p.doi,
                       trim(
                           COALESCE(me.property_name || ': ', '') ||
                           COALESCE(me.raw_value_text || ' ', '') ||
                           me.description || '\nEvidence: ' || me.evidence_text
                       ) AS text
                FROM multimodal_evidence me LEFT JOIN papers p ON p.paper_id = me.paper_id
                ORDER BY me.pdf_id, me.page_number, me.evidence_id
                """
            ).fetchall()
            documents.extend(
                _document_record(
                    "multimodal_evidence",
                    row,
                    f"[MULTIMODAL EVIDENCE]\n{row['text']}",
                    evidence_tier=str(row["evidence_tier"]),
                )
                for row in rows
            )

    documents.sort(key=lambda item: (item["source_type"], item["pdf_id"] or "", item["page_number"] or 0, item["source_id"]))
    return documents[: max(0, limit)] if limit is not None else documents


def build_semantic_index(
    db_path: Path | None = None,
    index_path: Path | None = None,
    source_types: Iterable[str] = DEFAULT_SOURCE_TYPES,
    limit: int | None = None,
    batch_size: int = 10,
    model: str = DEFAULT_EMBEDDING_MODEL,
    dimensions: int = DEFAULT_DIMENSIONS,
    embed_batch: EmbedBatch | None = None,
) -> dict[str, Any]:
    index = init_semantic_store(index_path)
    documents = collect_semantic_documents(db_path=db_path, source_types=source_types, limit=limit)
    batch_size = max(1, min(10, int(batch_size)))
    dimensions = int(dimensions)
    embed = embed_batch or _dashscope_embedder(model=model, dimensions=dimensions)
    indexed = skipped = errors = 0
    error_message = ""
    with sqlite3.connect(index) as store:
        existing = {
            row[0]: row[1]
            for row in store.execute(
                "SELECT doc_id, text_sha256 FROM semantic_documents WHERE model=? AND dimensions=?",
                (model, dimensions),
            )
        }
        pending = [doc for doc in documents if existing.get(doc["doc_id"]) != doc["text_sha256"]]
        skipped = len(documents) - len(pending)
        for start in range(0, len(pending), batch_size):
            batch = pending[start : start + batch_size]
            try:
                vectors = embed([str(doc["text"]) for doc in batch])
                if len(vectors) != len(batch):
                    raise ValueError(f"Embedding response count mismatch: {len(vectors)} != {len(batch)}")
                for doc, vector in zip(batch, vectors):
                    normalized = _normalized_vector(vector, dimensions)
                    store.execute(
                        """
                        INSERT INTO semantic_documents (
                            doc_id, source_type, source_id, paper_id, pdf_id, page_number,
                            title, doi, section, evidence_tier, source_status, text,
                            text_sha256, model, dimensions, vector, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(doc_id) DO UPDATE SET
                            text=excluded.text, text_sha256=excluded.text_sha256,
                            evidence_tier=excluded.evidence_tier, source_status=excluded.source_status,
                            model=excluded.model, dimensions=excluded.dimensions,
                            vector=excluded.vector, updated_at=CURRENT_TIMESTAMP
                        """,
                        (
                            doc["doc_id"], doc["source_type"], doc["source_id"], doc["paper_id"],
                            doc["pdf_id"], doc["page_number"], doc["title"], doc["doi"],
                            doc["section"], doc["evidence_tier"], doc["source_status"], doc["text"],
                            doc["text_sha256"], model, dimensions, normalized.tobytes(),
                        ),
                    )
                    indexed += 1
                store.commit()
            except Exception as exc:
                errors += len(batch)
                error_message = str(exc)
                if is_llm_budget_error(error_message):
                    break
                if embed_batch is not None:
                    raise
                time.sleep(1.0)
                break
        total_rows = store.execute(
            "SELECT COUNT(*) FROM semantic_documents WHERE model=? AND dimensions=?",
            (model, dimensions),
        ).fetchone()[0]
        meta = {
            "model": model,
            "dimensions": dimensions,
            "source_types": sorted(set(source_types)),
            "selected_documents": len(documents),
            "indexed_this_run": indexed,
            "skipped_unchanged": skipped,
            "errors": errors,
            "total_index_rows": total_rows,
            "error_message": error_message,
            "ready_for_rag": bool(
                limit is None
                and set(source_types) == set(DEFAULT_SOURCE_TYPES)
                and errors == 0
                and indexed + skipped == len(documents)
            ),
        }
        store.execute(
            """
            INSERT INTO semantic_index_meta(key, value_json, updated_at)
            VALUES ('latest_build', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=CURRENT_TIMESTAMP
            """,
            (json.dumps(meta, ensure_ascii=False),),
        )
        store.commit()
    record_pipeline_run(
        "61_build_semantic_vector_index",
        "ok" if not errors else "partial",
        {**meta, "index_path": str(index)},
        db_path=db_path,
    )
    return {**meta, "index_path": str(index)}


def search_semantic_index(
    query: str,
    limit: int = 8,
    index_path: Path | None = None,
    model: str = DEFAULT_EMBEDDING_MODEL,
    dimensions: int = DEFAULT_DIMENSIONS,
    query_embedder: EmbedBatch | None = None,
) -> list[dict[str, Any]]:
    path = index_path or SEMANTIC_DB_PATH
    if not path.exists() or not query.strip():
        return []
    embed = query_embedder or _dashscope_embedder(model=model, dimensions=dimensions)
    query_vector = _normalized_vector(embed([query])[0], dimensions)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT doc_id, source_type, source_id, paper_id, pdf_id, page_number,
                   title, doi, section, evidence_tier, source_status, text, vector
            FROM semantic_documents WHERE model=? AND dimensions=?
            """,
            (model, dimensions),
        ).fetchall()
    if not rows:
        return []
    matrix = np.vstack([np.frombuffer(row["vector"], dtype=np.float32) for row in rows])
    scores = matrix @ query_vector
    ranked = np.argsort(scores)[::-1][: max(1, int(limit))]
    hits: list[dict[str, Any]] = []
    for position in ranked:
        score = float(scores[position])
        if score <= 0:
            continue
        row = rows[int(position)]
        hit = {key: row[key] for key in row.keys() if key != "vector"}
        hit["score"] = score
        hits.append(hit)
    return hits


def semantic_index_status(index_path: Path | None = None) -> dict[str, Any]:
    path = index_path or SEMANTIC_DB_PATH
    if not path.exists():
        return {"available": False, "rows": 0, "index_path": str(path)}
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT COUNT(*) FROM semantic_documents").fetchone()[0]
        meta_row = conn.execute(
            "SELECT value_json FROM semantic_index_meta WHERE key='latest_build'"
        ).fetchone()
    meta = json.loads(meta_row[0]) if meta_row else {}
    return {
        "available": bool(rows),
        "ready_for_rag": bool(meta.get("ready_for_rag")),
        "rows": rows,
        "index_path": str(path),
        "latest_build": meta,
    }


def _dashscope_embedder(model: str, dimensions: int) -> EmbedBatch:
    api_key = get_llm_api_key()
    if not api_key:
        raise RuntimeError("LLM API key is not configured for semantic embeddings")
    from openai import OpenAI

    settings = get_settings()
    base_url, _ = normalize_base_url(settings.llm_provider, settings.llm_base_url)
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=settings.llm_timeout_seconds)

    def embed(texts: list[str]) -> list[list[float]]:
        response = client.embeddings.create(
            model=model,
            input=texts,
            dimensions=dimensions,
            encoding_format="float",
        )
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]

    return embed


def _document_record(
    source_type: str,
    row: Any,
    text: str,
    evidence_tier: str,
) -> dict[str, Any]:
    raw = dict(row)
    source_id = str(raw["source_id"])
    normalized_text = " ".join(str(text).split())
    return {
        "doc_id": f"{source_type}:{source_id}",
        "source_type": source_type,
        "source_id": source_id,
        "paper_id": raw.get("paper_id"),
        "pdf_id": raw.get("pdf_id"),
        "page_number": raw.get("page_number"),
        "title": raw.get("title"),
        "doi": raw.get("doi"),
        "section": raw.get("section"),
        "evidence_tier": evidence_tier,
        "source_status": raw.get("source_status"),
        "text": normalized_text,
        "text_sha256": hashlib.sha256(normalized_text.encode("utf-8")).hexdigest(),
    }


def _paper_metadata(conn: Any, paper_ids: set[str]) -> dict[str, dict[str, Any]]:
    if not paper_ids:
        return {}
    metadata: dict[str, dict[str, Any]] = {}
    values = sorted(paper_ids)
    for start in range(0, len(values), 800):
        batch = values[start : start + 800]
        marks = ",".join("?" for _ in batch)
        rows = conn.execute(
            f"SELECT paper_id, title, doi FROM papers WHERE paper_id IN ({marks})",
            batch,
        ).fetchall()
        metadata.update({str(row["paper_id"]): {"title": row["title"], "doi": row["doi"]} for row in rows})
    return metadata


def _has_table(conn: Any, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _normalized_vector(vector: Any, dimensions: int) -> np.ndarray:
    array = np.asarray(vector, dtype=np.float32)
    if array.ndim != 1 or array.shape[0] != dimensions:
        raise ValueError(f"Expected a {dimensions}-dimensional embedding, got {array.shape}")
    norm = float(np.linalg.norm(array))
    if norm <= 0:
        raise ValueError("Embedding vector has zero norm")
    return array / norm
