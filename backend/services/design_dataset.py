from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.pipeline_log import record_pipeline_run


DESIGN_TARGETS = {
    "remanent_polarization_Pr",
    "double_remanent_polarization_2Pr",
    "coercive_field_Ec",
    "endurance_cycles",
    "retention_time",
    "memory_window",
    "leakage_current_density",
}

BENCHMARK_TIERS = {
    "strong_only": "strong sample-level rows approved for modeling",
    "strong_partial": "strong or partial traceable rows that are not rejected",
    "all_traceable": "all rows with evidence and paper traceability",
}

PROPERTY_ALIASES = {
    "pr": "remanent_polarization_Pr",
    "remnant polarization": "remanent_polarization_Pr",
    "remanent polarization": "remanent_polarization_Pr",
    "remanent_polarization": "remanent_polarization_Pr",
    "2pr": "double_remanent_polarization_2Pr",
    "double remanent polarization": "double_remanent_polarization_2Pr",
    "double_remanent_polarization": "double_remanent_polarization_2Pr",
    "ec": "coercive_field_Ec",
    "coercive field": "coercive_field_Ec",
    "coercive_field": "coercive_field_Ec",
    "endurance": "endurance_cycles",
    "retention": "retention_time",
    "memory window": "memory_window",
    "memory_window": "memory_window",
    "leakage": "leakage_current_density",
    "leakage current density": "leakage_current_density",
}

SECONDS_PER_YEAR = 365 * 24 * 3600


def _unit_text(unit: Any) -> str:
    return (
        str(unit or "")
        .strip()
        .lower()
        .replace("µ", "μ")
        .replace("−", "-")
        .replace("‒", "-")
        .replace("–", "-")
        .replace("⁻", "-")
        .replace("²", "2")
        .replace("·", " ")
    )


