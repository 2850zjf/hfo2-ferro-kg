from __future__ import annotations

import json
import os
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from backend.core.config import PROJECT_ROOT, get_settings
from backend.db.session import connect
from backend.services.hfo2_extractor import (
    EXTRACTOR_VERSION,
    LLM_EXTRACTOR_VERSION,
    build_reviewed_fact_records,
    empty_result_payload,
    extract_chunk,
    preaudit_status,
)
from backend.services.llm_extractor import extract_chunk_with_llm
from backend.services.llm_quota_guard import (
    clear_llm_pause,
    is_llm_budget_error,
    is_llm_transient_error,
    write_llm_pause,
)
from backend.services.ontology_builder import build_ontology
from backend.services.ontology_context import build_ontology_context
from backend.services.pipeline_log import record_pipeline_run


UNRELATED_PDF_PATH_PATTERN = "%/data/unrelated_pdfs/%"


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
    facts = build_reviewed_fact_records(result, candidate_id, payload["preaudit"], status)
    usage_stats = {
        "llm_prompt_tokens": int((llm_usage or {}).get("prompt_tokens") or 0),
        "llm_completion_tokens": int((llm_usage or {}).get("completion_tokens") or 0),
        "llm_total_tokens": int((llm_usage or {}).get("total_tokens") or 0),
        "llm_specialist_calls": int((llm_usage or {}).get("specialist_calls") or 0),
    }
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
            **usage_stats,
        },
    }


