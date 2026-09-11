from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.config import PROJECT_ROOT, get_settings


def prepare_llm_retry_queue(
    source_queue: Path,
    output_path: Path,
    db_path: Path | None = None,
    limit_rows: int | None = None,
) -> dict[str, object]:
    target_db = db_path or get_settings().db_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with source_queue.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise ValueError(f"No CSV header found in {source_queue}")
        rows = list(reader)
        fieldnames = reader.fieldnames

    conn = sqlite3.connect(target_db, timeout=60)
    try:
        llm_chunk_ids = {
            row[0]
            for row in conn.execute(
                """
                SELECT DISTINCT chunk_id
                FROM extraction_candidates
                WHERE payload_json LIKE '%"extraction_source": "llm"%'
                """
            ).fetchall()
            if row[0]
        }
        candidate_chunk_ids = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT chunk_id FROM extraction_candidates WHERE chunk_id IS NOT NULL"
            ).fetchall()
            if row[0]
        }
    finally:
        conn.close()

    retry_rows: list[dict[str, str]] = []
    missing_candidate = 0
    fallback_candidate = 0
    for row in rows:
        chunk_id = row["chunk_id"]
        if chunk_id in llm_chunk_ids:
            continue
        if chunk_id in candidate_chunk_ids:
            fallback_candidate += 1
        else:
            missing_candidate += 1
        retry_rows.append(row)
        if limit_rows is not None and len(retry_rows) >= limit_rows:
            break

    with output_path.open("w", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(retry_rows)

    manifest = {
        "source_queue": str(source_queue),
        "output_path": str(output_path),
        "source_rows": len(rows),
        "retry_rows": len(retry_rows),
        "missing_candidate_rows": missing_candidate,
        "fallback_candidate_rows": fallback_candidate,
        "llm_success_rows": len(rows) - len(retry_rows),
        "limit_rows": limit_rows,
    }
    manifest_path = output_path.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a retry queue for core chunks without successful LLM extraction.")
    parser.add_argument(
        "--source-queue",
        type=Path,
        default=PROJECT_ROOT / "data" / "extraction_queues" / "full_hzo_llm_high_value_chunks.csv",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "data" / "extraction_queues" / "full_hzo_llm_retry_chunks.csv",
    )
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--limit-rows", type=int, default=None)
    args = parser.parse_args()
    print(
        json.dumps(
            prepare_llm_retry_queue(
                source_queue=args.source_queue,
                output_path=args.output,
                db_path=args.db_path,
                limit_rows=args.limit_rows,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
