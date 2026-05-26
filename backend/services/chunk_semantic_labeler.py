from __future__ import annotations

import json
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from backend.db.session import connect
from backend.services.llm_json_client import llm_json_chat
from backend.services.llm_quota_guard import is_llm_budget_error, read_llm_pause, write_llm_pause
from backend.services.pipeline_log import record_pipeline_run


CHUNK_LABEL_VERSION = "chunk-semantic-label-v0.1"

SEMANTIC_SECTIONS = {
    "title",
    "abstract",
    "introduction",
    "method",
    "results",
    "discussion",
    "conclusion",
    "figure_caption",
    "table",
    "mechanism",
    "review_context",
    "theoretical_calculation",
    "references",
    "unknown",
}


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def init_chunk_label_tables(db_path: Path | None = None) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_chunk_labels (
                chunk_id TEXT PRIMARY KEY,
                paper_id TEXT,
                pdf_id TEXT NOT NULL,
                page_number INTEGER,
                semantic_section TEXT NOT NULL,
                semantic_roles_json TEXT NOT NULL,
                contains_original_experiment INTEGER DEFAULT 0,
                contains_secondary_citation INTEGER DEFAULT 0,
                contains_theoretical_guidance INTEGER DEFAULT 0,
                contains_table_or_figure_reference INTEGER DEFAULT 0,
                materials_json TEXT NOT NULL,
                properties_json TEXT NOT NULL,
                risk_flags_json TEXT NOT NULL,
                evidence_text TEXT,
                confidence REAL,
                model_name TEXT,
                llm_usage_json TEXT,
                label_version TEXT NOT NULL,
                status TEXT DEFAULT 'ok',
                error_message TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chunk_labels_section ON llm_chunk_labels(semantic_section)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chunk_labels_risk ON llm_chunk_labels(contains_secondary_citation, contains_theoretical_guidance)"
        )
        conn.commit()


