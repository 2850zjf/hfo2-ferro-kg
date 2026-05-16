from __future__ import annotations

import json
import pickle
from pathlib import Path

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.pipeline_log import record_pipeline_run


INDEX_PATH = PROJECT_ROOT / "data" / "vector_index" / "tfidf_index.pkl"


def build_lightweight_index(db_path: Path | None = None) -> dict[str, int]:
    """Build local JSONL and TF-IDF retrieval indexes without external services."""
    output_dir = PROJECT_ROOT / "data" / "vector_index"
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
