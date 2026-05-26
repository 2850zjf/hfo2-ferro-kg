from __future__ import annotations

import json
import math
import re
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from backend.core.config import get_llm_api_key, get_settings
from backend.db.session import connect
from backend.services.llm_extractor import json_loads_object, normalize_base_url
from backend.services.llm_quota_guard import is_llm_budget_error, read_llm_pause, write_llm_pause
from backend.services.pipeline_log import record_pipeline_run


LINKER_VERSION = "sample-linker-v0.1"
ACCEPTED_STATUSES = {"approved", "preapproved_machine", "needs_human_review"}


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


def _safe_float(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def init_sample_link_tables(db_path: Path | None = None) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sample_property_links (
                link_id TEXT PRIMARY KEY,
                source_kind TEXT NOT NULL,
                source_id TEXT NOT NULL,
                paper_id TEXT NOT NULL,
                pdf_id TEXT NOT NULL,
                chunk_id TEXT,
                page_number INTEGER,
                sample_id TEXT NOT NULL,
                material_json TEXT NOT NULL,
                sample_json TEXT NOT NULL,
                phase_json TEXT NOT NULL,
                property_json TEXT NOT NULL,
                evidence_text TEXT,
                variable_roles_json TEXT NOT NULL,
                context_quality TEXT NOT NULL,
                context_score REAL NOT NULL,
                linkage_method TEXT NOT NULL,
                linker_version TEXT NOT NULL,
                llm_usage_json TEXT,
                status TEXT DEFAULT 'linked',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source_kind, source_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sample_property_links_sample ON sample_property_links(sample_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sample_property_links_paper ON sample_property_links(paper_id, pdf_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sample_property_links_quality ON sample_property_links(context_quality)"
        )
        conn.commit()


def _has_any(mapping: dict[str, Any], keys: list[str]) -> bool:
    return any(mapping.get(key) not in (None, "", []) for key in keys)


def _context_score(
    material: dict[str, Any],
    sample: dict[str, Any],
    phase: dict[str, Any],
    prop: dict[str, Any],
    evidence_text: str,
    page_number: int | None,
    chunk_id: str | None,
) -> tuple[float, str]:
    score = 0.0
    if material.get("canonical_name") or material.get("raw_name") or material.get("material_family"):
        score += 0.18
    if prop.get("property_name") and prop.get("value") is not None:
        score += 0.22
    if prop.get("unit") or prop.get("normalized_unit"):
        score += 0.08
    if evidence_text:
        score += 0.14
    if page_number or chunk_id:
        score += 0.1
    if _has_any(sample, ["film_thickness_nm", "deposition_method", "device_stack"]):
        score += 0.1
    if _has_any(sample, ["annealing_temperature_c", "annealing_time_s", "annealing_atmosphere"]):
        score += 0.08
    if _has_any(sample, ["top_electrode", "bottom_electrode", "electrode_stack"]):
        score += 0.05
    if phase.get("phase_name") or phase.get("space_group"):
        score += 0.05
    score = min(1.0, round(score, 3))
    if score >= 0.75:
        return score, "strong"
    if score >= 0.45:
        return score, "partial"
    return score, "weak"


def _sample_signature(
    paper_id: str,
    material: dict[str, Any],
    sample: dict[str, Any],
    phase: dict[str, Any],
) -> str:
    parts = [
        paper_id,
        str(material.get("canonical_name") or material.get("raw_name") or material.get("material_family") or "material"),
        str(sample.get("film_thickness_nm") or ""),
        str(sample.get("device_stack") or sample.get("electrode_stack") or ""),
        str(sample.get("annealing_temperature_c") or ""),
        str(sample.get("annealing_time_s") or ""),
        str(sample.get("annealing_atmosphere") or ""),
        str(phase.get("phase_name") or phase.get("space_group") or ""),
    ]
    return "|".join(parts)


def _sample_id(signature: str) -> str:
    return f"sample_{uuid.uuid5(uuid.NAMESPACE_URL, signature).hex[:16]}"


def _roles(sample: dict[str, Any], prop: dict[str, Any], phase: dict[str, Any]) -> dict[str, Any]:
    controllable = []
    for key in [
        "film_thickness_nm",
        "deposition_method",
        "annealing_temperature_c",
        "annealing_time_s",
        "annealing_atmosphere",
        "top_electrode",
        "bottom_electrode",
        "device_stack",
        "substrate",
        "device_type",
    ]:
        if sample.get(key) not in (None, "", []):
            controllable.append(key)
    mechanisms = []
    if phase.get("phase_name") or phase.get("space_group"):
        mechanisms.append("phase_structure")
    constraints = []
    if sample.get("annealing_temperature_c"):
        constraints.append("thermal_budget")
    if sample.get("film_thickness_nm"):
        constraints.append("thickness_window")
    return {
        "controllable_variables": controllable,
        "target_properties": [prop.get("property_name")] if prop.get("property_name") else [],
        "mechanism_variables": mechanisms,
        "constraint_variables": constraints,
        "evidence_required": ["paper_id", "page_number", "evidence_text"],
    }


def _reviewed_fact_records(db_path: Path | None = None) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in ACCEPTED_STATUSES)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT rf.fact_id, rf.paper_id, rf.pdf_id, rf.chunk_id, rf.page_number,
                   rf.payload_json, dc.text AS chunk_text
            FROM reviewed_facts rf
            LEFT JOIN document_chunks dc ON dc.chunk_id = rf.chunk_id
            WHERE rf.review_status IN ({placeholders})
            """,
            tuple(sorted(ACCEPTED_STATUSES)),
        ).fetchall()
    records: list[dict[str, Any]] = []
    for row in rows:
        payload = _safe_loads(row["payload_json"])
        prop = payload.get("property") or {}
        if not prop.get("property_name"):
            continue
        material = payload.get("material") or {}
        sample = payload.get("sample") or {}
        phase = (_as_list(payload.get("phases")) or [{}])[0] or {}
        evidence_text = prop.get("evidence_text") or ""
        score, quality = _context_score(material, sample, phase, prop, evidence_text, row["page_number"], row["chunk_id"])
        signature = _sample_signature(row["paper_id"], material, sample, phase)
        records.append(
            {
                "source_kind": "reviewed_fact",
                "source_id": row["fact_id"],
                "paper_id": row["paper_id"],
                "pdf_id": row["pdf_id"],
                "chunk_id": row["chunk_id"],
                "page_number": row["page_number"],
                "sample_id": _sample_id(signature),
                "material": material,
                "sample": sample,
                "phase": phase,
                "property": prop,
                "evidence_text": evidence_text,
                "variable_roles": _roles(sample, prop, phase),
                "context_quality": quality,
                "context_score": score,
                "linkage_method": "rules_from_reviewed_fact",
                "chunk_text": row["chunk_text"] or "",
                "llm_usage": {},
            }
        )
    return records


def _benchmark_records(db_path: Path | None = None) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT be.extraction_id, be.paper_id, be.pdf_id, be.chunk_id,
                   be.page_number, be.payload_json, dc.text AS chunk_text
            FROM benchmark_extractions be
            LEFT JOIN document_chunks dc ON dc.chunk_id = be.chunk_id
            WHERE be.status = 'ok'
            """
        ).fetchall()
    records: list[dict[str, Any]] = []
    for row in rows:
        payload = _safe_loads(row["payload_json"])
        for index, record in enumerate(_as_list(payload.get("benchmark_records"))):
            if not isinstance(record, dict):
                continue
            inputs = record.get("input_variables") if isinstance(record.get("input_variables"), dict) else {}
            outputs = record.get("output_targets") if isinstance(record.get("output_targets"), dict) else {}
            if not outputs:
                continue
            material = {
                "canonical_name": inputs.get("material_name") or inputs.get("material") or "",
                "material_family": inputs.get("material_family") or "",
                "formula": inputs.get("formula") or "",
                "dopant_elements": inputs.get("dopants") or inputs.get("dopant_elements") or [],
                "zr_fraction": inputs.get("zr_fraction"),
            }
            sample = {
                "film_thickness_nm": inputs.get("film_thickness_nm") or inputs.get("thickness_nm"),
                "deposition_method": inputs.get("deposition_method"),
                "annealing_temperature_c": inputs.get("annealing_temperature_c"),
                "annealing_time_s": inputs.get("annealing_time_s"),
                "annealing_atmosphere": inputs.get("annealing_atmosphere"),
                "top_electrode": inputs.get("top_electrode"),
                "bottom_electrode": inputs.get("bottom_electrode"),
                "device_stack": inputs.get("device_stack") or inputs.get("electrode_stack"),
                "substrate": inputs.get("substrate"),
                "device_type": inputs.get("device_type"),
                "wake_up_or_endurance_state": inputs.get("wake_up_or_endurance_state"),
            }
            phase = {"phase_name": inputs.get("phase") or inputs.get("phase_name"), "space_group": inputs.get("space_group")}
            for target_name, target in outputs.items():
                if isinstance(target, dict):
                    value = target.get("value")
                    unit = target.get("unit") or ""
                else:
                    value = target
                    unit = ""
                prop = {
                    "property_name": str(target_name),
                    "raw_property_name": str(target_name),
                    "value": _safe_float(value),
                    "unit": unit,
                    "evidence_text": record.get("evidence_text") or "",
                    "condition": record.get("conditions") or {},
                }
                source_id = f"{row['extraction_id']}_{index}_{target_name}"
                evidence_text = prop["evidence_text"]
                score, quality = _context_score(
                    material, sample, phase, prop, evidence_text, row["page_number"], row["chunk_id"]
                )
                signature = _sample_signature(row["paper_id"], material, sample, phase)
                records.append(
                    {
                        "source_kind": "benchmark_record",
                        "source_id": source_id,
                        "paper_id": row["paper_id"],
                        "pdf_id": row["pdf_id"],
                        "chunk_id": row["chunk_id"],
                        "page_number": row["page_number"],
                        "sample_id": _sample_id(signature),
                        "material": material,
                        "sample": sample,
                        "phase": phase,
                        "property": prop,
                        "evidence_text": evidence_text,
                        "variable_roles": _roles(sample, prop, phase),
                        "context_quality": quality,
                        "context_score": score,
                        "linkage_method": "rules_from_benchmark_record",
                        "chunk_text": row["chunk_text"] or "",
                        "llm_usage": {},
                    }
                )
    return records


def _system_prompt() -> str:
    return """
You are a sample-level fact linker for HfO2 ferroelectric materials.
Given an extracted property/fact and its original chunk text, reconstruct the sample-level context.

Return one JSON object with:
- material: canonical_name, material_family, formula, dopant_elements, zr_fraction.
- sample: film_thickness_nm, deposition_method, annealing_temperature_c, annealing_time_s,
  annealing_atmosphere, top_electrode, bottom_electrode, device_stack, substrate, device_type,
  wake_up_or_endurance_state.
- phase: phase_name, space_group, characterization_method.
- property: property_name, raw_property_name, value, unit, condition.
- variable_roles: controllable_variables, target_properties, mechanism_variables, constraint_variables.
- context_quality: strong, partial, weak, or ambiguous.
- context_score: number from 0 to 1.
- evidence_text: exact sentence/table fragment supporting the linked sample-property relation.
- warnings: array of strings.

Rules:
- Bind one property value to the matching material, thickness, annealing, electrode stack, device, and phase only when supported.
- Do not invent missing values. Use null for unknown fields.
- Keep Pr and 2Pr separate.
- If the value appears to be from review/secondary literature, figure-estimated, normalized footprint, percentage, or a ratio, warn explicitly.
- Evidence must be copied from the provided text.
""".strip()


def _llm_link_record(record: dict[str, Any], model: str | None = None) -> dict[str, Any]:
    if not get_llm_api_key():
        return {**record, "linkage_method": record["linkage_method"] + "+no_llm_key"}
    settings = get_settings()
    selected_model = model or settings.llm_model
    base_url, _ = normalize_base_url(settings.llm_provider, settings.llm_base_url)
    from openai import OpenAI

    kwargs: dict[str, Any] = {"api_key": get_llm_api_key(), "timeout": settings.llm_timeout_seconds}
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)
    prompt_payload = {
        key: record.get(key)
        for key in [
            "source_kind",
            "source_id",
            "paper_id",
            "pdf_id",
            "chunk_id",
            "page_number",
            "material",
            "sample",
            "phase",
            "property",
            "evidence_text",
            "chunk_text",
        ]
    }
    request_kwargs: dict[str, Any] = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": json.dumps(prompt_payload, ensure_ascii=False)},
        ],
        "temperature": 0,
        "max_tokens": min(settings.llm_max_tokens, 2400),
        "response_format": {"type": "json_object"},
    }
    if settings.llm_provider == "dashscope":
        request_kwargs["extra_body"] = {"enable_thinking": settings.llm_enable_thinking}
    last_error: Exception | None = None
    response = None
    for attempt in range(1, 4):
        try:
            response = client.chat.completions.create(**request_kwargs)
            break
        except Exception as exc:
            last_error = exc
            message = str(exc).lower()
            if attempt < 3 and (
                "connection" in message
                or "timeout" in message
                or "timed out" in message
                or "temporarily" in message
            ):
                import time

                time.sleep(1.5 * attempt)
                continue
            raise
    if response is None:
        raise last_error or RuntimeError("LLM response is empty")
    data = json_loads_object(response.choices[0].message.content or "{}")
    usage = getattr(response, "usage", None)
    material = data.get("material") if isinstance(data.get("material"), dict) else record["material"]
    sample = data.get("sample") if isinstance(data.get("sample"), dict) else record["sample"]
    phase = data.get("phase") if isinstance(data.get("phase"), dict) else record["phase"]
    prop = data.get("property") if isinstance(data.get("property"), dict) else record["property"]
    evidence_text = str(data.get("evidence_text") or record["evidence_text"] or "")
    score = _safe_float(data.get("context_score"))
    quality = str(data.get("context_quality") or "")
    if score is None or quality not in {"strong", "partial", "weak", "ambiguous"}:
        score, quality = _context_score(material, sample, phase, prop, evidence_text, record["page_number"], record["chunk_id"])
    signature = _sample_signature(record["paper_id"], material, sample, phase)
    return {
        **record,
        "sample_id": _sample_id(signature),
        "material": material,
        "sample": sample,
        "phase": phase,
        "property": prop,
        "evidence_text": evidence_text,
        "variable_roles": data.get("variable_roles") if isinstance(data.get("variable_roles"), dict) else _roles(sample, prop, phase),
        "context_quality": quality,
        "context_score": score,
        "linkage_method": "llm_sample_linker",
        "llm_usage": {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }
        if usage is not None
        else {},
    }


