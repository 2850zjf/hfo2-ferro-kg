from __future__ import annotations

import json
import re
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from backend.core.config import PROJECT_ROOT, get_llm_api_key, get_settings
from backend.db.session import connect
from backend.services.llm_extractor import json_loads_object, normalize_base_url
from backend.services.llm_quota_guard import clear_llm_pause, is_llm_budget_error, write_llm_pause
from backend.services.pipeline_log import record_pipeline_run


BENCHMARK_VERSION = "hfo2-open-benchmark-v0.1"


def _clean_json_payload(data: dict[str, Any], row: Any, source_type: str) -> dict[str, Any]:
    """Keep open extraction flexible while preserving traceability."""
    return {
        "paper_id": row["paper_id"],
        "pdf_id": row["pdf_id"],
        "chunk_id": row["chunk_id"],
        "page_number": row["page_number"],
        "source_type": source_type,
        "benchmark_version": BENCHMARK_VERSION,
        "material_systems": _as_list(data.get("material_systems")),
        "sample_processes": _as_list(data.get("sample_processes")),
        "properties": _as_list(data.get("properties")),
        "mechanisms": _as_list(data.get("mechanisms")),
        "theoretical_insights": _as_list(data.get("theoretical_insights")),
        "design_rules": _as_list(data.get("design_rules")),
        "optimization_targets": _as_list(data.get("optimization_targets")),
        "benchmark_records": _as_list(data.get("benchmark_records")),
        "figure_or_table_observations": _as_list(data.get("figure_or_table_observations")),
        "ontology_candidates": _as_list(data.get("ontology_candidates")),
        "warnings": [str(item) for item in _as_list(data.get("warnings")) if str(item).strip()],
        "llm_usage": data.get("llm_usage") if isinstance(data.get("llm_usage"), dict) else {},
    }


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _source_type(section: str | None) -> str:
    if section == "table":
        return "table"
    if section == "figure_caption":
        return "figure_caption"
    return "text"


def _system_prompt() -> str:
    return """
You are building HfO2-FerroKG-Benchmark, an evidence-grounded dataset for
hafnia-based ferroelectric material design and process optimization.

Extract open scientific facts from the provided chunk. Do NOT restrict yourself
to the current ontology. Include experimental, computational, theoretical,
review, mechanism, design-rule, and optimization evidence when present.

Return only one valid JSON object with these keys:
- material_systems: array of objects with raw_name, normalized_name, composition,
  dopants, material_family, evidence_text.
- sample_processes: array of objects with thickness_nm, deposition_method,
  annealing_temperature_c, annealing_time_s, annealing_atmosphere, electrode_stack,
  substrate, device_type, phase, wake_up_or_endurance_state, evidence_text.
- properties: array of objects with property_name, raw_property_name, value,
  unit, condition, device_type, reported_or_derived, evidence_text.
- mechanisms: array of objects with mechanism_name, cause, effect, evidence_text.
- theoretical_insights: array of objects with model_or_method, descriptor,
  conclusion, design_relevance, evidence_text.
- design_rules: array of objects with rule, variables, target_property,
  direction, confidence, evidence_text.
- optimization_targets: array of objects with target, objective, constraints,
  evidence_text.
- benchmark_records: array of objects suitable for ML or benchmark rows. Include
  input_variables, output_targets, conditions, evidence_text, and quality_flags.
- figure_or_table_observations: array of objects summarizing table rows, figure
  captions, trends, labels, axes, or reported comparisons when present.
- ontology_candidates: array of objects with entity_or_relation, label,
  reason_to_add, evidence_text.
- warnings: array of strings.

Hard rules:
- Every extracted item must include evidence_text copied from the chunk.
- Keep Pr and 2Pr separate. Do not convert 2Pr to Pr unless the text explicitly
  gives a derivation.
- Mark secondary literature, review summaries, figure-estimated values, and
  ambiguous sample-property links in warnings or quality_flags.
- If the chunk has no useful benchmark evidence, return empty arrays.
""".strip()


def _user_prompt(row: Any, source_type: str) -> str:
    return f"""
paper_id: {row['paper_id']}
pdf_id: {row['pdf_id']}
chunk_id: {row['chunk_id']}
page_number: {row['page_number']}
source_type: {source_type}

chunk_text:
{row['text']}
""".strip()


