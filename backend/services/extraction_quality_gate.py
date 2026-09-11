from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.pipeline_log import record_pipeline_run


def apply_excluded_candidate_gate(
    excluded_csv: Path | None = None,
    ontology_version: str = "hfo2-ferrokg-v2.3",
    apply: bool = False,
    db_path: Path | None = None,
) -> dict[str, Any]:
    path = excluded_csv or (
        PROJECT_ROOT / "data" / "extraction_queues" / "publication_v3" / "publication_excluded_chunks.csv"
    )
    with path.open(encoding="utf-8-sig") as fh:
        chunk_ids = sorted(
            {
                str(row.get("chunk_id") or "").strip()
                for row in csv.DictReader(fh)
                if str(row.get("chunk_id") or "").strip()
            }
        )

    with connect(db_path) as conn:
        conn.execute("DROP TABLE IF EXISTS temp.quality_gate_excluded_chunks")
        conn.execute("CREATE TEMP TABLE quality_gate_excluded_chunks (chunk_id TEXT PRIMARY KEY)")
        conn.executemany(
            "INSERT INTO quality_gate_excluded_chunks (chunk_id) VALUES (?)",
            ((chunk_id,) for chunk_id in chunk_ids),
        )
        candidates = conn.execute(
            """
            SELECT COUNT(*)
            FROM extraction_candidates ec
            JOIN quality_gate_excluded_chunks qg ON qg.chunk_id = ec.chunk_id
            WHERE ec.ontology_version = ?
            """,
            (ontology_version,),
        ).fetchone()[0]
        facts = conn.execute(
            """
            SELECT COUNT(*)
            FROM reviewed_facts rf
            JOIN extraction_candidates ec ON ec.candidate_id = rf.candidate_id
            JOIN quality_gate_excluded_chunks qg ON qg.chunk_id = ec.chunk_id
            WHERE ec.ontology_version = ?
            """,
            (ontology_version,),
        ).fetchone()[0]
        if apply:
            conn.execute(
                """
                DELETE FROM reviewed_facts
                WHERE candidate_id IN (
                    SELECT ec.candidate_id
                    FROM extraction_candidates ec
                    JOIN quality_gate_excluded_chunks qg ON qg.chunk_id = ec.chunk_id
                    WHERE ec.ontology_version = ?
                )
                """,
                (ontology_version,),
            )
            conn.execute(
                """
                DELETE FROM extraction_candidates
                WHERE ontology_version = ?
                  AND chunk_id IN (SELECT chunk_id FROM quality_gate_excluded_chunks)
                """,
                (ontology_version,),
            )
            conn.commit()

    stats = {
        "ontology_version": ontology_version,
        "excluded_chunks": len(chunk_ids),
        "candidate_rows_matched": int(candidates),
        "reviewed_fact_rows_matched": int(facts),
        "applied": bool(apply),
        "excluded_csv": str(path),
    }
    if apply:
        record_pipeline_run("53_apply_extraction_quality_gate", "ok", stats, db_path=db_path)
    return stats


def reset_retryable_candidates(
    chunk_list: Path,
    ontology_version: str = "hfo2-ferrokg-v2.3",
    apply: bool = False,
    db_path: Path | None = None,
) -> dict[str, Any]:
    with chunk_list.open(encoding="utf-8-sig") as fh:
        chunk_ids = sorted(
            {
                str(row.get("chunk_id") or "").strip()
                for row in csv.DictReader(fh)
                if str(row.get("chunk_id") or "").strip()
            }
        )
    with connect(db_path) as conn:
        conn.execute("DROP TABLE IF EXISTS temp.retry_chunk_ids")
        conn.execute("CREATE TEMP TABLE retry_chunk_ids (chunk_id TEXT PRIMARY KEY)")
        conn.executemany("INSERT INTO retry_chunk_ids (chunk_id) VALUES (?)", ((value,) for value in chunk_ids))
        candidate_ids = [
            row[0]
            for row in conn.execute(
                """
                SELECT ec.candidate_id
                FROM extraction_candidates ec
                JOIN retry_chunk_ids rq ON rq.chunk_id = ec.chunk_id
                WHERE ec.ontology_version = ? AND ec.status = 'extraction_error'
                """,
                (ontology_version,),
            ).fetchall()
        ]
        if apply and candidate_ids:
            conn.executemany("DELETE FROM reviewed_facts WHERE candidate_id = ?", ((value,) for value in candidate_ids))
            conn.executemany("DELETE FROM extraction_candidates WHERE candidate_id = ?", ((value,) for value in candidate_ids))
            conn.commit()
    stats = {
        "ontology_version": ontology_version,
        "queue_chunks": len(chunk_ids),
        "retryable_candidates": len(candidate_ids),
        "applied": bool(apply),
        "chunk_list": str(chunk_list),
    }
    if apply:
        record_pipeline_run("54_prepare_extraction_retry", "ok", stats, db_path=db_path)
    return stats