def _write_links(records: list[dict[str, Any]], reset: bool, db_path: Path | None = None) -> None:
    init_sample_link_tables(db_path=db_path)
    with connect(db_path) as conn:
        if reset:
            conn.execute("DELETE FROM sample_property_links")
        for record in records:
            conn.execute(
                """
                INSERT INTO sample_property_links (
                    link_id, source_kind, source_id, paper_id, pdf_id, chunk_id,
                    page_number, sample_id, material_json, sample_json, phase_json,
                    property_json, evidence_text, variable_roles_json, context_quality,
                    context_score, linkage_method, linker_version, llm_usage_json, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_kind, source_id) DO UPDATE SET
                    sample_id = excluded.sample_id,
                    material_json = excluded.material_json,
                    sample_json = excluded.sample_json,
                    phase_json = excluded.phase_json,
                    property_json = excluded.property_json,
                    evidence_text = excluded.evidence_text,
                    variable_roles_json = excluded.variable_roles_json,
                    context_quality = excluded.context_quality,
                    context_score = excluded.context_score,
                    linkage_method = excluded.linkage_method,
                    linker_version = excluded.linker_version,
                    llm_usage_json = excluded.llm_usage_json,
                    status = excluded.status
                """,
                (
                    f"link_{uuid.uuid5(uuid.NAMESPACE_URL, record['source_kind'] + record['source_id']).hex[:16]}",
                    record["source_kind"],
                    record["source_id"],
                    record["paper_id"],
                    record["pdf_id"],
                    record.get("chunk_id"),
                    record.get("page_number"),
                    record["sample_id"],
                    _json(record["material"]),
                    _json(record["sample"]),
                    _json(record["phase"]),
                    _json(record["property"]),
                    record.get("evidence_text") or "",
                    _json(record["variable_roles"]),
                    record["context_quality"],
                    float(record["context_score"]),
                    record["linkage_method"],
                    LINKER_VERSION,
                    _json(record.get("llm_usage") or {}),
                    "linked",
                ),
            )
        conn.commit()


