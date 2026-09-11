from __future__ import annotations

from backend.db.init_db import init_database
from backend.db.session import connect
from backend.services.semantic_vector_store import (
    build_semantic_index,
    search_semantic_index,
    semantic_index_status,
)


def _fake_embed(texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for text in texts:
        lower = text.lower()
        if "hzo" in lower or "orthorhombic" in lower or "2pr" in lower:
            vectors.append([1.0, 0.0, 0.0])
        else:
            vectors.append([0.0, 1.0, 0.0])
    return vectors


def test_semantic_index_builds_resumably_and_returns_evidence(tmp_path):
    db_path = tmp_path / "source.sqlite3"
    index_path = tmp_path / "semantic.sqlite3"
    init_database(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO papers (paper_id, title, doi) VALUES ('paper_1', 'HZO study', '10.1/hzo')"
        )
        conn.execute(
            """
            INSERT INTO pdf_files (
                pdf_id, paper_id, file_name, file_path, sha256, file_size, parse_status
            ) VALUES ('pdf_1', 'paper_1', 'hzo.pdf', 'hzo.pdf', 'sha', 1, 'parsed')
            """
        )
        conn.execute(
            """
            INSERT INTO document_chunks (
                chunk_id, paper_id, pdf_id, page_number, section, chunk_index, text,
                char_count, is_high_value
            ) VALUES (
                'chunk_1', 'paper_1', 'pdf_1', 2, 'results', 0,
                'HZO has an orthorhombic Pca21 phase and a reported 2Pr response.', 65, 1
            )
            """
        )
        conn.commit()

    first = build_semantic_index(
        db_path=db_path,
        index_path=index_path,
        source_types=("chunk",),
        model="fake-embedding",
        dimensions=3,
        embed_batch=_fake_embed,
    )
    second = build_semantic_index(
        db_path=db_path,
        index_path=index_path,
        source_types=("chunk",),
        model="fake-embedding",
        dimensions=3,
        embed_batch=_fake_embed,
    )
    hits = search_semantic_index(
        "HZO 2Pr orthorhombic",
        index_path=index_path,
        model="fake-embedding",
        dimensions=3,
        query_embedder=_fake_embed,
    )

    assert first["indexed_this_run"] == 1
    assert second["indexed_this_run"] == 0
    assert second["skipped_unchanged"] == 1
    assert semantic_index_status(index_path)["rows"] == 1
    assert semantic_index_status(index_path)["ready_for_rag"] is False
    assert hits[0]["source_id"] == "chunk_1"
    assert hits[0]["doi"] == "10.1/hzo"