def _normalize_model_target(property_name: str, value: Any, unit: Any) -> dict[str, Any]:
    numeric = _safe_float(value)
    unit_text = _unit_text(unit)
    if numeric is None:
        return {
            "model_target_value": "",
            "model_target_unit": "",
            "model_include": 0,
            "model_exclusion_reason": "missing_numeric_value",
        }

    normalized = numeric
    normalized_unit = str(unit or "")
    reason = ""

    if property_name in {"remanent_polarization_Pr", "double_remanent_polarization_2Pr"}:
        normalized_unit = "μC/cm²"
        if "c/m2" in unit_text or "c m-2" in unit_text:
            normalized = numeric * 100
        elif "mc/cm" in unit_text or "mc cm" in unit_text:
            normalized = numeric * 1000
        elif "%" in unit_text or "μm" in unit_text:
            reason = "invalid_polarization_unit"
        upper = 100 if property_name == "remanent_polarization_Pr" else 200
        if normalized <= 0 or normalized > upper:
            reason = reason or "polarization_out_of_range"

    elif property_name == "coercive_field_Ec":
        normalized_unit = "MV/cm"
        if "kv/cm" in unit_text or "kv cm" in unit_text:
            normalized = numeric / 1000
        elif "v/m" in unit_text:
            normalized = numeric / 100_000_000
        elif "mv/cm" in unit_text or "mv cm" in unit_text:
            normalized = numeric
        else:
            reason = "invalid_ec_unit"
        if normalized <= 0 or normalized > 10:
            reason = reason or "ec_out_of_range"

    elif property_name == "endurance_cycles":
        normalized_unit = "cycles"
        if any(token in unit_text for token in ["cycle", "cycles", "pulse", "pulses"]):
            normalized = numeric
        else:
            reason = "invalid_endurance_unit"
        if normalized <= 0 or normalized > 1e15:
            reason = reason or "endurance_out_of_range"

    elif property_name == "retention_time":
        normalized_unit = "s"
        if unit_text in {"s", "sec", "secs", "second", "seconds"}:
            normalized = numeric
        elif unit_text in {"min", "mins", "minute", "minutes"}:
            normalized = numeric * 60
        elif unit_text in {"h", "hr", "hrs", "hour", "hours"}:
            normalized = numeric * 3600
        elif unit_text in {"day", "days"}:
            normalized = numeric * 86400
        elif unit_text in {"y", "yr", "year", "years"}:
            normalized = numeric * SECONDS_PER_YEAR
        else:
            reason = "invalid_retention_unit"
        if normalized <= 0 or normalized > 100 * SECONDS_PER_YEAR:
            reason = reason or "retention_out_of_range"

    elif property_name == "memory_window":
        normalized_unit = "V"
        if unit_text == "v":
            normalized = numeric
        elif unit_text == "mv":
            normalized = numeric / 1000
        else:
            reason = "invalid_memory_window_unit"
        if normalized <= 0 or normalized > 20:
            reason = reason or "memory_window_out_of_range"

    elif property_name == "leakage_current_density":
        normalized_unit = "A/cm²"
        if "ma/cm" in unit_text or "ma cm" in unit_text:
            normalized = numeric * 1e-3
        elif "μa/cm" in unit_text or "ua/cm" in unit_text or "μa cm" in unit_text or "ua cm" in unit_text:
            normalized = numeric * 1e-6
        elif "na/cm" in unit_text or "na cm" in unit_text:
            normalized = numeric * 1e-9
        elif "a/cm" in unit_text or "a cm" in unit_text or "log(a/cm" in unit_text:
            normalized = numeric
        else:
            reason = "invalid_leakage_unit"
        if normalized <= 0 or normalized > 1e3:
            reason = reason or "leakage_out_of_range"

    include = 0 if reason else 1
    return {
        "model_target_value": normalized if include else "",
        "model_target_unit": normalized_unit if include else "",
        "model_include": include,
        "model_exclusion_reason": reason,
    }


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _canonical_property_name(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    lowered = raw.lower().replace("μ", "u").replace("µ", "u")
    compact = lowered.replace("-", "_").replace(" ", "_")
    if raw in DESIGN_TARGETS:
        return raw
    if lowered in PROPERTY_ALIASES:
        return PROPERTY_ALIASES[lowered]
    if compact in PROPERTY_ALIASES:
        return PROPERTY_ALIASES[compact]
    return raw


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True) if value not in (None, "") else ""


def _read_json_dict(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _read_json_list(value: Any) -> list[Any]:
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value))
    except Exception:
        parsed = None
    if isinstance(parsed, list):
        return parsed
    return [value]


def _has_traceability(row: dict[str, Any]) -> bool:
    return bool(
        row.get("paper_id")
        and (row.get("page_number") not in (None, ""))
        and str(row.get("evidence_text") or "").strip()
    )


def _assign_benchmark_tier(row: dict[str, Any]) -> str:
    if not _has_traceability(row):
        return ""
    ai_status = str(row.get("ai_review_status") or "")
    if ai_status == "reject":
        return ""
    quality = str(row.get("review_status") or row.get("context_quality") or "")
    source = str(row.get("source") or "")
    usable = str(row.get("usable_for_model") or "").lower() in {"1", "true", "yes"}
    if source == "sample_property_links":
        if quality == "strong" and (usable or not ai_status):
            return "strong_only"
        if quality in {"strong", "partial"} and ai_status != "reject":
            return "strong_partial"
        return "all_traceable"
    if source == "reviewed_facts" and row.get("review_status") == "approved":
        return "strong_partial"
    return "all_traceable"


def _enrich_row_for_tiers(row: dict[str, Any]) -> dict[str, Any]:
    row.setdefault("ai_review_status", "")
    row.setdefault("usable_for_model", "")
    row.setdefault("risk_flags", "")
    row["benchmark_tier"] = _assign_benchmark_tier(row)
    ai_status = str(row.get("ai_review_status") or "")
    if ai_status in {"needs_human_review", "reject", "usable_for_rag_only"}:
        row["model_include"] = 0
        if not row.get("model_exclusion_reason"):
            row["model_exclusion_reason"] = ai_status
    return row