def _clear_links(db_path: Path | None = None) -> None:
    init_sample_link_tables(db_path=db_path)
    with connect(db_path) as conn:
        conn.execute("DELETE FROM sample_property_links")
        conn.commit()


def _existing_sources(db_path: Path | None = None) -> set[tuple[str, str]]:
    init_sample_link_tables(db_path=db_path)
    with connect(db_path) as conn:
        rows = conn.execute("SELECT source_kind, source_id FROM sample_property_links").fetchall()
    return {(row["source_kind"], row["source_id"]) for row in rows}


def build_sample_property_links(
    db_path: Path | None = None,
    reset: bool = False,
    include_benchmark: bool = True,
    use_llm: bool = True,
    force_llm_when_paused: bool = False,
    model: str | None = None,
    max_workers: int = 8,
    limit: int | None = None,
    progress_every: int = 25,
    commit_every: int = 100,
) -> dict[str, Any]:
    init_sample_link_tables(db_path=db_path)
    if reset:
        _clear_links(db_path=db_path)
    records = _reviewed_fact_records(db_path=db_path)
    if include_benchmark:
        records.extend(_benchmark_records(db_path=db_path))
    if limit is not None:
        records = records[:limit]
    if not reset:
        existing = _existing_sources(db_path=db_path)
        records = [
            record
            for record in records
            if (record["source_kind"], record["source_id"]) not in existing
        ]

    stats: dict[str, Any] = {
        "records_seen": len(records),
        "linked": 0,
        "llm_used": 0,
        "llm_failed": 0,
        "paused": 0,
        "strong": 0,
        "partial": 0,
        "weak": 0,
        "ambiguous": 0,
    }
    pause = read_llm_pause()
    llm_allowed = bool(use_llm and (force_llm_when_paused or not pause))
    linked_records: list[dict[str, Any]] = []

    if not llm_allowed:
        linked_records = records
        _write_links(linked_records, reset=False, db_path=db_path)
    else:
        pending_writes: list[dict[str, Any]] = []

        def flush_pending() -> None:
            nonlocal pending_writes
            if pending_writes:
                _write_links(pending_writes, reset=False, db_path=db_path)
                pending_writes = []

        def run_one(record: dict[str, Any]) -> dict[str, Any]:
            try:
                return _llm_link_record(record, model=model)
            except Exception as exc:
                error = str(exc)
                if is_llm_budget_error(error):
                    return {"_pause_error": error, **record}
                return {"_llm_error": error, **record}

        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
            futures = set()
            index = 0

            def submit_next() -> None:
                nonlocal index
                if index < len(records):
                    futures.add(pool.submit(run_one, records[index]))
                    index += 1

            for _ in range(min(max(1, max_workers), len(records))):
                submit_next()
            while futures:
                done, futures = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    result = future.result()
                    if "_pause_error" in result:
                        stats["paused"] = 1
                        stats["llm_failed"] += 1
                        pause_path = write_llm_pause(
                            result["_pause_error"],
                            {
                                "pipeline": "23_link_sample_facts",
                                "source_kind": result.get("source_kind"),
                                "source_id": result.get("source_id"),
                            },
                        )
                        stats["pause_file"] = str(pause_path)
                        linked_records.append(result)
                        pending_writes.append(result)
                        for pending in futures:
                            pending.cancel()
                        futures.clear()
                        break
                    if "_llm_error" in result:
                        stats["llm_failed"] += 1
                        result["linkage_method"] = result.get("linkage_method", "rules") + "+llm_failed"
                    else:
                        stats["llm_used"] += 1
                    linked_records.append(result)
                    pending_writes.append(result)
                    if progress_every and len(linked_records) % progress_every == 0:
                        print(
                            f"sample_links processed={len(linked_records)} llm_used={stats['llm_used']} "
                            f"llm_failed={stats['llm_failed']} paused={stats['paused']}",
                            flush=True,
                        )
                    if commit_every and len(pending_writes) >= commit_every:
                        flush_pending()
                    if not stats.get("paused"):
                        submit_next()
                if stats.get("paused"):
                    break
            flush_pending()
        if stats.get("paused") and len(linked_records) < len(records):
            # Preserve all local-rule links so downstream design graph can still work.
            seen = {(item["source_kind"], item["source_id"]) for item in linked_records}
            fallback_records = [
                item for item in records if (item["source_kind"], item["source_id"]) not in seen
            ]
            linked_records.extend(fallback_records)
            _write_links(fallback_records, reset=False, db_path=db_path)

    stats["linked"] = len(linked_records)
    for record in linked_records:
        quality = record.get("context_quality") or "weak"
        stats[quality if quality in {"strong", "partial", "weak", "ambiguous"} else "weak"] += 1
    record_pipeline_run(
        "23_link_sample_facts",
        "paused" if stats.get("paused") else "ok",
        stats,
        db_path=db_path,
    )
    return stats
