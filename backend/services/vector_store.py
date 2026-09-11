from __future__ import annotations

import json
import os
import pickle
from pathlib import Path

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.pipeline_log import record_pipeline_run


INDEX_PATH = PROJECT_ROOT / "data" / "vector_index" / "tfidf_index.pkl"


def build_lightweight_index(db_path: Path | None = None) -> dict[str, int]:
    """Build local JSONL and TF-IDF retrieval indexes without external services."""
    output_dir = INDEX_PATH.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "keyword_index.jsonl"
    count = 0
    documents: list[str] = []
    metadata: list[dict[str, object]] = []
    with connect(db_path) as conn, output_path.open("w", encoding="utf-8") as fh:
        rows = conn.execute(
            """
            SELECT dc.chunk_id, dc.paper_id, dc.pdf_id, dc.page_number, dc.section, dc.text,
                   p.title, p.doi
            FROM document_chunks dc
            LEFT JOIN papers p ON p.paper_id = dc.paper_id
            WHERE is_high_value = 1
            ORDER BY dc.pdf_id, dc.chunk_index
            """
        ).fetchall()
        for row in rows:
            record = dict(row)
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            documents.append(row["text"])
            metadata.append(
                {
                    "chunk_id": row["chunk_id"],
                    "paper_id": row["paper_id"],
                    "pdf_id": row["pdf_id"],
                    "page_number": row["page_number"],
                    "section": row["section"],
                    "title": row["title"],
                    "doi": row["doi"],
                    "text": row["text"],
                }
            )
            count += 1

    if documents:
        from sklearn.feature_extraction.text import TfidfVectorizer

        vectorizer = TfidfVectorizer(
            lowercase=True,
            ngram_range=(1, 2),
            max_features=50000,
            token_pattern=r"(?u)\b[\w\.\-/]+\b",
        )
        matrix = vectorizer.fit_transform(documents)
        with INDEX_PATH.open("wb") as fh:
            pickle.dump({"vectorizer": vectorizer, "matrix": matrix, "metadata": metadata}, fh)

    stats = {"indexed_chunks": count, "tfidf_index": int(bool(documents))}
    record_pipeline_run("08_build_vector_index", "ok", stats, db_path=db_path)
    return stats


def search_vector_index(query: str, limit: int = 8) -> list[dict[str, object]]:
    """Search the semantic index with a deterministic TF-IDF fallback.

    `HFO2_FERROKG_VECTOR_MODE` accepts `hybrid`, `semantic`, or `tfidf`.
    """
    mode = os.getenv("HFO2_FERROKG_VECTOR_MODE", "hybrid").strip().lower()
    tfidf_hits = _search_tfidf_index(query, max(limit * 2, limit)) if mode != "semantic" else []
    semantic_hits: list[dict[str, object]] = []
    if mode in {"hybrid", "semantic"}:
        try:
            from backend.services.semantic_vector_store import (
                search_semantic_index,
                semantic_index_status,
            )

            status = semantic_index_status()
            allow_partial = os.getenv(
                "HFO2_FERROKG_ALLOW_PARTIAL_SEMANTIC_INDEX", "false"
            ).lower() in {"1", "true", "yes", "on"}
            if status.get("available") and (status.get("ready_for_rag") or allow_partial):
                semantic_hits = search_semantic_index(query, limit=max(limit * 2, limit))
        except Exception:
            semantic_hits = []
    if mode == "semantic":
        return semantic_hits[:limit]
    if not semantic_hits:
        return tfidf_hits[:limit]
    return _reciprocal_rank_merge(semantic_hits, tfidf_hits, limit=limit)


def _search_tfidf_index(query: str, limit: int = 8) -> list[dict[str, object]]:
    if not INDEX_PATH.exists():
        return []

    from sklearn.metrics.pairwise import cosine_similarity

    with INDEX_PATH.open("rb") as fh:
        index = pickle.load(fh)
    vectorizer = index["vectorizer"]
    matrix = index["matrix"]
    metadata = index["metadata"]

    query_vector = vectorizer.transform([query])
    scores = cosine_similarity(query_vector, matrix).ravel()
    ranked = scores.argsort()[::-1][:limit]
    hits: list[dict[str, object]] = []
    for position in ranked:
        score = float(scores[position])
        if score <= 0:
            continue
        hit = dict(metadata[position])
        hit["score"] = score
        hits.append(hit)
    return hits


def _reciprocal_rank_merge(
    semantic_hits: list[dict[str, object]],
    tfidf_hits: list[dict[str, object]],
    limit: int,
) -> list[dict[str, object]]:
    merged: dict[str, dict[str, object]] = {}
    for weight, hits in ((0.7, semantic_hits), (0.3, tfidf_hits)):
        for rank, raw in enumerate(hits, start=1):
            hit = dict(raw)
            source_type = str(hit.get("source_type") or "chunk")
            source_id = str(hit.get("source_id") or hit.get("chunk_id") or "")
            if not source_id:
                continue
            key = f"{source_type}:{source_id}"
            current = merged.setdefault(key, hit)
            current["hybrid_score"] = float(current.get("hybrid_score") or 0.0) + weight / (60 + rank)
            if source_type == "chunk" and not current.get("chunk_id"):
                current["chunk_id"] = source_id
    ranked = sorted(merged.values(), key=lambda item: float(item.get("hybrid_score") or 0.0), reverse=True)
    for hit in ranked:
        hit["score"] = float(hit.get("hybrid_score") or hit.get("score") or 0.0)
    return ranked[:limit]