def normalize_chunk_label_payload(data: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    section = str(data.get("semantic_section") or row.get("section") or "unknown").strip()
    if section not in SEMANTIC_SECTIONS:
        section = "unknown"
    payload = {
        "chunk_id": row.get("chunk_id"),
        "paper_id": row.get("paper_id"),
        "pdf_id": row.get("pdf_id"),
        "page_number": row.get("page_number"),
        "semantic_section": section,
        "semantic_roles": _as_list(data.get("semantic_roles")),
        "contains_original_experiment": bool(data.get("contains_original_experiment", False)),
        "contains_secondary_citation": bool(data.get("contains_secondary_citation", False)),
        "contains_theoretical_guidance": bool(data.get("contains_theoretical_guidance", False)),
        "contains_table_or_figure_reference": bool(data.get("contains_table_or_figure_reference", False)),
        "materials": _as_list(data.get("materials")),
        "properties": _as_list(data.get("properties")),
        "risk_flags": [str(item) for item in _as_list(data.get("risk_flags")) if str(item).strip()],
        "evidence_text": str(data.get("evidence_text") or "").strip(),
        "confidence": data.get("confidence", 0.0),
        "label_version": CHUNK_LABEL_VERSION,
    }
    try:
        payload["confidence"] = max(0.0, min(1.0, float(payload["confidence"])))
    except (TypeError, ValueError):
        payload["confidence"] = 0.0
    return payload


def _system_prompt() -> str:
    return """
You are semantically labeling chunks for HfO2-FerroKG. Return exactly one JSON object.

Required keys:
- semantic_section: one of title, abstract, introduction, method, results,
  discussion, conclusion, figure_caption, table, mechanism, review_context,
  theoretical_calculation, references, unknown.
- semantic_roles: array chosen from material_definition, sample_process,
  phase_characterization, property_report, mechanism_explanation,
  design_rule, benchmark_record, review_secondary_literature, theory_guidance,
  table_row, figure_caption.
- contains_original_experiment: boolean.
- contains_secondary_citation: boolean.
- contains_theoretical_guidance: boolean.
- contains_table_or_figure_reference: boolean.
- materials: array of material names or objects.
- properties: array of property names or objects. Keep Pr and 2Pr separate.
- risk_flags: array of strings for review citation, figure-estimated value,
  sample mismatch, fatigue-after-value, low-temperature, unit ambiguity.
- evidence_text: short copied text proving the label.
- confidence: 0 to 1.

Rules:
- A chunk can contain useful theory even if it has no experimental benchmark.
- Mark review/context/citation chunks so later modeling can avoid treating them
  as direct experimental data.
- Do not invent values not present in the chunk.
""".strip()


def _user_prompt(row: dict[str, Any]) -> str:
    return f"""
paper_id: {row.get('paper_id')}
pdf_id: {row.get('pdf_id')}
chunk_id: {row.get('chunk_id')}
page_number: {row.get('page_number')}
current_section: {row.get('section')}

chunk_text:
{row.get('text', '')}
""".strip()


def _load_rows(
    limit: int | None = None,
    include_low_value: bool = False,
    skip_existing: bool = True,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    init_chunk_label_tables(db_path=db_path)
    where = ["dc.section != 'references'", "dc.char_count >= 120"]
    if not include_low_value:
        where.append("(dc.is_high_value = 1 OR dc.contains_table = 1 OR dc.section IN ('table', 'figure_caption'))")
    if skip_existing:
        where.append("NOT EXISTS (SELECT 1 FROM llm_chunk_labels l WHERE l.chunk_id = dc.chunk_id)")
    sql = f"""
        SELECT dc.chunk_id, dc.paper_id, dc.pdf_id, dc.page_number, dc.section,
               dc.text, dc.char_count, dc.is_high_value, dc.contains_table
        FROM document_chunks dc
        WHERE {' AND '.join(where)}
        ORDER BY dc.is_high_value DESC, dc.pdf_id, dc.page_number, dc.chunk_index
    """
    params: list[Any] = []
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    with connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def _label_row(row: dict[str, Any], model: str | None = None) -> dict[str, Any]:
    result = llm_json_chat(
        _system_prompt(),
        _user_prompt(row),
        model=model,
        max_tokens=1800,
        enable_thinking=True,
    )
    payload = normalize_chunk_label_payload(result.payload, row)
    payload["llm_usage"] = result.usage
    payload["model_name"] = result.model
    return payload


def _write_labels(labels: list[dict[str, Any]], db_path: Path | None = None) -> None:
    if not labels:
        return
    init_chunk_label_tables(db_path=db_path)
    with connect(db_path) as conn:
        for label in labels:
            conn.execute(
                """
                INSERT INTO llm_chunk_labels (
                    chunk_id, paper_id, pdf_id, page_number, semantic_section,
                    semantic_roles_json, contains_original_experiment,
                    contains_secondary_citation, contains_theoretical_guidance,
                    contains_table_or_figure_reference, materials_json,
                    properties_json, risk_flags_json, evidence_text, confidence,
                    model_name, llm_usage_json, label_version, status, error_message,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(chunk_id) DO UPDATE SET
                    semantic_section = excluded.semantic_section,
                    semantic_roles_json = excluded.semantic_roles_json,
                    contains_original_experiment = excluded.contains_original_experiment,
                    contains_secondary_citation = excluded.contains_secondary_citation,
                    contains_theoretical_guidance = excluded.contains_theoretical_guidance,
                    contains_table_or_figure_reference = excluded.contains_table_or_figure_reference,
                    materials_json = excluded.materials_json,
                    properties_json = excluded.properties_json,
                    risk_flags_json = excluded.risk_flags_json,
                    evidence_text = excluded.evidence_text,
                    confidence = excluded.confidence,
                    model_name = excluded.model_name,
                    llm_usage_json = excluded.llm_usage_json,
                    label_version = excluded.label_version,
                    status = excluded.status,
                    error_message = excluded.error_message,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    label["chunk_id"],
                    label.get("paper_id"),
                    label["pdf_id"],
                    label.get("page_number"),
                    label.get("semantic_section") or "unknown",
                    _json(label.get("semantic_roles") or []),
                    int(bool(label.get("contains_original_experiment"))),
                    int(bool(label.get("contains_secondary_citation"))),
                    int(bool(label.get("contains_theoretical_guidance"))),
                    int(bool(label.get("contains_table_or_figure_reference"))),
                    _json(label.get("materials") or []),
                    _json(label.get("properties") or []),
                    _json(label.get("risk_flags") or []),
                    label.get("evidence_text") or "",
                    float(label.get("confidence") or 0),
                    label.get("model_name") or "",
                    _json(label.get("llm_usage") or {}),
                    CHUNK_LABEL_VERSION,
                    label.get("status") or "ok",
                    label.get("error_message"),
                ),
            )
        conn.commit()


def label_chunks_semantically(
    limit: int | None = None,
    include_low_value: bool = False,
    reset: bool = False,
    model: str | None = None,
    max_workers: int = 12,
    commit_every: int = 50,
    progress_every: int = 25,
    force_llm_when_paused: bool = False,
    db_path: Path | None = None,
) -> dict[str, Any]:
    init_chunk_label_tables(db_path=db_path)
    if reset:
        with connect(db_path) as conn:
            conn.execute("DELETE FROM llm_chunk_labels")
            conn.commit()
    rows = _load_rows(limit=limit, include_low_value=include_low_value, skip_existing=not reset, db_path=db_path)
    stats: dict[str, Any] = {
        "chunks_seen": len(rows),
        "labels_written": 0,
        "llm_failed": 0,
        "paused": 0,
    }
    if read_llm_pause() and not force_llm_when_paused:
        stats["paused"] = 1
        record_pipeline_run("27_label_chunks_semantically", "paused", stats, db_path=db_path)
        return stats
    pending: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal pending
        if pending:
            _write_labels(pending, db_path=db_path)
            stats["labels_written"] += len(pending)
            pending = []

    def run_one(row: dict[str, Any]) -> dict[str, Any]:
        try:
            return _label_row(row, model=model)
        except Exception as exc:
            error = str(exc)
            if is_llm_budget_error(error):
                return {"_pause_error": error, **row}
            return normalize_chunk_label_payload({}, row) | {
                "status": "error",
                "error_message": error[:1500],
            }

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        futures = set()
        index = 0

        def submit_next() -> None:
            nonlocal index
            if index < len(rows):
                futures.add(pool.submit(run_one, rows[index]))
                index += 1

        for _ in range(min(max(1, max_workers), len(rows))):
            submit_next()
        while futures:
            done, futures = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                result = future.result()
                if "_pause_error" in result:
                    stats["paused"] = 1
                    stats["llm_failed"] += 1
                    pause_path = write_llm_pause(result["_pause_error"], {"pipeline": "27_label_chunks_semantically"})
                    stats["pause_file"] = str(pause_path)
                    for pending_future in futures:
                        pending_future.cancel()
                    futures.clear()
                    break
                if result.get("status") == "error":
                    stats["llm_failed"] += 1
                pending.append(result)
                if progress_every and (stats["labels_written"] + len(pending)) % progress_every == 0:
                    print(
                        f"chunk_labels processed={stats['labels_written'] + len(pending)} "
                        f"written={stats['labels_written']} errors={stats['llm_failed']} paused={stats['paused']}",
                        flush=True,
                    )
                if commit_every and len(pending) >= commit_every:
                    flush()
                if not stats.get("paused"):
                    submit_next()
            if stats.get("paused"):
                break
    flush()
    record_pipeline_run(
        "27_label_chunks_semantically",
        "paused" if stats.get("paused") else "ok",
        stats,
        db_path=db_path,
    )
    return stats
