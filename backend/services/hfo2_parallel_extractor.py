from __future__ import annotations

import json
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from backend.core.config import PROJECT_ROOT, get_settings
from backend.db.session import connect
from backend.services.hfo2_extractor import (
    EXTRACTOR_VERSION,
    LLM_EXTRACTOR_VERSION,
    empty_result_payload,
    extract_chunk,
    preaudit_status,
)
from backend.services.llm_extractor import extract_chunk_with_llm
from backend.services.llm_quota_guard import clear_llm_pause, is_llm_budget_error, write_llm_pause
from backend.services.ontology_builder import build_ontology
from backend.services.ontology_context import build_ontology_context
from backend.services.pipeline_log import record_pipeline_run


def _candidate_for_result(
    row: dict[str, Any],
    result: Any,
    source: str,
    extractor_version: str,
    ontology_version: str,
    llm_error: str | None = None,
    llm_usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status, confidence, warnings = preaudit_status(result, source)
    if llm_error:
        warnings.append(f"LLM fallback used: {llm_error[:240]}")
    payload = result.model_dump(mode="json")
    payload["preaudit"] = {
        "status": status,
        "confidence": confidence,
        "warnings": warnings,
        "extractor_version": extractor_version,
        "extraction_source": source,
        "ontology_version": ontology_version,
    }
    if llm_usage:
        payload["preaudit"]["llm_usage"] = llm_usage
    candidate_id = f"cand_{uuid.uuid5(uuid.NAMESPACE_URL, row['chunk_id'] + json.dumps(payload, sort_keys=True, ensure_ascii=False)).hex[:16]}"
    facts = []
    if result.properties:
        for index, prop in enumerate(result.properties):
            fact_payload = {
                "material": result.materials[0].model_dump(mode="json") if result.materials else None,
                "sample": result.samples[0].model_dump(mode="json") if result.samples else None,
                "property": prop.model_dump(mode="json"),
                "phases": [phase.model_dump(mode="json") for phase in result.phases],
                "devices": [device.model_dump(mode="json") for device in result.devices],
                "preaudit": payload["preaudit"],
            }
            fact_payload["ontology_context"] = build_ontology_context(
                fact_payload["material"],
                fact_payload["sample"],
                fact_payload["property"],
                fact_payload["phases"],
                fact_payload["devices"],
            )
            fact_id = f"fact_{uuid.uuid5(uuid.NAMESPACE_URL, candidate_id + str(index)).hex[:16]}"
            facts.append(
                {
                    "fact_id": fact_id,
                    "candidate_id": candidate_id,
                    "paper_id": result.paper_id,
                    "pdf_id": result.pdf_id,
                    "chunk_id": result.chunk_id,
                    "page_number": result.page_number,
                    "payload_json": json.dumps(fact_payload, ensure_ascii=False),
                    "review_status": status,
                }
            )
    return {
        "candidate_id": candidate_id,
        "paper_id": result.paper_id,
        "pdf_id": result.pdf_id,
        "chunk_id": result.chunk_id,
        "page_number": result.page_number,
        "payload_json": json.dumps(payload, ensure_ascii=False),
        "extractor_version": extractor_version,
        "ontology_version": ontology_version,
        "confidence": confidence,
        "status": status,
        "error_message": None,
        "output_payload": {"candidate_id": candidate_id, **payload},
        "facts": facts,
        "stats": {
            "candidates": 1,
            "preapproved": int(status == "preapproved_machine"),
            "needs_human_review": int(status != "preapproved_machine"),
        },
    }


def _process_row(
    row: dict[str, Any],
    should_use_llm: bool,
    llm_model: str | None,
    ontology_version: str,
) -> dict[str, Any]:
    source = "rules"
    extractor_version = EXTRACTOR_VERSION
    llm_error = None
    llm_usage = None
    try:
        if should_use_llm:
            outcome = extract_chunk_with_llm(row, model=llm_model)
            llm_usage = outcome.usage or None
            if outcome.result is not None:
                result = outcome.result
                source = "llm"
                extractor_version = LLM_EXTRACTOR_VERSION
                llm_stats = {"llm_used": 1, "llm_failed": 0, "llm_skipped": 0, "rules_used": 0}
            else:
                llm_error = outcome.error_message
                if outcome.used_llm and is_llm_budget_error(llm_error):
                    return {
                        "kind": "llm_budget_pause",
                        "paper_id": row["paper_id"],
                        "pdf_id": row["pdf_id"],
                        "chunk_id": row["chunk_id"],
                        "page_number": row["page_number"],
                        "error_message": llm_error,
                        "stats": {
                            "llm_failed": 1,
                            "paused": 1,
                        },
                    }
                result = extract_chunk(row)
                llm_stats = {
                    "llm_used": 0,
                    "llm_failed": int(outcome.used_llm),
                    "llm_skipped": int(not outcome.used_llm),
                    "rules_used": 1,
                }
        else:
            result = extract_chunk(row)
            llm_stats = {"llm_used": 0, "llm_failed": 0, "llm_skipped": 0, "rules_used": 1}
    except Exception as exc:
        fallback_llm_stats = locals().get(
            "llm_stats",
            {"llm_used": 0, "llm_failed": 0, "llm_skipped": 0, "rules_used": 0},
        )
        candidate_id = f"cand_error_{uuid.uuid5(uuid.NAMESPACE_URL, row['chunk_id'] + str(exc)).hex[:16]}"
        error_payload = {
            "paper_id": row["paper_id"],
            "pdf_id": row["pdf_id"],
            "chunk_id": row["chunk_id"],
            "page_number": row["page_number"],
            "error": str(exc),
            "extractor_version": extractor_version,
            "ontology_version": ontology_version,
        }
        return {
            "kind": "error",
            "candidate_id": candidate_id,
            "paper_id": row["paper_id"],
            "pdf_id": row["pdf_id"],
            "chunk_id": row["chunk_id"],
            "page_number": row["page_number"],
            "payload_json": json.dumps(error_payload, ensure_ascii=False),
            "extractor_version": extractor_version,
            "ontology_version": ontology_version,
            "confidence": 0.0,
            "status": "extraction_error",
            "error_message": str(exc),
            "output_payload": {"candidate_id": candidate_id, **error_payload},
            "facts": [],
            "stats": {
                "errors": 1,
                "candidates": 0,
                "empty": 0,
                "empty_recorded": 0,
                "preapproved": 0,
                "needs_human_review": 0,
                **fallback_llm_stats,
            },
        }

    if not any([result.materials, result.samples, result.phases, result.properties, result.devices]):
        payload = empty_result_payload(
            row,
            source,
            extractor_version,
            ontology_version,
            llm_error=llm_error,
        )
        if llm_usage:
            payload["preaudit"]["llm_usage"] = llm_usage
        candidate_id = f"cand_empty_{uuid.uuid5(uuid.NAMESPACE_URL, row['chunk_id'] + ontology_version).hex[:16]}"
        return {
            "kind": "empty",
            "candidate_id": candidate_id,
            "paper_id": row["paper_id"],
            "pdf_id": row["pdf_id"],
            "chunk_id": row["chunk_id"],
            "page_number": row["page_number"],
            "payload_json": json.dumps(payload, ensure_ascii=False),
            "extractor_version": extractor_version,
            "ontology_version": ontology_version,
            "confidence": 0.0,
            "status": "empty_result",
            "error_message": None,
            "output_payload": {"candidate_id": candidate_id, **payload},
            "facts": [],
            "stats": {"empty": 1, "empty_recorded": 1, "candidates": 0, "preapproved": 0, "needs_human_review": 0, **llm_stats},
        }

    item = _candidate_for_result(
        row,
        result,
        source,
        extractor_version,
        ontology_version,
        llm_error=llm_error,
        llm_usage=llm_usage,
    )
    item["kind"] = "candidate"
    item["stats"].update(llm_stats)
    return item


def run_parallel_extraction(
    limit_chunks: int | None = None,
    db_path: Path | None = None,
    use_llm: bool | None = None,
    llm_model: str | None = None,
    dry_run: bool = False,
    reset_existing: bool = True,
    commit_every: int = 50,
    progress_every: int | None = 10,
    max_workers: int = 24,
) -> dict[str, Any]:
    output_path = PROJECT_ROOT / "data" / "extraction_candidates" / "hfo2_candidates.jsonl"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    should_use_llm = settings.use_llm if use_llm is None else use_llm
    ontology_build = build_ontology(db_path=db_path, record_run=not dry_run)
    ontology_version = ontology_build["version"]
    stats: dict[str, Any] = {
        "ontology_version": ontology_version,
        "chunks": 0,
        "candidates": 0,
        "llm_used": 0,
        "llm_failed": 0,
        "llm_skipped": 0,
        "rules_used": 0,
        "preapproved": 0,
        "needs_human_review": 0,
        "empty": 0,
        "empty_recorded": 0,
        "errors": 0,
        "skipped_existing": 0,
        "max_workers": max_workers,
        "paused": 0,
    }
    if should_use_llm and not dry_run:
        clear_llm_pause()

    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT chunk_id, paper_id, pdf_id, page_number, text
            FROM document_chunks
            WHERE is_high_value = 1
              AND (? = 1 OR NOT EXISTS (
                  SELECT 1 FROM extraction_candidates ec
                  WHERE ec.chunk_id = document_chunks.chunk_id
                    AND ec.ontology_version = ?
              ))
            ORDER BY pdf_id, chunk_index
            """,
            (int(reset_existing), ontology_version),
        ).fetchall()
        row_dicts = [dict(row) for row in rows]
        if limit_chunks is not None:
            row_dicts = row_dicts[:limit_chunks]

        if not dry_run:
            if reset_existing:
                conn.execute("DELETE FROM extraction_candidates")
                conn.execute("DELETE FROM reviewed_facts")
                conn.commit()
            else:
                stats["skipped_existing"] = conn.execute(
                    "SELECT COUNT(DISTINCT chunk_id) FROM extraction_candidates WHERE ontology_version = ?",
                    (ontology_version,),
                ).fetchone()[0]

        file_mode = "w" if reset_existing else "a"
        def submit_next(pool: ThreadPoolExecutor, index: int):
            if index >= len(row_dicts):
                return None
            return pool.submit(_process_row, row_dicts[index], should_use_llm, llm_model, ontology_version)

        with output_path.open(file_mode, encoding="utf-8") as fh, ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
            next_index = 0
            futures = set()
            initial = min(max(1, max_workers), len(row_dicts))
            for _ in range(initial):
                future = submit_next(pool, next_index)
                next_index += 1
                if future is not None:
                    futures.add(future)

            while futures:
                done, futures = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    item = future.result()
                    stats["chunks"] += 1
                    for key, value in item["stats"].items():
                        stats[key] = int(stats.get(key, 0)) + int(value)
                    if item.get("kind") == "llm_budget_pause":
                        pause_path = write_llm_pause(
                            item.get("error_message") or "LLM quota/authentication/rate-limit error",
                            {
                                "pipeline": "05_run_extraction",
                                "paper_id": item.get("paper_id"),
                                "pdf_id": item.get("pdf_id"),
                                "chunk_id": item.get("chunk_id"),
                                "page_number": item.get("page_number"),
                                "processed_chunks_in_this_run": stats["chunks"],
                            },
                        )
                        stats["pause_file"] = str(pause_path)
                        if not dry_run:
                            conn.commit()
                        for pending in futures:
                            pending.cancel()
                        futures.clear()
                        break
                    if not dry_run:
                        conn.execute(
                            """
                            INSERT INTO extraction_candidates (
                                candidate_id, paper_id, pdf_id, chunk_id, page_number, payload_json,
                                extractor_version, ontology_version, confidence, status, error_message
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(candidate_id) DO UPDATE SET
                                payload_json = excluded.payload_json,
                                extractor_version = excluded.extractor_version,
                                ontology_version = excluded.ontology_version,
                                confidence = excluded.confidence,
                                status = excluded.status,
                                error_message = excluded.error_message
                            """,
                            (
                                item["candidate_id"],
                                item["paper_id"],
                                item["pdf_id"],
                                item["chunk_id"],
                                item["page_number"],
                                item["payload_json"],
                                item["extractor_version"],
                                item["ontology_version"],
                                item["confidence"],
                                item["status"],
                                item["error_message"],
                            ),
                        )
                        for fact in item["facts"]:
                            conn.execute(
                                """
                                INSERT INTO reviewed_facts (
                                    fact_id, candidate_id, paper_id, pdf_id, chunk_id, page_number,
                                    fact_type, payload_json, review_status, reviewer_notes
                                )
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                ON CONFLICT(fact_id) DO UPDATE SET
                                    payload_json = excluded.payload_json,
                                    review_status = excluded.review_status,
                                    reviewer_notes = excluded.reviewer_notes,
                                    updated_at = CURRENT_TIMESTAMP
                                """,
                                (
                                    fact["fact_id"],
                                    fact["candidate_id"],
                                    fact["paper_id"],
                                    fact["pdf_id"],
                                    fact["chunk_id"],
                                    fact["page_number"],
                                    "ferroelectric_property",
                                    fact["payload_json"],
                                    fact["review_status"],
                                    "Machine pre-audit only. Requires later human review before publication claims.",
                                ),
                            )
                    fh.write(json.dumps(item["output_payload"], ensure_ascii=False) + "\n")
                    fh.flush()
                    if not dry_run and commit_every > 0 and stats["chunks"] % commit_every == 0:
                        conn.commit()
                    if progress_every and stats["chunks"] % progress_every == 0:
                        print(
                            "processed={chunks} candidates={candidates} llm_used={llm_used} "
                            "llm_failed={llm_failed} empty={empty} "
                            "empty_recorded={empty_recorded} errors={errors} paused={paused}".format(**stats),
                            flush=True,
                        )
                    if not stats.get("paused"):
                        future = submit_next(pool, next_index)
                        next_index += 1
                        if future is not None:
                            futures.add(future)
                if stats.get("paused"):
                    break
            if not dry_run:
                conn.commit()

    if not dry_run:
        record_pipeline_run(
            "05_run_extraction_parallel",
            "paused" if stats.get("paused") else "ok",
            stats,
            db_path=db_path,
        )
    return stats
