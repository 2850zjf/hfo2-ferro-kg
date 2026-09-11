from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.db.session import connect
from backend.services.hfo2_extractor import (
    LLM_CURRENT_CHUNK_PATH,
    clear_llm_current_chunk,
    insert_llm_deferred_candidate,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reason", default="Stale LLM request deferred by progress guard.")
    parser.add_argument("--failure-count", type=int, default=3)
    parser.add_argument("--max-retries", type=int, default=3)
    args = parser.parse_args()

    if not LLM_CURRENT_CHUNK_PATH.exists():
        print("no current LLM chunk heartbeat found")
        return

    heartbeat = json.loads(LLM_CURRENT_CHUNK_PATH.read_text(encoding="utf-8"))
    chunk_id = heartbeat.get("chunk_id")
    ontology_version = heartbeat.get("ontology_version") or "hfo2-ferrokg-v1"
    if not chunk_id:
        print("current LLM chunk heartbeat has no chunk_id")
        return

    with connect() as conn:
        existing = conn.execute(
            """
            SELECT candidate_id, status
            FROM extraction_candidates
            WHERE chunk_id = ? AND ontology_version = ?
            LIMIT 1
            """,
            (chunk_id, ontology_version),
        ).fetchone()
        if existing:
            print(f"skip {chunk_id}: already has candidate {existing['candidate_id']} status={existing['status']}")
            clear_llm_current_chunk(chunk_id)
            return

        row = conn.execute(
            """
            SELECT chunk_id, paper_id, pdf_id, page_number, text
            FROM document_chunks
            WHERE chunk_id = ?
            """,
            (chunk_id,),
        ).fetchone()
        if row is None:
            print(f"chunk not found: {chunk_id}")
            return

        candidate_id, payload = insert_llm_deferred_candidate(
            conn,
            row,
            ontology_version,
            args.reason,
            args.failure_count,
            args.max_retries,
        )
        conn.commit()

    output_path = Path("data/extraction_candidates/hfo2_candidates.jsonl")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"candidate_id": candidate_id, **payload}, ensure_ascii=False) + "\n")
    clear_llm_current_chunk(chunk_id)
    print(f"deferred {chunk_id} as {candidate_id}")


if __name__ == "__main__":
    main()
