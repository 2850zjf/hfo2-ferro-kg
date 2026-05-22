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
        rows_out.append(
            {
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
            }
        )
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
            inputs = record.get("input_variables") or {}
            outputs = record.get("output_targets") or {}
            if not isinstance(outputs, dict):
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
                rows_out.append(
                    {
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
                    }
                )
    return rows_out


def build_design_dataset(
    output_path: Path | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    target = output_path or PROJECT_ROOT / "data" / "design" / "hfo2_design_dataset.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = _accepted_reviewed_fact_rows(db_path=db_path) + _benchmark_rows(db_path=db_path)
    fieldnames = [
        "record_id",
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
        "condition",
        "evidence_text",
        "quality_flags",
    ]
    with target.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})
    stats = {
        "rows": len(rows),
        "reviewed_fact_rows": sum(1 for row in rows if row["source"] == "reviewed_facts"),
        "benchmark_rows": sum(1 for row in rows if row["source"] == "benchmark_extractions"),
        "output_path": str(target),
    }
    record_pipeline_run("21_build_design_dataset", "ok", stats, db_path=db_path)
    return stats
