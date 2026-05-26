from __future__ import annotations

import json
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from backend.db.session import connect
from backend.services.llm_json_client import llm_json_chat
from backend.services.llm_quota_guard import is_llm_budget_error, read_llm_pause, write_llm_pause
from backend.services.pipeline_log import record_pipeline_run


AI_AUDIT_VERSION = "ai-fact-audit-v0.1"
AUDIT_STATUSES = {
    "usable_for_model",
    "usable_for_rag_only",
    "needs_human_review",
    "reject",
}
HIGH_VALUE_TARGETS = {
    "double_remanent_polarization_2Pr",
    "remanent_polarization_Pr",
    "coercive_field_Ec",
    "endurance_cycles",
    "retention_time",
    "memory_window",
    "leakage_current_density",
}


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _safe_loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return {} if default is None else default
    try:
        return json.loads(value)
    except Exception:
        return {} if default is None else default


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def init_ai_audit_tables(db_path: Path | None = None) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_fact_audits (
                audit_id TEXT PRIMARY KEY,
                link_id TEXT NOT NULL,
                source_kind TEXT,
                source_id TEXT,
                paper_id TEXT,
                sample_id TEXT,
                ai_review_status TEXT NOT NULL,
                risk_flags_json TEXT NOT NULL,
                repair_suggestion TEXT,
                usable_for_model INTEGER DEFAULT 0,
                usable_for_paper_claims INTEGER DEFAULT 0,
                reasoning_summary TEXT,
                evidence_text TEXT,
                model_name TEXT,
                llm_usage_json TEXT,
                audit_version TEXT NOT NULL,
                status TEXT DEFAULT 'ok',
                error_message TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(link_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_fact_audits_status ON ai_fact_audits(ai_review_status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_fact_audits_paper ON ai_fact_audits(paper_id)")
        conn.commit()


def normalize_ai_audit_payload(data: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    status = str(data.get("ai_review_status") or "").strip()
    if status not in AUDIT_STATUSES:
        context_quality = str(row.get("context_quality") or "")
        status = "usable_for_model" if context_quality == "strong" else "needs_human_review"
    risk_flags = [str(item).strip() for item in _as_list(data.get("risk_flags")) if str(item).strip()]
    usable_for_model = bool(data.get("usable_for_model", status == "usable_for_model"))
    usable_for_paper_claims = bool(data.get("usable_for_paper_claims", status in {"usable_for_model", "usable_for_rag_only"}))
    if status in {"needs_human_review", "reject"}:
        usable_for_model = False
    payload = {
        "link_id": row["link_id"],
        "source_kind": row.get("source_kind"),
        "source_id": row.get("source_id"),
        "paper_id": row.get("paper_id"),
        "sample_id": row.get("sample_id"),
        "ai_review_status": status,
        "risk_flags": risk_flags,
        "repair_suggestion": str(data.get("repair_suggestion") or "").strip(),
        "usable_for_model": usable_for_model,
        "usable_for_paper_claims": usable_for_paper_claims,
        "reasoning_summary": str(data.get("reasoning_summary") or "").strip(),
        "evidence_text": str(data.get("evidence_text") or row.get("evidence_text") or "").strip(),
        "audit_version": AI_AUDIT_VERSION,
    }
    return payload


def _system_prompt() -> str:
    return """
You are auditing sample-level facts for HfO2-FerroKG. Return exactly one JSON object.

Decide whether the linked property is safe for modeling and evidence-based
claims. Required keys:
- ai_review_status: usable_for_model, usable_for_rag_only, needs_human_review, or reject.
- risk_flags: array of specific risks. Use terms such as pr_2pr_confusion,
  unit_ambiguity, review_secondary_value, theoretical_only, figure_estimated,
  sample_property_mismatch, low_temperature_value, fatigue_after_value,
  wakeup_after_value, missing_thickness, missing_annealing, missing_electrode,
  missing_phase, non_hfo2_material.
- repair_suggestion: concise instruction to repair or verify the record.
- usable_for_model: boolean.
- usable_for_paper_claims: boolean.
- reasoning_summary: short explanation.
- evidence_text: copied evidence text supporting the audit.

Audit rules:
- Pr and 2Pr must never be mixed.
- Modeling rows need a target property, numeric value, unit, material/sample
  context, evidence text, and traceability to page or chunk.
- Review or cited secondary values can still help RAG, but should not be used
  as direct experimental training labels unless the text clearly reports the
  paper's own experiment.
- Theoretical-only facts can guide design graph mechanisms, but not
  experimental benchmark targets.
""".strip()


def _user_prompt(row: dict[str, Any]) -> str:
    payload = {
        "link_id": row.get("link_id"),
        "source_kind": row.get("source_kind"),
        "source_id": row.get("source_id"),
        "paper_id": row.get("paper_id"),
        "pdf_id": row.get("pdf_id"),
        "page_number": row.get("page_number"),
        "title": row.get("title"),
        "doi": row.get("doi"),
        "context_quality": row.get("context_quality"),
        "context_score": row.get("context_score"),
        "material": row.get("material"),
        "sample": row.get("sample"),
        "phase": row.get("phase"),
        "property": row.get("property"),
        "variable_roles": row.get("variable_roles"),
        "evidence_text": row.get("evidence_text"),
        "chunk_text": row.get("chunk_text"),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _load_rows(
    limit: int | None = None,
    skip_existing: bool = True,
    high_value_only: bool = True,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    init_ai_audit_tables(db_path=db_path)
    where = ["spl.status = 'linked'"]
    if skip_existing:
        where.append("NOT EXISTS (SELECT 1 FROM ai_fact_audits afa WHERE afa.link_id = spl.link_id)")
    sql = f"""
        SELECT spl.*, p.title, p.doi, p.year, dc.text AS chunk_text
        FROM sample_property_links spl
        LEFT JOIN papers p ON p.paper_id = spl.paper_id
        LEFT JOIN document_chunks dc ON dc.chunk_id = spl.chunk_id
        WHERE {' AND '.join(where)}
        ORDER BY
            CASE spl.context_quality
                WHEN 'weak' THEN 0
                WHEN 'partial' THEN 1
                WHEN 'strong' THEN 2
                ELSE 3
            END,
            spl.created_at
    """
    params: list[Any] = []
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    with connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        prop = _safe_loads(row["property_json"])
        if high_value_only and prop.get("property_name") not in HIGH_VALUE_TARGETS:
            continue
        out.append(
            {
                **dict(row),
                "material": _safe_loads(row["material_json"]),
                "sample": _safe_loads(row["sample_json"]),
                "phase": _safe_loads(row["phase_json"]),
                "property": prop,
                "variable_roles": _safe_loads(row["variable_roles_json"]),
                "chunk_text": (row["chunk_text"] or "")[:2500],
            }
        )
    return out


def _audit_row(row: dict[str, Any], model: str | None = None) -> dict[str, Any]:
    result = llm_json_chat(
        _system_prompt(),
        _user_prompt(row),
        model=model,
        max_tokens=1800,
        enable_thinking=True,
    )
    payload = normalize_ai_audit_payload(result.payload, row)
    payload["llm_usage"] = result.usage
    payload["model_name"] = result.model
    return payload


def _write_audits(audits: list[dict[str, Any]], db_path: Path | None = None) -> None:
    if not audits:
        return
    init_ai_audit_tables(db_path=db_path)
    with connect(db_path) as conn:
        for audit in audits:
            conn.execute(
                """
                INSERT INTO ai_fact_audits (
                    audit_id, link_id, source_kind, source_id, paper_id, sample_id,
                    ai_review_status, risk_flags_json, repair_suggestion,
                    usable_for_model, usable_for_paper_claims, reasoning_summary,
                    evidence_text, model_name, llm_usage_json, audit_version,
                    status, error_message, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(link_id) DO UPDATE SET
                    ai_review_status = excluded.ai_review_status,
                    risk_flags_json = excluded.risk_flags_json,
                    repair_suggestion = excluded.repair_suggestion,
                    usable_for_model = excluded.usable_for_model,
                    usable_for_paper_claims = excluded.usable_for_paper_claims,
                    reasoning_summary = excluded.reasoning_summary,
                    evidence_text = excluded.evidence_text,
                    model_name = excluded.model_name,
                    llm_usage_json = excluded.llm_usage_json,
                    audit_version = excluded.audit_version,
                    status = excluded.status,
                    error_message = excluded.error_message,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    f"audit_{uuid.uuid5(uuid.NAMESPACE_URL, audit['link_id']).hex[:16]}",
                    audit["link_id"],
                    audit.get("source_kind"),
                    audit.get("source_id"),
                    audit.get("paper_id"),
                    audit.get("sample_id"),
                    audit["ai_review_status"],
                    _json(audit.get("risk_flags") or []),
                    audit.get("repair_suggestion") or "",
                    int(bool(audit.get("usable_for_model"))),
                    int(bool(audit.get("usable_for_paper_claims"))),
                    audit.get("reasoning_summary") or "",
                    audit.get("evidence_text") or "",
                    audit.get("model_name") or "",
                    _json(audit.get("llm_usage") or {}),
                    AI_AUDIT_VERSION,
                    audit.get("status") or "ok",
                    audit.get("error_message"),
                ),
            )
        conn.commit()


def audit_sample_links_with_ai(
    limit: int | None = None,
    model: str | None = None,
    reset: bool = False,
    high_value_only: bool = True,
    max_workers: int = 12,
    commit_every: int = 50,
    progress_every: int = 25,
    force_llm_when_paused: bool = False,
    db_path: Path | None = None,
) -> dict[str, Any]:
    init_ai_audit_tables(db_path=db_path)
    if reset:
        with connect(db_path) as conn:
            conn.execute("DELETE FROM ai_fact_audits")
            conn.commit()
    rows = _load_rows(
        limit=limit,
        skip_existing=not reset,
        high_value_only=high_value_only,
        db_path=db_path,
    )
    stats: dict[str, Any] = {
        "links_seen": len(rows),
        "audits_written": 0,
        "llm_failed": 0,
        "paused": 0,
        "usable_for_model": 0,
        "usable_for_rag_only": 0,
        "needs_human_review": 0,
        "reject": 0,
    }
    if read_llm_pause() and not force_llm_when_paused:
        stats["paused"] = 1
        record_pipeline_run("28_ai_audit_sample_links", "paused", stats, db_path=db_path)
        return stats
    pending: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal pending
        if not pending:
            return
        _write_audits(pending, db_path=db_path)
        for audit in pending:
            status = audit.get("ai_review_status") or "needs_human_review"
            if status in AUDIT_STATUSES:
                stats[status] += 1
        stats["audits_written"] += len(pending)
        pending = []

    def run_one(row: dict[str, Any]) -> dict[str, Any]:
        try:
            return _audit_row(row, model=model)
        except Exception as exc:
            error = str(exc)
            if is_llm_budget_error(error):
                return {"_pause_error": error, **row}
            return normalize_ai_audit_payload({}, row) | {
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
                    pause_path = write_llm_pause(result["_pause_error"], {"pipeline": "28_ai_audit_sample_links"})
                    stats["pause_file"] = str(pause_path)
                    for pending_future in futures:
                        pending_future.cancel()
                    futures.clear()
                    break
                if result.get("status") == "error":
                    stats["llm_failed"] += 1
                pending.append(result)
                if progress_every and (stats["audits_written"] + len(pending)) % progress_every == 0:
                    print(
                        f"ai_audits processed={stats['audits_written'] + len(pending)} "
                        f"written={stats['audits_written']} errors={stats['llm_failed']} paused={stats['paused']}",
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
        "28_ai_audit_sample_links",
        "paused" if stats.get("paused") else "ok",
        stats,
        db_path=db_path,
    )
    return stats