def extract_open_benchmark_chunk(row: Any, model: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
    if not get_llm_api_key():
        return None, "LLM API key is not configured"
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover
        return None, f"OpenAI SDK unavailable: {exc}"

    settings = get_settings()
    selected_model = model or settings.llm_model
    base_url, _ = normalize_base_url(settings.llm_provider, settings.llm_base_url)
    client_kwargs: dict[str, Any] = {
        "api_key": get_llm_api_key(),
        "timeout": settings.llm_timeout_seconds,
    }
    if base_url:
        client_kwargs["base_url"] = base_url
    source_type = _source_type(row["section"])
    try:
        client = OpenAI(**client_kwargs)
        request_kwargs: dict[str, Any] = {
            "model": selected_model,
            "messages": [
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": _user_prompt(row, source_type)},
            ],
            "temperature": 0,
            "max_tokens": min(settings.llm_max_tokens, 3000),
            "response_format": {"type": "json_object"},
        }
        if settings.llm_provider == "dashscope":
            request_kwargs["extra_body"] = {"enable_thinking": settings.llm_enable_thinking}
        response = client.chat.completions.create(**request_kwargs)
        data = json_loads_object(response.choices[0].message.content or "{}")
        usage = getattr(response, "usage", None)
        if usage is not None:
            data["llm_usage"] = {
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
            }
        return _clean_json_payload(data, row, source_type), None
    except Exception as exc:
        return None, str(exc)


def _row_is_extractable(row: Any, include_low_value: bool) -> bool:
    if row["section"] == "references":
        return False
    if row["char_count"] < 120:
        return False
    if include_low_value:
        return True
    return bool(row["is_high_value"] or row["contains_table"])


def run_open_benchmark_extraction(
    limit_chunks: int | None = None,
    model: str | None = None,
    reset_existing: bool = False,
    include_low_value: bool = True,
    db_path: Path | None = None,
    commit_every: int = 20,
    progress_every: int | None = 10,
    max_workers: int = 16,
) -> dict[str, Any]:
    output_dir = PROJECT_ROOT / "data" / "benchmark"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "open_benchmark_extractions.jsonl"
    file_mode = "w" if reset_existing else "a"
    settings = get_settings()
    selected_model = model or settings.llm_model
    stats: dict[str, Any] = {
        "benchmark_version": BENCHMARK_VERSION,
        "model": selected_model,
        "chunks_seen": 0,
        "chunks_attempted": 0,
        "written": 0,
        "empty": 0,
        "errors": 0,
        "skipped_existing": 0,
        "paused": 0,
    }
    clear_llm_pause()

    with connect(db_path) as conn:
        conn.execute(
            """
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
            )
            """
        )
        if reset_existing:
            conn.execute("DELETE FROM benchmark_extractions")

        rows = conn.execute(
            """
            SELECT dc.chunk_id, dc.paper_id, dc.pdf_id, dc.page_number, dc.section,
                   dc.chunk_index, dc.text, dc.char_count, dc.contains_table,
                   dc.contains_hfo2_keyword, dc.contains_process_keyword,
                   dc.contains_property_keyword, dc.is_high_value
            FROM document_chunks dc
            WHERE (? = 1 OR NOT EXISTS (
                SELECT 1 FROM benchmark_extractions be
                WHERE be.chunk_id = dc.chunk_id
            ))
            ORDER BY dc.pdf_id, dc.chunk_index
            """,
            (int(reset_existing),),
        ).fetchall()

        rows = [row for row in rows if _row_is_extractable(row, include_low_value)]
        if limit_chunks is not None:
            rows = rows[:limit_chunks]
        if not reset_existing:
            stats["skipped_existing"] = conn.execute(
                "SELECT COUNT(*) FROM benchmark_extractions"
            ).fetchone()[0]

        def run_one(row_dict: dict[str, Any]) -> tuple[dict[str, Any], str, str, dict[str, Any] | None, str | None]:
            source_type = _source_type(row_dict["section"])
            extraction_id = f"bench_{uuid.uuid5(uuid.NAMESPACE_URL, row_dict['chunk_id'] + source_type).hex[:16]}"
            payload, error = extract_open_benchmark_chunk(row_dict, model=selected_model)
            return row_dict, source_type, extraction_id, payload, error

        def submit_next(pool: ThreadPoolExecutor, index: int):
            if index >= len(rows):
                return None
            return pool.submit(run_one, dict(rows[index]))

        with output_path.open(file_mode, encoding="utf-8") as fh, ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
            next_index = 0
            futures = set()
            initial = min(max(1, max_workers), len(rows))
            for _ in range(initial):
                future = submit_next(pool, next_index)
                next_index += 1
                if future is not None:
                    futures.add(future)

            while futures:
                done, futures = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    row, source_type, extraction_id, payload, error = future.result()
                    stats["chunks_seen"] += 1
                    if payload is None and is_llm_budget_error(error):
                        stats["errors"] += 1
                        stats["paused"] = 1
                        pause_path = write_llm_pause(
                            error or "LLM quota/authentication/rate-limit error",
                            {
                                "pipeline": "19_open_benchmark_extraction",
                                "paper_id": row["paper_id"],
                                "pdf_id": row["pdf_id"],
                                "chunk_id": row["chunk_id"],
                                "page_number": row["page_number"],
                                "source_type": source_type,
                                "processed_chunks_in_this_run": stats["chunks_seen"],
                            },
                        )
                        stats["pause_file"] = str(pause_path)
                        conn.commit()
                        for pending in futures:
                            pending.cancel()
                        futures.clear()
                        break
                    if payload is None:
                        stats["errors"] += 1
                        error_payload = {
                            "paper_id": row["paper_id"],
                            "pdf_id": row["pdf_id"],
                            "chunk_id": row["chunk_id"],
                            "page_number": row["page_number"],
                            "source_type": source_type,
                            "benchmark_version": BENCHMARK_VERSION,
                            "error": error,
                        }
                        conn.execute(
                            """
                            INSERT INTO benchmark_extractions (
                                extraction_id, paper_id, pdf_id, chunk_id, page_number,
                                source_type, payload_json, model_name, confidence, status,
                                error_message
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(chunk_id, source_type) DO UPDATE SET
                                payload_json = excluded.payload_json,
                                model_name = excluded.model_name,
                                status = excluded.status,
                                error_message = excluded.error_message
                            """,
                            (
                                extraction_id,
                                row["paper_id"],
                                row["pdf_id"],
                                row["chunk_id"],
                                row["page_number"],
                                source_type,
                                json.dumps(error_payload, ensure_ascii=False),
                                selected_model,
                                0.0,
                                "error",
                                error,
                            ),
                        )
                        fh.write(json.dumps({"extraction_id": extraction_id, **error_payload}, ensure_ascii=False) + "\n")
                    else:
                        non_empty = any(
                            payload.get(key)
                            for key in [
                                "material_systems",
                                "sample_processes",
                                "properties",
                                "mechanisms",
                                "theoretical_insights",
                                "design_rules",
                                "optimization_targets",
                                "benchmark_records",
                                "figure_or_table_observations",
                                "ontology_candidates",
                            ]
                        )
                        stats["chunks_attempted"] += 1
                        stats["written"] += int(non_empty)
                        stats["empty"] += int(not non_empty)
                        status = "ok" if non_empty else "empty_result"
                        conn.execute(
                            """
                            INSERT INTO benchmark_extractions (
                                extraction_id, paper_id, pdf_id, chunk_id, page_number,
                                source_type, payload_json, model_name, confidence, status
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(chunk_id, source_type) DO UPDATE SET
                                payload_json = excluded.payload_json,
                                model_name = excluded.model_name,
                                confidence = excluded.confidence,
                                status = excluded.status,
                                error_message = NULL
                            """,
                            (
                                extraction_id,
                                row["paper_id"],
                                row["pdf_id"],
                                row["chunk_id"],
                                row["page_number"],
                                source_type,
                                json.dumps(payload, ensure_ascii=False),
                                selected_model,
                                0.7 if non_empty else 0.0,
                                status,
                            ),
                        )
                        fh.write(json.dumps({"extraction_id": extraction_id, **payload}, ensure_ascii=False) + "\n")
                    fh.flush()
                    if commit_every > 0 and stats["chunks_seen"] % commit_every == 0:
                        conn.commit()
                    if progress_every and stats["chunks_seen"] % progress_every == 0:
                        print(
                            "benchmark processed={chunks_seen} written={written} "
                            "empty={empty} errors={errors} paused={paused}".format(**stats),
                            flush=True,
                        )
                    if not stats.get("paused"):
                        future = submit_next(pool, next_index)
                        next_index += 1
                        if future is not None:
                            futures.add(future)
                if stats.get("paused"):
                    break
            conn.commit()

    record_pipeline_run(
        "19_open_benchmark_extraction",
        "paused" if stats.get("paused") else "ok",
        stats,
        db_path=db_path,
    )
    return stats


def export_benchmark_csv(db_path: Path | None = None) -> dict[str, str | int]:
    output_dir = PROJECT_ROOT / "data" / "benchmark"
    output_dir.mkdir(parents=True, exist_ok=True)
    records_path = output_dir / "hfo2_benchmark_records.jsonl"
    ontology_candidates_path = output_dir / "ontology_extension_candidates.jsonl"
    record_count = 0
    ontology_count = 0
    with connect(db_path) as conn, records_path.open("w", encoding="utf-8") as records_fh, ontology_candidates_path.open(
        "w", encoding="utf-8"
    ) as ontology_fh:
        rows = conn.execute(
            """
            SELECT be.*, p.title, p.doi, p.year
            FROM benchmark_extractions be
            LEFT JOIN papers p ON p.paper_id = be.paper_id
            WHERE be.status IN ('ok', 'empty_result')
            ORDER BY be.pdf_id, be.page_number
            """
        ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            for record in payload.get("benchmark_records") or []:
                records_fh.write(
                    json.dumps(
                        {
                            "paper_id": row["paper_id"],
                            "pdf_id": row["pdf_id"],
                            "chunk_id": row["chunk_id"],
                            "page_number": row["page_number"],
                            "title": row["title"],
                            "doi": row["doi"],
                            "year": row["year"],
                            **record,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                record_count += 1
            for candidate in payload.get("ontology_candidates") or []:
                ontology_fh.write(
                    json.dumps(
                        {
                            "paper_id": row["paper_id"],
                            "pdf_id": row["pdf_id"],
                            "chunk_id": row["chunk_id"],
                            "page_number": row["page_number"],
                            "title": row["title"],
                            "doi": row["doi"],
                            **candidate,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                ontology_count += 1
    return {
        "records": record_count,
        "ontology_candidates": ontology_count,
        "records_path": str(records_path),
        "ontology_candidates_path": str(ontology_candidates_path),
    }
