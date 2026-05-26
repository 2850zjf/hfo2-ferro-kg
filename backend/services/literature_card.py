from __future__ import annotations

import json
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.llm_json_client import llm_json_chat
from backend.services.llm_quota_guard import is_llm_budget_error, read_llm_pause, write_llm_pause
from backend.services.pipeline_log import record_pipeline_run


LITERATURE_CARD_VERSION = "literature-card-v0.1"


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def init_literature_card_tables(db_path: Path | None = None) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_literature_cards (
                card_id TEXT PRIMARY KEY,
                paper_id TEXT,
                pdf_id TEXT NOT NULL,
                title TEXT,
                doi TEXT,
                year INTEGER,
                research_type TEXT,
                material_systems_json TEXT NOT NULL,
                key_properties_json TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                evidence_text TEXT,
                confidence REAL,
                model_name TEXT,
                llm_usage_json TEXT,
                card_version TEXT NOT NULL,
                status TEXT DEFAULT 'ok',
                error_message TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(pdf_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_literature_cards_paper ON llm_literature_cards(paper_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_literature_cards_type ON llm_literature_cards(research_type)"
        )
        conn.commit()


def normalize_literature_card_payload(data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    title = str(data.get("title") or context.get("title") or "").strip()
    doi = str(data.get("doi") or context.get("doi") or "").strip()
    year = data.get("year") or context.get("year") or ""
    try:
        year = int(year) if year not in (None, "") else None
    except (TypeError, ValueError):
        year = None
    research_type = str(data.get("research_type") or data.get("paper_type") or context.get("paper_type") or "unknown")
    payload = {
        "paper_id": context.get("paper_id"),
        "pdf_id": context.get("pdf_id"),
        "title": title,
        "doi": doi,
        "year": year,
        "research_type": research_type,
        "paper_type": data.get("paper_type") or context.get("paper_type") or "unknown",
        "is_review": bool(data.get("is_review", False)),
        "is_theoretical": bool(data.get("is_theoretical", False)),
        "is_experimental": bool(data.get("is_experimental", False)),
        "material_systems": _as_list(data.get("material_systems")),
        "core_samples": _as_list(data.get("core_samples")),
        "main_properties": _as_list(data.get("main_properties")),
        "key_tables_figures": _as_list(data.get("key_tables_figures")),
        "mechanism_or_design_rules": _as_list(data.get("mechanism_or_design_rules")),
        "quality_warnings": [str(item) for item in _as_list(data.get("quality_warnings")) if str(item).strip()],
        "evidence_text": str(data.get("evidence_text") or "").strip(),
        "page_numbers": _as_list(data.get("page_numbers")),
        "confidence": data.get("confidence", 0.0),
        "card_version": LITERATURE_CARD_VERSION,
    }
    try:
        payload["confidence"] = max(0.0, min(1.0, float(payload["confidence"])))
    except (TypeError, ValueError):
        payload["confidence"] = 0.0
    return payload


def _system_prompt() -> str:
    return """
You are building paper-level literature cards for HfO2-FerroKG.
Read the provided metadata, first pages, and high-value chunks. Return only one
JSON object. The card must be evidence-grounded and useful for materials design.

Required JSON keys:
- title: best paper title from the original text.
- doi: DOI if visible, otherwise empty string.
- year: integer or null.
- research_type: one of experimental, theoretical, computational, review,
  mixed, dataset, unknown.
- paper_type: short paper class.
- is_review, is_theoretical, is_experimental: booleans.
- material_systems: array of HfO2/HZO/doped hafnia material names.
- core_samples: array of objects with material, thickness, process, electrode_stack,
  device_type, phase, evidence_text.
- main_properties: array of objects with property_name, value, unit,
  condition, evidence_text.
- key_tables_figures: array of objects with label, content_type, why_important,
  evidence_text.
- mechanism_or_design_rules: array of objects with rule_or_mechanism,
  variable, target, evidence_text.
- quality_warnings: array of strings for review values, theory-only values,
  ambiguous sample-property links, or missing context.
- evidence_text: short sentence proving the card classification.
- page_numbers: array of page numbers used.
- confidence: number from 0 to 1.

Rules:
- Do not invent missing title, DOI, values, or sample conditions.
- Keep Pr and 2Pr separate.
- Treat review citations and theoretical claims as useful guidance, but not as
  direct experimental benchmark values.
""".strip()


def _user_prompt(context: dict[str, Any]) -> str:
    return f"""
metadata:
paper_id: {context.get('paper_id')}
pdf_id: {context.get('pdf_id')}
file_name: {context.get('file_name')}
title_from_db: {context.get('title')}
doi_from_db: {context.get('doi')}
year_from_db: {context.get('year')}
paper_type_from_db: {context.get('paper_type')}

first_pages:
{context.get('first_pages_text', '')}

high_value_chunks:
{context.get('high_value_chunks_text', '')}
""".strip()


def _load_contexts(limit: int | None = None, db_path: Path | None = None, skip_existing: bool = True) -> list[dict[str, Any]]:
    init_literature_card_tables(db_path=db_path)
    with connect(db_path) as conn:
        sql = """
            SELECT pf.pdf_id, pf.file_name, pf.paper_id, p.title, p.doi, p.year, p.paper_type
            FROM pdf_files pf
            LEFT JOIN papers p ON p.paper_id = pf.paper_id
        """
        params: list[Any] = []
        if skip_existing:
            sql += " WHERE NOT EXISTS (SELECT 1 FROM llm_literature_cards lc WHERE lc.pdf_id = pf.pdf_id)"
        sql += " ORDER BY COALESCE(p.year, 0) DESC, pf.file_name"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        contexts: list[dict[str, Any]] = []
        for row in rows:
            first_pages = conn.execute(
                """
                SELECT page_number, text
                FROM parsed_pages
                WHERE pdf_id = ?
                ORDER BY page_number
                LIMIT 3
                """,
                (row["pdf_id"],),
            ).fetchall()
            chunks = conn.execute(
                """
                SELECT page_number, section, text
                FROM document_chunks
                WHERE pdf_id = ?
                  AND (is_high_value = 1 OR section IN ('table', 'figure_caption'))
                ORDER BY is_high_value DESC, page_number, chunk_index
                LIMIT 10
                """,
                (row["pdf_id"],),
            ).fetchall()
            first_text = "\n\n".join(
                f"[page {item['page_number']}]\n{item['text'][:3500]}" for item in first_pages
            )[:9000]
            chunk_text = "\n\n".join(
                f"[page {item['page_number']} | {item['section']}]\n{item['text'][:1600]}" for item in chunks
            )[:14000]
            contexts.append(
                {
                    "paper_id": row["paper_id"],
                    "pdf_id": row["pdf_id"],
                    "file_name": row["file_name"],
                    "title": row["title"] or "",
                    "doi": row["doi"] or "",
                    "year": row["year"],
                    "paper_type": row["paper_type"] or "unknown",
                    "first_pages_text": first_text,
                    "high_value_chunks_text": chunk_text,
                }
            )
    return contexts


def _build_card(context: dict[str, Any], model: str | None = None) -> dict[str, Any]:
    result = llm_json_chat(
        _system_prompt(),
        _user_prompt(context),
        model=model,
        max_tokens=3200,
        enable_thinking=True,
    )
    payload = normalize_literature_card_payload(result.payload, context)
    payload["llm_usage"] = result.usage
    return payload | {"model_name": result.model}


def _write_cards(cards: list[dict[str, Any]], db_path: Path | None = None) -> None:
    if not cards:
        return
    init_literature_card_tables(db_path=db_path)
    with connect(db_path) as conn:
        for card in cards:
            conn.execute(
                """
                INSERT INTO llm_literature_cards (
                    card_id, paper_id, pdf_id, title, doi, year, research_type,
                    material_systems_json, key_properties_json, payload_json,
                    evidence_text, confidence, model_name, llm_usage_json,
                    card_version, status, error_message, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(pdf_id) DO UPDATE SET
                    title = excluded.title,
                    doi = excluded.doi,
                    year = excluded.year,
                    research_type = excluded.research_type,
                    material_systems_json = excluded.material_systems_json,
                    key_properties_json = excluded.key_properties_json,
                    payload_json = excluded.payload_json,
                    evidence_text = excluded.evidence_text,
                    confidence = excluded.confidence,
                    model_name = excluded.model_name,
                    llm_usage_json = excluded.llm_usage_json,
                    card_version = excluded.card_version,
                    status = excluded.status,
                    error_message = excluded.error_message,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    f"card_{uuid.uuid5(uuid.NAMESPACE_URL, str(card.get('pdf_id'))).hex[:16]}",
                    card.get("paper_id"),
                    card["pdf_id"],
                    card.get("title") or "",
                    card.get("doi") or "",
                    card.get("year"),
                    card.get("research_type") or "unknown",
                    _json(card.get("material_systems") or []),
                    _json(card.get("main_properties") or []),
                    _json(card),
                    card.get("evidence_text") or "",
                    float(card.get("confidence") or 0),
                    card.get("model_name") or "",
                    _json(card.get("llm_usage") or {}),
                    LITERATURE_CARD_VERSION,
                    card.get("status") or "ok",
                    card.get("error_message"),
                ),
            )
        conn.commit()


def _append_jsonl(cards: list[dict[str, Any]], reset: bool) -> Path:
    output_dir = PROJECT_ROOT / "data" / "literature_cards"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "llm_literature_cards.jsonl"
    mode = "w" if reset else "a"
    with output_path.open(mode, encoding="utf-8") as fh:
        for card in cards:
            fh.write(_json(card) + "\n")
    return output_path


def build_literature_cards(
    limit: int | None = None,
    model: str | None = None,
    reset: bool = False,
    max_workers: int = 8,
    commit_every: int = 20,
    progress_every: int = 10,
    force_llm_when_paused: bool = False,
    db_path: Path | None = None,
) -> dict[str, Any]:
    init_literature_card_tables(db_path=db_path)
    if reset:
        with connect(db_path) as conn:
            conn.execute("DELETE FROM llm_literature_cards")
            conn.commit()
    contexts = _load_contexts(limit=limit, db_path=db_path, skip_existing=not reset)
    stats: dict[str, Any] = {
        "papers_seen": len(contexts),
        "cards_written": 0,
        "llm_failed": 0,
        "paused": 0,
        "output_jsonl": "",
    }
    if read_llm_pause() and not force_llm_when_paused:
        stats["paused"] = 1
        record_pipeline_run("26_build_literature_cards", "paused", stats, db_path=db_path)
        return stats
    pending: list[dict[str, Any]] = []
    first_flush = reset

    def flush() -> None:
        nonlocal pending, first_flush
        if not pending:
            return
        _write_cards(pending, db_path=db_path)
        output_path = _append_jsonl(pending, reset=first_flush)
        first_flush = False
        stats["output_jsonl"] = str(output_path)
        stats["cards_written"] += len(pending)
        pending = []

    def run_one(context: dict[str, Any]) -> dict[str, Any]:
        try:
            return _build_card(context, model=model)
        except Exception as exc:
            error = str(exc)
            if is_llm_budget_error(error):
                return {"_pause_error": error, **context}
            return {
                **normalize_literature_card_payload({}, context),
                "status": "error",
                "error_message": error[:1500],
            }

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        futures = set()
        index = 0

        def submit_next() -> None:
            nonlocal index
            if index < len(contexts):
                futures.add(pool.submit(run_one, contexts[index]))
                index += 1

        for _ in range(min(max(1, max_workers), len(contexts))):
            submit_next()
        while futures:
            done, futures = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                result = future.result()
                if "_pause_error" in result:
                    stats["paused"] = 1
                    stats["llm_failed"] += 1
                    pause_path = write_llm_pause(result["_pause_error"], {"pipeline": "26_build_literature_cards"})
                    stats["pause_file"] = str(pause_path)
                    for pending_future in futures:
                        pending_future.cancel()
                    futures.clear()
                    break
                if result.get("status") == "error":
                    stats["llm_failed"] += 1
                pending.append(result)
                if progress_every and (stats["cards_written"] + len(pending)) % progress_every == 0:
                    print(
                        f"literature_cards processed={stats['cards_written'] + len(pending)} "
                        f"written={stats['cards_written']} errors={stats['llm_failed']} paused={stats['paused']}",
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
        "26_build_literature_cards",
        "paused" if stats.get("paused") else "ok",
        stats,
        db_path=db_path,
    )
    return stats