def _accepted_reviewed_fact_rows(db_path: Path | None = None) -> list[dict[str, Any]]:
    rows_out: list[dict[str, Any]] = []
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT rf.fact_id, rf.paper_id, rf.pdf_id, rf.chunk_id, rf.page_number,
                   rf.review_status, rf.payload_json, p.title, p.doi, p.year,
                   p.paper_type, p.is_review
            FROM reviewed_facts rf
            LEFT JOIN papers p ON p.paper_id = rf.paper_id
            WHERE rf.review_status IN ('approved', 'preapproved_machine', 'needs_human_review')
            """
        ).fetchall()
    for row in rows:
        payload = json.loads(row["payload_json"])
        material = payload.get("material") or {}
        sample = payload.get("sample") or {}
        prop = payload.get("property") or {}
        phases = payload.get("phases") or []
        devices = payload.get("devices") or []
        target_name = _canonical_property_name(prop.get("property_name"))
        if target_name not in DESIGN_TARGETS:
            continue
        value = _safe_float(_first_present(prop.get("normalized_value"), prop.get("value")))
        if value is None:
            continue
        row_out = {
                "record_id": row["fact_id"],
                "source": "reviewed_facts",
                "paper_id": row["paper_id"],
                "pdf_id": row["pdf_id"],
                "chunk_id": row["chunk_id"],
                "page_number": row["page_number"],
                "title": row["title"] or "",
                "doi": row["doi"] or "",
                "year": row["year"] or "",
                "paper_type": row["paper_type"] or "",
                "is_review": int(row["is_review"] or 0),
                "review_status": row["review_status"],
                "material_name": material.get("canonical_name") or material.get("raw_name") or "",
                "material_family": material.get("material_family") or "",
                "formula": material.get("formula") or "",
                "dopant_elements": ",".join(material.get("dopant_elements") or []),
                "dopant_concentration": material.get("dopant_concentration") or "",
                "zr_fraction": material.get("zr_fraction") or "",
                "film_thickness_nm": sample.get("film_thickness_nm") or "",
                "deposition_method": sample.get("deposition_method") or "",
                "annealing_temperature_c": sample.get("annealing_temperature_c") or "",
                "annealing_time_s": sample.get("annealing_time_s") or "",
                "annealing_atmosphere": sample.get("annealing_atmosphere") or "",
                "top_electrode": sample.get("top_electrode") or "",
                "bottom_electrode": sample.get("bottom_electrode") or "",
                "electrode_stack": sample.get("device_stack") or "",
                "substrate": sample.get("substrate") or "",
                "device_type": prop.get("device_type") or (devices[0].get("device_type") if devices else ""),
                "phase_name": phases[0].get("phase_name") if phases else "",
                "space_group": phases[0].get("space_group") if phases else "",
                "wake_up_or_endurance_state": "",
                "target_property": target_name,
                "target_value": value,
                "target_unit": _first_present(prop.get("normalized_unit"), prop.get("unit")) or "",
                "condition": _json(
                    {
                        "measurement_temperature": prop.get("measurement_temperature"),
                        "measurement_frequency": prop.get("measurement_frequency"),
                        "electric_field": prop.get("electric_field"),
                        "cycle_number": prop.get("cycle_number"),
                    }
                ),
                "evidence_text": prop.get("evidence_text") or "",
                "quality_flags": _json(
                    {
                        "context_quality": (payload.get("ontology_context") or {}).get("context_quality"),
                        "preaudit_status": (payload.get("preaudit") or {}).get("status"),
                    }
                ),
                "ai_review_status": "",
                "usable_for_model": "",
                "risk_flags": "",
            }
        row_out.update(_normalize_model_target(target_name, value, row_out["target_unit"]))
        rows_out.append(_enrich_row_for_tiers(row_out))
    return rows_out


def _benchmark_rows(db_path: Path | None = None) -> list[dict[str, Any]]:
    rows_out: list[dict[str, Any]] = []
    with connect(db_path) as conn:
        try:
            rows = conn.execute(
                """
                SELECT be.extraction_id, be.paper_id, be.pdf_id, be.chunk_id,
                       be.page_number, be.payload_json, p.title, p.doi, p.year,
                       p.paper_type, p.is_review
                FROM benchmark_extractions be
                LEFT JOIN papers p ON p.paper_id = be.paper_id
                WHERE be.status = 'ok'
                """
            ).fetchall()
        except Exception:
            return []
    for row in rows:
        payload = json.loads(row["payload_json"])
        for index, record in enumerate(payload.get("benchmark_records") or []):
            if not isinstance(record, dict):
                continue
            inputs = record.get("input_variables") or {}
            outputs = record.get("output_targets") or {}
            if not isinstance(inputs, dict) or not isinstance(outputs, dict):
                continue
            for target_name, target in outputs.items():
                canonical_target = _canonical_property_name(target_name)
                if isinstance(target, dict):
                    value = _safe_float(target.get("value"))
                    unit = target.get("unit") or ""
                else:
                    value = _safe_float(target)
                    unit = ""
                if value is None:
                    continue
                row_out = {
                        "record_id": f"{row['extraction_id']}_{index}_{target_name}",
                        "source": "benchmark_extractions",
                        "paper_id": row["paper_id"],
                        "pdf_id": row["pdf_id"],
                        "chunk_id": row["chunk_id"],
                        "page_number": row["page_number"],
                        "title": row["title"] or "",
                        "doi": row["doi"] or "",
                        "year": row["year"] or "",
                        "paper_type": row["paper_type"] or "",
                        "is_review": int(row["is_review"] or 0),
                        "review_status": "benchmark_machine",
                        "material_name": inputs.get("material_name") or inputs.get("material") or "",
                        "material_family": inputs.get("material_family") or "",
                        "formula": inputs.get("formula") or "",
                        "dopant_elements": _json(inputs.get("dopants") or inputs.get("dopant_elements")),
                        "dopant_concentration": inputs.get("dopant_concentration") or "",
                        "zr_fraction": inputs.get("zr_fraction") or "",
                        "film_thickness_nm": inputs.get("film_thickness_nm") or inputs.get("thickness_nm") or "",
                        "deposition_method": inputs.get("deposition_method") or "",
                        "annealing_temperature_c": inputs.get("annealing_temperature_c") or "",
                        "annealing_time_s": inputs.get("annealing_time_s") or "",
                        "annealing_atmosphere": inputs.get("annealing_atmosphere") or "",
                        "top_electrode": inputs.get("top_electrode") or "",
                        "bottom_electrode": inputs.get("bottom_electrode") or "",
                        "electrode_stack": inputs.get("electrode_stack") or inputs.get("device_stack") or "",
                        "substrate": inputs.get("substrate") or "",
                        "device_type": inputs.get("device_type") or "",
                        "phase_name": inputs.get("phase") or inputs.get("phase_name") or "",
                        "space_group": inputs.get("space_group") or "",
                        "wake_up_or_endurance_state": inputs.get("wake_up_or_endurance_state") or "",
                        "target_property": canonical_target,
                        "target_value": value,
                        "target_unit": unit,
                        "condition": _json(record.get("conditions")),
                        "evidence_text": record.get("evidence_text") or "",
                        "quality_flags": _json(record.get("quality_flags")),
                        "ai_review_status": "",
                        "usable_for_model": "",
                        "risk_flags": ",".join(str(item) for item in _read_json_list(record.get("quality_flags"))),
                    }
                row_out.update(_normalize_model_target(canonical_target, value, row_out["target_unit"]))
                rows_out.append(_enrich_row_for_tiers(row_out))
    return rows_out


def _sample_link_rows(db_path: Path | None = None) -> list[dict[str, Any]]:
    rows_out: list[dict[str, Any]] = []
    with connect(db_path) as conn:
        try:
            rows = conn.execute(
                """
                SELECT spl.*, p.title, p.doi, p.year, p.paper_type, p.is_review,
                       afa.ai_review_status, afa.usable_for_model, afa.risk_flags_json
                FROM sample_property_links spl
                LEFT JOIN papers p ON p.paper_id = spl.paper_id
                LEFT JOIN ai_fact_audits afa ON afa.link_id = spl.link_id
                WHERE spl.status = 'linked'
                """
            ).fetchall()
        except Exception:
            return []
    for row in rows:
        material = json.loads(row["material_json"] or "{}")
        sample = json.loads(row["sample_json"] or "{}")
        phase = json.loads(row["phase_json"] or "{}")
        prop = json.loads(row["property_json"] or "{}")
        roles = json.loads(row["variable_roles_json"] or "{}")
        target_name = _canonical_property_name(prop.get("property_name"))
        if target_name not in DESIGN_TARGETS:
            continue
        value = _safe_float(_first_present(prop.get("normalized_value"), prop.get("value")))
        if value is None:
            continue
        row_out = {
            "record_id": row["link_id"],
            "source_record_id": row["source_id"],
            "sample_id": row["sample_id"],
            "source": "sample_property_links",
            "paper_id": row["paper_id"],
            "pdf_id": row["pdf_id"],
            "chunk_id": row["chunk_id"],
            "page_number": row["page_number"],
            "title": row["title"] or "",
            "doi": row["doi"] or "",
            "year": row["year"] or "",
            "paper_type": row["paper_type"] or "",
            "is_review": int(row["is_review"] or 0),
            "review_status": row["context_quality"],
            "context_quality": row["context_quality"],
            "material_name": material.get("canonical_name") or material.get("raw_name") or "",
            "material_family": material.get("material_family") or "",
            "formula": material.get("formula") or "",
            "dopant_elements": ",".join(str(item) for item in _as_csv_list(material.get("dopant_elements"))),
            "dopant_concentration": material.get("dopant_concentration") or "",
            "zr_fraction": material.get("zr_fraction") or "",
            "film_thickness_nm": sample.get("film_thickness_nm") or "",
            "deposition_method": sample.get("deposition_method") or "",
            "annealing_temperature_c": sample.get("annealing_temperature_c") or "",
            "annealing_time_s": sample.get("annealing_time_s") or "",
            "annealing_atmosphere": sample.get("annealing_atmosphere") or "",
            "top_electrode": sample.get("top_electrode") or "",
            "bottom_electrode": sample.get("bottom_electrode") or "",
            "electrode_stack": sample.get("device_stack") or sample.get("electrode_stack") or "",
            "substrate": sample.get("substrate") or "",
            "device_type": prop.get("device_type") or sample.get("device_type") or "",
            "phase_name": phase.get("phase_name") or "",
            "space_group": phase.get("space_group") or "",
            "wake_up_or_endurance_state": sample.get("wake_up_or_endurance_state") or "",
            "target_property": target_name,
            "target_value": value,
            "target_unit": _first_present(prop.get("normalized_unit"), prop.get("unit")) or "",
            "condition": _json(prop.get("condition")),
            "evidence_text": row["evidence_text"] or prop.get("evidence_text") or "",
            "ai_review_status": row["ai_review_status"] or "",
            "usable_for_model": int(row["usable_for_model"] or 0) if row["usable_for_model"] is not None else "",
            "risk_flags": ",".join(str(item) for item in _read_json_list(row["risk_flags_json"])),
            "quality_flags": _json(
                {
                    "context_quality": row["context_quality"],
                    "context_score": row["context_score"],
                    "linkage_method": row["linkage_method"],
                    "variable_roles": roles,
                    "ai_review_status": row["ai_review_status"] or "",
                    "risk_flags": _read_json_list(row["risk_flags_json"]),
                }
            ),
        }
        row_out.update(_normalize_model_target(target_name, value, row_out["target_unit"]))
        rows_out.append(_enrich_row_for_tiers(row_out))
    return rows_out


def _as_csv_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            return parsed
        return [item.strip() for item in value.split(",") if item.strip()]
    return [value]


def _collect_design_rows(db_path: Path | None = None) -> list[dict[str, Any]]:
    sample_rows = _sample_link_rows(db_path=db_path)
    linked_source_ids = {
        str(row.get("source_record_id") or "")
        for row in sample_rows
        if row.get("source_record_id")
    }
    fallback_rows = _accepted_reviewed_fact_rows(db_path=db_path) + _benchmark_rows(db_path=db_path)
    fallback_rows = [
        row
        for row in fallback_rows
        if str(row.get("record_id") or "") not in linked_source_ids
    ]
    return sample_rows + fallback_rows


def _design_fieldnames() -> list[str]:
    return [
        "record_id",
        "source_record_id",
        "sample_id",
        "source",
        "paper_id",
        "pdf_id",
        "chunk_id",
        "page_number",
        "title",
        "doi",
        "year",
        "paper_type",
        "is_review",
        "review_status",
        "ai_review_status",
        "usable_for_model",
        "benchmark_tier",
        "material_name",
        "material_family",
        "formula",
        "dopant_elements",
        "dopant_concentration",
        "zr_fraction",
        "film_thickness_nm",
        "deposition_method",
        "annealing_temperature_c",
        "annealing_time_s",
        "annealing_atmosphere",
        "top_electrode",
        "bottom_electrode",
        "electrode_stack",
        "substrate",
        "device_type",
        "phase_name",
        "space_group",
        "wake_up_or_endurance_state",
        "target_property",
        "target_value",
        "target_unit",
        "model_target_value",
        "model_target_unit",
        "model_include",
        "model_exclusion_reason",
        "condition",
        "evidence_text",
        "risk_flags",
        "quality_flags",
    ]


def _write_design_rows(rows: list[dict[str, Any]], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = _design_fieldnames()
    with target.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def build_design_dataset(
    output_path: Path | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    target = output_path or PROJECT_ROOT / "data" / "design" / "hfo2_design_dataset.csv"
    rows = _collect_design_rows(db_path=db_path)
    _write_design_rows(rows, target)
    stats = {
        "rows": len(rows),
        "reviewed_fact_rows": sum(1 for row in rows if row["source"] == "reviewed_facts"),
        "benchmark_rows": sum(1 for row in rows if row["source"] == "benchmark_extractions"),
        "sample_link_rows": sum(1 for row in rows if row["source"] == "sample_property_links"),
        "model_rows": sum(1 for row in rows if int(row.get("model_include") or 0) == 1),
        "excluded_model_rows": sum(1 for row in rows if int(row.get("model_include") or 0) == 0),
        "strong_only_rows": sum(1 for row in rows if row.get("benchmark_tier") == "strong_only"),
        "strong_partial_rows": sum(1 for row in rows if row.get("benchmark_tier") in {"strong_only", "strong_partial"}),
        "all_traceable_rows": sum(1 for row in rows if row.get("benchmark_tier")),
        "output_path": str(target),
    }
    record_pipeline_run("21_build_design_dataset", "ok", stats, db_path=db_path)
    return stats


def build_benchmark_tier_datasets(
    output_dir: Path | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    out_dir = output_dir or PROJECT_ROOT / "data" / "design"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = _collect_design_rows(db_path=db_path)
    tiers = {
        "strong_only": [row for row in rows if row.get("benchmark_tier") == "strong_only"],
        "strong_partial": [
            row for row in rows if row.get("benchmark_tier") in {"strong_only", "strong_partial"}
        ],
        "all_traceable": [row for row in rows if row.get("benchmark_tier")],
    }
    outputs: dict[str, Any] = {}
    for name, tier_rows in tiers.items():
        path = out_dir / f"hfo2_design_dataset_{name}.csv"
        _write_design_rows(tier_rows, path)
        outputs[name] = {"rows": len(tier_rows), "path": str(path)}
    stats = {"tiers": outputs, "total_rows": len(rows), "tier_definitions": BENCHMARK_TIERS}
    (out_dir / "benchmark_tiers.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    record_pipeline_run("29_build_benchmark_tiers", "ok", stats, db_path=db_path)
    return stats
