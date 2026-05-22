from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.db.session import connect
from backend.services.hfo2_extractor import (
    extract_devices,
    extract_materials,
    extract_phases,
    extract_samples,
)
from backend.services.ontology_context import build_ontology_context
from backend.services.pipeline_log import record_pipeline_run


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(_has_value(item) for item in value)
    if isinstance(value, dict):
        return any(_has_value(item) for item in value.values())
    return True


def _model_dump_list(items: list[Any]) -> list[dict[str, Any]]:
    return [
        item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
        for item in items
    ]


def _merge_missing(base: dict[str, Any] | None, supplement: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(base or {})
    for key, value in (supplement or {}).items():
        if not _has_value(merged.get(key)) and _has_value(value):
            merged[key] = value
    return merged


def _merge_unique(base: list[dict[str, Any]], supplements: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    merged = list(base or [])
    seen = {str(item.get(key) or item) for item in merged}
    for item in supplements:
        identity = str(item.get(key) or item)
        if identity not in seen:
            merged.append(item)
            seen.add(identity)
    return merged


def _first(values: list[dict[str, Any]]) -> dict[str, Any] | None:
    return values[0] if values else None


def _relation_completion(
    row: Any,
    material: dict[str, Any],
    sample: dict[str, Any],
    prop: dict[str, Any],
    phases: list[dict[str, Any]],
    devices: list[dict[str, Any]],
    ontology_context: dict[str, Any],
    sources: list[str],
) -> dict[str, Any]:
    material_label = (
        material.get("canonical_name")
        or material.get("raw_name")
        or prop.get("material_ref")
        or "unknown material"
    )
    property_label = prop.get("property_name") or "unknown property"
    sample_label = ontology_context.get("sample_label") or f"{material_label} sample"
    process_summary = ontology_context.get("process_context", {}).get("summary")
    phase_label = ", ".join(
        ontology_context.get("structure_context", {}).get("phase_names", [])
        + ontology_context.get("structure_context", {}).get("space_groups", [])
    )
    device_label = ", ".join(ontology_context.get("device_context", {}).get("device_types", []))
    return {
        "schema_version": "fact-relation-completion-v1",
        "fact_id": row["fact_id"],
        "candidate_id": row["candidate_id"],
        "completion_sources": sorted(set(sources)),
        "completion_score": ontology_context.get("context_score"),
        "completion_quality": ontology_context.get("context_quality"),
        "comparison_ready": ontology_context.get("comparison_ready"),
        "relations": [
            {
                "type": "REPORTS",
                "from": row["paper_id"],
                "to": material_label,
                "ready": _has_value(material_label),
            },
            {
                "type": "HAS_SAMPLE",
                "from": material_label,
                "to": sample_label,
                "ready": _has_value(sample_label),
            },
            {
                "type": "FABRICATED_BY",
                "from": sample_label,
                "to": process_summary,
                "ready": _has_value(process_summary),
            },
            {
                "type": "HAS_PHASE",
                "from": sample_label,
                "to": phase_label,
                "ready": _has_value(phase_label),
            },
            {
                "type": "USED_IN",
                "from": sample_label,
                "to": device_label,
                "ready": _has_value(device_label),
            },
            {
                "type": "HAS_PROPERTY",
                "from": sample_label,
                "to": property_label,
                "ready": _has_value(property_label),
            },
            {
                "type": "SUPPORTED_BY",
                "from": property_label,
                "to": prop.get("evidence_text"),
                "ready": _has_value(prop.get("evidence_text")),
            },
        ],
        "present_context_fields": ontology_context.get("present_context_fields", []),
        "missing_context_fields": ontology_context.get("missing_context_fields", []),
        "missing_context_labels": ontology_context.get("missing_context_labels", []),
    }


def enrich_fact_relations(db_path: Path | None = None, dry_run: bool = False) -> dict[str, int]:
    stats = {
        "facts_seen": 0,
        "facts_updated": 0,
        "materials_completed": 0,
        "samples_completed": 0,
        "phases_completed": 0,
        "devices_completed": 0,
        "strong_context": 0,
        "partial_context": 0,
        "weak_context": 0,
    }

    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT rf.fact_id, rf.candidate_id, rf.paper_id, rf.pdf_id, rf.chunk_id,
                   rf.page_number, rf.payload_json,
                   ec.payload_json AS candidate_payload_json,
                   dc.text AS chunk_text
            FROM reviewed_facts rf
            LEFT JOIN extraction_candidates ec ON ec.candidate_id = rf.candidate_id
            LEFT JOIN document_chunks dc ON dc.chunk_id = rf.chunk_id
            """
        ).fetchall()

        for row in rows:
            stats["facts_seen"] += 1
            payload = json.loads(row["payload_json"])
            candidate_payload = (
                json.loads(row["candidate_payload_json"])
                if row["candidate_payload_json"]
                else {}
            )
            prop = payload.get("property") or {}
            material = payload.get("material") or {}
            sample = payload.get("sample") or {}
            phases = payload.get("phases") or []
            devices = payload.get("devices") or []
            sources = ["reviewed_fact_payload"]

            candidate_material = _first(candidate_payload.get("materials") or [])
            if not _has_value(material) and candidate_material:
                material = candidate_material
                stats["materials_completed"] += 1
                sources.append("candidate_payload")

            candidate_sample = _first(candidate_payload.get("samples") or [])
            before_sample = json.dumps(sample, sort_keys=True, ensure_ascii=False)
            sample = _merge_missing(sample, candidate_sample)
            if json.dumps(sample, sort_keys=True, ensure_ascii=False) != before_sample:
                stats["samples_completed"] += 1
                sources.append("candidate_payload")

            candidate_phases = candidate_payload.get("phases") or []
            if candidate_phases and not phases:
                phases = candidate_phases
                stats["phases_completed"] += 1
                sources.append("candidate_payload")

            candidate_devices = candidate_payload.get("devices") or []
            if candidate_devices and not devices:
                devices = candidate_devices
                stats["devices_completed"] += 1
                sources.append("candidate_payload")

            chunk_text = row["chunk_text"] or ""
            if chunk_text:
                rule_materials = _model_dump_list(extract_materials(chunk_text))
                if not _has_value(material) and rule_materials:
                    material = rule_materials[0]
                    stats["materials_completed"] += 1
                    sources.append("chunk_rules")
                material_ref = (
                    material.get("canonical_name")
                    or material.get("raw_name")
                    or prop.get("material_ref")
                    or "HfO2"
                )
                rule_samples = _model_dump_list(extract_samples(chunk_text, material_ref))
                before_sample = json.dumps(sample, sort_keys=True, ensure_ascii=False)
                sample = _merge_missing(sample, _first(rule_samples))
                if json.dumps(sample, sort_keys=True, ensure_ascii=False) != before_sample:
                    stats["samples_completed"] += 1
                    sources.append("chunk_rules")
                rule_phases = _model_dump_list(extract_phases(chunk_text, material_ref))
                merged_phases = _merge_unique(phases, rule_phases, "phase_name")
                if len(merged_phases) != len(phases):
                    stats["phases_completed"] += len(merged_phases) - len(phases)
                    sources.append("chunk_rules")
                phases = merged_phases
                rule_devices = _model_dump_list(extract_devices(chunk_text))
                merged_devices = _merge_unique(devices, rule_devices, "device_type")
                if len(merged_devices) != len(devices):
                    stats["devices_completed"] += len(merged_devices) - len(devices)
                    sources.append("chunk_rules")
                devices = merged_devices

            ontology_context = build_ontology_context(material, sample, prop, phases, devices)
            payload["material"] = material or None
            payload["sample"] = sample or None
            payload["phases"] = phases
            payload["devices"] = devices
            payload["ontology_context"] = ontology_context
            payload["relation_completion"] = _relation_completion(
                row,
                material,
                sample,
                prop,
                phases,
                devices,
                ontology_context,
                sources,
            )

            quality = ontology_context.get("context_quality") or "weak"
            if quality == "strong":
                stats["strong_context"] += 1
            elif quality == "partial":
                stats["partial_context"] += 1
            else:
                stats["weak_context"] += 1

            if not dry_run:
                conn.execute(
                    """
                    UPDATE reviewed_facts
                    SET payload_json = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE fact_id = ?
                    """,
                    (json.dumps(payload, ensure_ascii=False), row["fact_id"]),
                )
                stats["facts_updated"] += 1

        if not dry_run:
            conn.commit()
            record_pipeline_run("16_enrich_fact_relations", "ok", stats, db_path=db_path)
    return stats
