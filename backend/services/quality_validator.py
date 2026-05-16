from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from backend.core.config import PROJECT_ROOT, get_settings
from backend.db.session import connect
from backend.services.pipeline_log import record_pipeline_run


PROPERTY_LIMITS = {
    "remanent_polarization_Pr": ("μC/cm²", 100.0, "Pr above 100 μC/cm² is unusual for HfO2 and needs review."),
    "double_remanent_polarization_2Pr": ("μC/cm²", 200.0, "2Pr above 200 μC/cm² is unusual for HfO2 and needs review."),
    "coercive_field_Ec": ("MV/cm", 10.0, "Ec above 10 MV/cm is unusual for HfO2 and needs review."),
}

TWO_PR_RE = re.compile(r"\b2\s*\.?\s*P\s*\.?\s*r\b", re.IGNORECASE)


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_under(path_text: str, root: Path) -> bool:
    try:
        return Path(path_text).resolve().is_relative_to(root.resolve())
    except Exception:
        return False


def validate_local_results(db_path: Path | None = None) -> dict[str, Any]:
    settings = get_settings()
    stats: dict[str, Any] = {}
    status_counts: Counter[str] = Counter()
    property_counts: Counter[str] = Counter()
    unit_counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    missing_evidence: list[str] = []
    missing_trace: list[str] = []
    anomaly_records: list[dict[str, Any]] = []

    with connect(db_path) as conn:
        for table in [
            "pdf_files",
            "papers",
            "parsed_pages",
            "document_chunks",
            "pdf_tables",
            "extraction_candidates",
            "reviewed_facts",
        ]:
            stats[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

        pdf_rows = conn.execute("SELECT pdf_id, file_path FROM pdf_files").fetchall()
        safe_pdf_paths = sum(_is_under(row["file_path"], settings.pdf_root) for row in pdf_rows)

        rows = conn.execute(
            """
            SELECT fact_id, review_status, page_number, chunk_id, payload_json
            FROM reviewed_facts
            """
        ).fetchall()
        for row in rows:
            status_counts[str(row["review_status"])] += 1
            payload = json.loads(row["payload_json"])
            prop = payload.get("property") or {}
            material = payload.get("material") or {}
            evidence = payload.get("evidence") or {}

            property_name = str(prop.get("property_name") or "unknown")
            unit = str(prop.get("normalized_unit") or prop.get("unit") or "unknown")
            property_counts[property_name] += 1
            unit_counts[f"{property_name} | {unit}"] += 1
            family_counts[str(material.get("material_family") or "unknown")] += 1

            evidence_text = (
                prop.get("evidence_text")
                or evidence.get("evidence_text")
                or material.get("evidence_text")
                or (payload.get("sample") or {}).get("evidence_text")
            )
            if not evidence_text:
                missing_evidence.append(row["fact_id"])
            if not (row["page_number"] or row["chunk_id"] or evidence.get("page_number") or evidence.get("chunk_id")):
                missing_trace.append(row["fact_id"])

            if property_name == "remanent_polarization_Pr" and evidence_text and TWO_PR_RE.search(evidence_text):
                anomaly_records.append(
                    {
                        "fact_id": row["fact_id"],
                        "property_name": property_name,
                        "value": prop.get("normalized_value", prop.get("value")),
                        "unit": unit,
                        "reason": "Evidence mentions 2Pr but the fact is classified as Pr.",
                    }
                )

            value = _safe_float(prop.get("normalized_value", prop.get("value")))
            if value is None:
                continue
            if value < 0:
                anomaly_records.append(
                    {
                        "fact_id": row["fact_id"],
                        "property_name": property_name,
                        "value": value,
                        "unit": unit,
                        "reason": "Negative material property value needs review.",
                    }
                )
            limit = PROPERTY_LIMITS.get(property_name)
            if limit and value > limit[1]:
                anomaly_records.append(
                    {
                        "fact_id": row["fact_id"],
                        "property_name": property_name,
                        "value": value,
                        "unit": unit,
                        "reason": limit[2],
                    }
                )

    human_approved = status_counts.get("approved", 0)
    machine_preaudited = status_counts.get("preapproved_machine", 0)
    needs_human_review = status_counts.get("needs_human_review", 0) + len(anomaly_records)
    publication_ready = (
        human_approved > 0
        and not missing_evidence
        and not missing_trace
        and not anomaly_records
    )

    return {
        "counts": stats,
        "pdf_path_status": {
            "safe_raw_pdf_paths": safe_pdf_paths,
            "pdf_files": stats["pdf_files"],
            "all_pdf_paths_under_raw_pdfs": safe_pdf_paths == stats["pdf_files"],
        },
        "review_statuses": _counter_dict(status_counts),
        "property_counts": _counter_dict(property_counts),
        "unit_counts": _counter_dict(unit_counts),
        "material_families": _counter_dict(family_counts),
        "missing_evidence_fact_ids": missing_evidence,
        "missing_trace_fact_ids": missing_trace,
        "domain_anomalies": anomaly_records,
        "quality_gate": {
            "human_approved_facts": human_approved,
            "machine_preaudited_facts": machine_preaudited,
            "needs_human_review_minimum": needs_human_review,
            "publication_ready": publication_ready,
            "verdict": "needs_human_review" if not publication_ready else "ready_for_export",
        },
    }


def write_validation_report(
    output_path: Path | None = None,
    db_path: Path | None = None,
) -> Path:
    report = validate_local_results(db_path=db_path)
    target = output_path or PROJECT_ROOT / "data" / "exports" / "validation_report.md"
    target.parent.mkdir(parents=True, exist_ok=True)

    gate = report["quality_gate"]
    lines = [
        "# HfO2-FerroKG Validation Report",
        "",
        "This local report checks traceability, evidence completeness, and HfO2-specific value ranges.",
        "It is safe to keep local; PDF files and parsed data remain ignored by Git.",
        "",
        "## Verdict",
        "",
        f"- Status: {gate['verdict']}",
        f"- Human approved facts: {gate['human_approved_facts']}",
        f"- Machine pre-audited facts: {gate['machine_preaudited_facts']}",
        f"- Minimum facts needing human review: {gate['needs_human_review_minimum']}",
        "",
        "## Corpus",
        "",
    ]
    for key, value in report["counts"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## PDF Path Check", ""])
    for key, value in report["pdf_path_status"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Review Statuses", ""])
    for key, value in report["review_statuses"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Properties", ""])
    for key, value in report["property_counts"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Domain Anomalies", ""])
    if report["domain_anomalies"]:
        for item in report["domain_anomalies"][:50]:
            lines.append(
                f"- {item['fact_id']}: {item['property_name']} = {item['value']} {item['unit']} ({item['reason']})"
            )
    else:
        lines.append("- None")
    lines.extend(["", "## Traceability", ""])
    lines.append(f"- Missing evidence: {len(report['missing_evidence_fact_ids'])}")
    lines.append(f"- Missing page or chunk trace: {len(report['missing_trace_fact_ids'])}")

    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    record_pipeline_run(
        "10_validate_results",
        "ok",
        {
            "verdict": gate["verdict"],
            "domain_anomalies": len(report["domain_anomalies"]),
            "missing_evidence": len(report["missing_evidence_fact_ids"]),
            "missing_trace": len(report["missing_trace_fact_ids"]),
            "report_path": str(target),
        },
        db_path=db_path,
    )
    return target