def _process_row(
    row: dict[str, Any],
    should_use_llm: bool,
    llm_model: str | None,
    ontology_version: str,
    llm_strict: bool = False,
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
                if llm_strict and should_use_llm:
                    if (not outcome.used_llm) or is_llm_budget_error(llm_error):
                        return {
                            "kind": "llm_budget_pause",
                            "paper_id": row["paper_id"],
                            "pdf_id": row["pdf_id"],
                            "chunk_id": row["chunk_id"],
                            "page_number": row["page_number"],
                            "error_message": llm_error or "Strict LLM extraction failed before returning a structured result.",
                            "stats": {
                                "llm_failed": int(outcome.used_llm),
                                "llm_skipped": int(not outcome.used_llm),
                                "paused": 1,
                            },
                        }
                    raise ValueError(f"Strict LLM extraction produced an unusable response: {llm_error}")
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
            "transient_error": is_llm_transient_error(str(exc)),
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

    if not any(
        [
            result.materials,
            result.samples,
            result.phases,
            result.properties,
            result.devices,
            result.process_steps,
            result.reliability_events,
            result.mechanisms,
            result.computations,
            result.applications,
            result.relations,
        ]
    ):
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
            "stats": {
                "empty": 1,
                "empty_recorded": 1,
                "candidates": 0,
                "preapproved": 0,
                "needs_human_review": 0,
                "llm_prompt_tokens": int((llm_usage or {}).get("prompt_tokens") or 0),
                "llm_completion_tokens": int((llm_usage or {}).get("completion_tokens") or 0),
                "llm_total_tokens": int((llm_usage or {}).get("total_tokens") or 0),
                "llm_specialist_calls": int((llm_usage or {}).get("specialist_calls") or 0),
                **llm_stats,
            },
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
    paper_ids: list[str] | None = None,
    chunk_ids: list[str] | None = None,
    high_value_only: bool = True,
    llm_strict: bool = False,
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
        "llm_prompt_tokens": 0,
        "llm_completion_tokens": 0,
        "llm_total_tokens": 0,
        "llm_specialist_calls": 0,
        "rules_used": 0,
        "preapproved": 0,
        "needs_human_review": 0,
        "empty": 0,
        "empty_recorded": 0,
        "errors": 0,
        "skipped_existing": 0,
        "max_workers": max_workers,
        "paused": 0,
        "transient_paused": 0,
        "transient_error_streak": 0,
        "high_value_only": int(high_value_only),
        "llm_strict": int(llm_strict),
    }
    if should_use_llm and not dry_run:
        clear_llm_pause()

    with connect(db_path) as conn:
        query = """
        SELECT dc.chunk_id, dc.paper_id, dc.pdf_id, dc.page_number, dc.section,
               dc.chunk_index, dc.text, p.title, p.doi, p.year, p.paper_type,
               p.is_review,
               (SELECT prev.text FROM document_chunks prev
                WHERE prev.pdf_id = dc.pdf_id AND prev.chunk_index = dc.chunk_index - 1
                LIMIT 1) AS context_before,
               (SELECT nxt.text FROM document_chunks nxt
                WHERE nxt.pdf_id = dc.pdf_id AND nxt.chunk_index = dc.chunk_index + 1
                LIMIT 1) AS context_after
        FROM document_chunks dc
        LEFT JOIN pdf_files pf ON pf.pdf_id = dc.pdf_id
        LEFT JOIN papers p ON p.paper_id = dc.paper_id
        WHERE (? = 0 OR dc.is_high_value = 1)
          AND (pf.file_path IS NULL OR pf.file_path NOT LIKE ?)
          AND (? = 1 OR NOT EXISTS (
              SELECT 1 FROM extraction_candidates ec
              WHERE ec.chunk_id = dc.chunk_id
                AND ec.ontology_version = ?
          ))
        """
        params: list[object] = [int(high_value_only), UNRELATED_PDF_PATH_PATTERN, int(reset_existing), ontology_version]
        if paper_ids:
            placeholders = ",".join("?" for _ in paper_ids)
            query += f" AND dc.paper_id IN ({placeholders})"
            params.extend(paper_ids)
        if chunk_ids:
            placeholders = ",".join("?" for _ in chunk_ids)
            query += f" AND dc.chunk_id IN ({placeholders})"
            params.extend(chunk_ids)
        query += " ORDER BY dc.pdf_id, dc.chunk_index"
        rows = conn.execute(query, params).fetchall()
        row_dicts = [dict(row) for row in rows]
        if limit_chunks is not None:
            row_dicts = row_dicts[:limit_chunks]

        if not dry_run:
            if reset_existing:
                selected_chunk_ids = [row["chunk_id"] for row in row_dicts]
                if paper_ids or chunk_ids or limit_chunks is not None:
                    if selected_chunk_ids:
                        placeholders = ",".join("?" for _ in selected_chunk_ids)
                        conn.execute(
                            f"DELETE FROM extraction_candidates WHERE chunk_id IN ({placeholders})",
                            selected_chunk_ids,
                        )
                        conn.execute(
                            f"DELETE FROM reviewed_facts WHERE chunk_id IN ({placeholders})",
                            selected_chunk_ids,
                        )
                else:
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
            return pool.submit(_process_row, row_dicts[index], should_use_llm, llm_model, ontology_version, llm_strict)

        transient_streak = 0
        try:
            transient_threshold = max(
                2,
                int(os.getenv("HFO2_FERROKG_TRANSIENT_ERROR_THRESHOLD", "4")),
            )
        except ValueError:
            transient_threshold = 4

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
                    if item.get("transient_error"):
                        transient_streak += 1
                    else:
                        transient_streak = 0
                    stats["transient_error_streak"] = transient_streak
                    if item.get("kind") == "llm_budget_pause":
                        pause_path = write_llm_pause(
                            item.get("error_message") or "LLM quota/authentication/rate-limit error",
                            {
                                "pipeline": "05_run_extraction",
                                "strict_llm": bool(llm_strict),
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
                                    fact["fact_type"],
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
                    if transient_streak >= transient_threshold:
                        stats["transient_paused"] = 1
                        stats["transient_pause_reason"] = str(item.get("error_message") or "")[:1000]
                        if not dry_run:
                            conn.commit()
                        for pending in futures:
                            pending.cancel()
                        futures.clear()
                        print(
                            "transient_provider_pause="
                            f"{transient_streak} threshold={transient_threshold} "
                            f"reason={stats['transient_pause_reason']}",
                            flush=True,
                        )
                        break
                    if not stats.get("paused") and not stats.get("transient_paused"):
                        future = submit_next(pool, next_index)
                        next_index += 1
                        if future is not None:
                            futures.add(future)
                if stats.get("paused") or stats.get("transient_paused"):
                    break
            if not dry_run:
                conn.commit()

    if not dry_run:
        if stats.get("paused"):
            run_status = "paused"
        elif stats.get("transient_paused"):
            run_status = "transient_pause"
        else:
            run_status = "ok"
        record_pipeline_run("05_run_extraction_parallel", run_status, stats, db_path=db_path)
    return stats
