from __future__ import annotations

import csv
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.pipeline_log import record_pipeline_run


PRIMARY_TARGETS = {
    "remanent_polarization_Pr",
    "double_remanent_polarization_2Pr",
}

GOLD_FIELDS = [
    "material_family",
    "film_thickness_nm",
    "deposition_method",
    "annealing_temperature_c",
    "annealing_time_s",
    "annealing_atmosphere",
    "electrode_stack",
    "device_type",
    "phase_name",
    "target_property",
    "target_value",
    "target_unit",
    "page_number",
]

TEMPLATE_COLUMNS = [
    "gold_status",
    "link_id",
    "paper_id",
    "pdf_id",
    "title",
    "doi",
    "year",
    "evidence_text",
    *[f"pred_{field}" for field in GOLD_FIELDS],
    *[f"gold_{field}" for field in GOLD_FIELDS],
    "gold_notes",
]


def _load_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


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


def _clean_text(value: Any) -> str:
    return (
        str(value or "")
        .strip()
        .lower()
        .replace("µ", "μ")
        .replace("u", "μ")
        .replace("²", "2")
        .replace(" ", "")
        .replace("_", "")
        .replace("-", "")
    )


def _match_value(predicted: Any, gold: Any, field: str) -> bool:
    if gold in (None, ""):
        return predicted in (None, "")
    if field in {
        "film_thickness_nm",
        "annealing_temperature_c",
        "annealing_time_s",
        "target_value",
    }:
        left = _safe_float(predicted)
        right = _safe_float(gold)
        if left is None or right is None:
            return False
        tolerance = max(0.01, abs(right) * 0.05)
        return abs(left - right) <= tolerance
    if field == "page_number":
        left = _safe_float(predicted)
        right = _safe_float(gold)
        return left is not None and right is not None and int(left) == int(right)
    return _clean_text(predicted) == _clean_text(gold)


def _property_name(prop: dict[str, Any]) -> str:
    return str(prop.get("property_name") or prop.get("raw_property_name") or "").strip()


def _target_value(prop: dict[str, Any]) -> Any:
    return prop.get("normalized_value") if prop.get("normalized_value") not in (None, "") else prop.get("value")


def _target_unit(prop: dict[str, Any]) -> Any:
    return prop.get("normalized_unit") or prop.get("unit") or ""


def _row_from_link(row: Any) -> dict[str, Any]:
    material = _load_json(row["material_json"])
    sample = _load_json(row["sample_json"])
    phase = _load_json(row["phase_json"])
    prop = _load_json(row["property_json"])
    electrode_stack = (
        sample.get("electrode_stack")
        or sample.get("device_stack")
        or "/".join(
            part
            for part in [str(sample.get("top_electrode") or ""), str(sample.get("bottom_electrode") or "")]
            if part
        )
    )
    predicted = {
        "material_family": material.get("material_family") or material.get("canonical_name") or "",
        "film_thickness_nm": sample.get("film_thickness_nm"),
        "deposition_method": sample.get("deposition_method") or "",
        "annealing_temperature_c": sample.get("annealing_temperature_c"),
        "annealing_time_s": sample.get("annealing_time_s"),
        "annealing_atmosphere": sample.get("annealing_atmosphere") or "",
        "electrode_stack": electrode_stack,
        "device_type": sample.get("device_type") or sample.get("sample_form") or "",
        "phase_name": phase.get("phase_name") or "",
        "target_property": _property_name(prop),
        "target_value": _target_value(prop),
        "target_unit": _target_unit(prop),
        "page_number": row["page_number"],
    }
    out = {
        "gold_status": "unchecked",
        "link_id": row["link_id"],
        "paper_id": row["paper_id"],
        "pdf_id": row["pdf_id"],
        "title": row["title"] or "",
        "doi": row["doi"] or "",
        "year": row["year"] or "",
        "evidence_text": row["evidence_text"] or "",
        "gold_notes": "",
    }
    for field in GOLD_FIELDS:
        out[f"pred_{field}"] = predicted.get(field, "")
        out[f"gold_{field}"] = ""
    return out


def _sample_candidate_links(limit: int, db_path: Path | None = None) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT spl.link_id, spl.paper_id, spl.pdf_id, spl.page_number,
                   spl.material_json, spl.sample_json, spl.phase_json,
                   spl.property_json, spl.evidence_text, spl.context_quality,
                   p.title, p.doi, p.year
            FROM sample_property_links spl
            LEFT JOIN papers p ON p.paper_id = spl.paper_id
            WHERE spl.context_quality IN ('strong', 'partial')
            ORDER BY
                CASE spl.context_quality WHEN 'strong' THEN 0 ELSE 1 END,
                spl.paper_id,
                spl.link_id
            """
        ).fetchall()

    selected: list[dict[str, Any]] = []
    seen_papers: set[str] = set()
    coverage = Counter()
    for row in rows:
        candidate = _row_from_link(row)
        target = str(candidate.get("pred_target_property") or "")
        family = str(candidate.get("pred_material_family") or "unknown")
        device = str(candidate.get("pred_device_type") or "unknown")
        if target not in PRIMARY_TARGETS:
            continue
        paper_id = candidate["paper_id"]
        if paper_id in seen_papers and len(seen_papers) < limit:
            continue
        selected.append(candidate)
        seen_papers.add(paper_id)
        coverage.update([f"target:{target}", f"family:{family}", f"device:{device}"])
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        selected_ids = {item["link_id"] for item in selected}
        for row in rows:
            candidate = _row_from_link(row)
            if candidate["link_id"] in selected_ids:
                continue
            selected.append(candidate)
            if len(selected) >= limit:
                break
    return selected


def create_gold_set_template(
    output_path: Path | None = None,
    sample_size: int = 30,
    db_path: Path | None = None,
) -> dict[str, Any]:
    target = output_path or PROJECT_ROOT / "data" / "evaluation" / "hfo2_paper_gold_set_template.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = _sample_candidate_links(limit=sample_size, db_path=db_path)
    with target.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=TEMPLATE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    stats = {
        "status": "template_created",
        "output_csv": str(target),
        "rows": len(rows),
        "sample_size": sample_size,
    }
    record_pipeline_run("32_evaluate_paper_prototype", "template", stats, db_path=db_path)
    return stats


def _read_gold_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _is_annotated(row: dict[str, Any]) -> bool:
    status = str(row.get("gold_status") or "").strip().lower()
    if status in {"skip", "skipped", "ignore"}:
        return False
    return any(str(row.get(f"gold_{field}") or "").strip() for field in GOLD_FIELDS)


def _evaluate_rows(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    annotated = [row for row in rows if _is_annotated(row)]
    field_counts: dict[str, Counter] = {field: Counter() for field in GOLD_FIELDS}
    details: list[dict[str, Any]] = []
    exact_sample_matches = 0
    pr_2pr_confusions = 0
    unit_errors = 0

    for row in annotated:
        detail = {
            "link_id": row.get("link_id", ""),
            "paper_id": row.get("paper_id", ""),
            "status": "ok",
            "failed_fields": [],
        }
        context_fields = [
            "material_family",
            "film_thickness_nm",
            "deposition_method",
            "annealing_temperature_c",
            "annealing_time_s",
            "annealing_atmosphere",
            "electrode_stack",
            "device_type",
            "phase_name",
            "page_number",
        ]
        context_ok = True
        for field in GOLD_FIELDS:
            predicted = row.get(f"pred_{field}", "")
            gold = row.get(f"gold_{field}", "")
            if gold in (None, ""):
                continue
            matched = _match_value(predicted, gold, field)
            counter = field_counts[field]
            if matched:
                counter["tp"] += 1
            else:
                counter["fp"] += 1
                counter["fn"] += 1
                detail["failed_fields"].append(field)
                if field in context_fields:
                    context_ok = False
        pred_target = str(row.get("pred_target_property") or "")
        gold_target = str(row.get("gold_target_property") or "")
        if pred_target in PRIMARY_TARGETS and gold_target in PRIMARY_TARGETS and pred_target != gold_target:
            pr_2pr_confusions += 1
        if row.get("gold_target_unit") and not _match_value(
            row.get("pred_target_unit", ""), row.get("gold_target_unit", ""), "target_unit"
        ):
            unit_errors += 1
        if context_ok:
            exact_sample_matches += 1
        detail["failed_fields"] = ",".join(detail["failed_fields"])
        detail["status"] = "failed" if detail["failed_fields"] else "ok"
        details.append(detail)

    field_metrics = []
    total = Counter()
    for field, counts in field_counts.items():
        total.update(counts)
        precision = counts["tp"] / (counts["tp"] + counts["fp"]) if counts["tp"] + counts["fp"] else None
        recall = counts["tp"] / (counts["tp"] + counts["fn"]) if counts["tp"] + counts["fn"] else None
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision is not None and recall is not None and precision + recall
            else None
        )
        field_metrics.append(
            {
                "field": field,
                "tp": counts["tp"],
                "fp": counts["fp"],
                "fn": counts["fn"],
                "precision": precision,
                "recall": recall,
                "f1": f1,
            }
        )
    precision = total["tp"] / (total["tp"] + total["fp"]) if total["tp"] + total["fp"] else None
    recall = total["tp"] / (total["tp"] + total["fn"]) if total["tp"] + total["fn"] else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall and precision + recall else None
    summary = {
        "annotated_rows": len(annotated),
        "field_precision": precision,
        "field_recall": recall,
        "field_f1": f1,
        "sample_property_linking_accuracy": exact_sample_matches / len(annotated) if annotated else None,
        "pr_2pr_confusion_rate": pr_2pr_confusions / len(annotated) if annotated else None,
        "unit_normalization_error_rate": unit_errors / len(annotated) if annotated else None,
        "field_metrics": field_metrics,
    }
    return summary, details


def _markdown_report(summary: dict[str, Any], csv_path: Path) -> str:
    def fmt(value: Any) -> str:
        return "" if value is None else f"{float(value):.4f}"

    rows = [
        "# HfO2-FerroKG Paper Prototype Evaluation",
        "",
        f"Gold set: `{csv_path}`",
        "",
        "## Summary Metrics",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Annotated rows | {summary.get('annotated_rows', 0)} |",
        f"| Extraction field precision | {fmt(summary.get('field_precision'))} |",
        f"| Extraction field recall | {fmt(summary.get('field_recall'))} |",
        f"| Extraction field F1 | {fmt(summary.get('field_f1'))} |",
        f"| Sample-property linking accuracy | {fmt(summary.get('sample_property_linking_accuracy'))} |",
        f"| Pr/2Pr confusion rate | {fmt(summary.get('pr_2pr_confusion_rate'))} |",
        f"| Unit normalization error rate | {fmt(summary.get('unit_normalization_error_rate'))} |",
        "",
        "## Field Metrics",
        "| Field | TP | FP | FN | Precision | Recall | F1 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in summary.get("field_metrics", []):
        rows.append(
            "| {field} | {tp} | {fp} | {fn} | {precision} | {recall} | {f1} |".format(
                field=item["field"],
                tp=item["tp"],
                fp=item["fp"],
                fn=item["fn"],
                precision=fmt(item["precision"]),
                recall=fmt(item["recall"]),
                f1=fmt(item["f1"]),
            )
        )
    return "\n".join(rows) + "\n"


def evaluate_paper_prototype(
    gold_set_path: Path | None = None,
    output_dir: Path | None = None,
    sample_size: int = 30,
    db_path: Path | None = None,
) -> dict[str, Any]:
    gold_path = gold_set_path or PROJECT_ROOT / "data" / "evaluation" / "hfo2_paper_gold_set_template.csv"
    if not gold_path.exists():
        return create_gold_set_template(output_path=gold_path, sample_size=sample_size, db_path=db_path)

    rows = _read_gold_rows(gold_path)
    out_dir = output_dir or PROJECT_ROOT / "data" / "evaluation"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary, details = _evaluate_rows(rows)
    if not summary["annotated_rows"]:
        stats = {
            "status": "needs_gold_annotations",
            "gold_set_csv": str(gold_path),
            "rows": len(rows),
            "annotated_rows": 0,
            "message": "Fill gold_* columns and rerun this script to compute metrics.",
        }
        record_pipeline_run("32_evaluate_paper_prototype", "needs_gold", stats, db_path=db_path)
        return stats

    summary_path = out_dir / "paper_prototype_eval_summary.json"
    details_path = out_dir / "paper_prototype_eval_details.csv"
    report_path = out_dir / "paper_prototype_eval_report.md"
    summary_payload = {
        "status": "ok",
        "gold_set_csv": str(gold_path),
        **summary,
    }
    summary_path.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with details_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["link_id", "paper_id", "status", "failed_fields"])
        writer.writeheader()
        writer.writerows(details)
    report_path.write_text(_markdown_report(summary_payload, gold_path), encoding="utf-8")
    stats = {
        **summary_payload,
        "output_json": str(summary_path),
        "output_csv": str(details_path),
        "output_markdown": str(report_path),
    }
    record_pipeline_run("32_evaluate_paper_prototype", "ok", stats, db_path=db_path)
    return stats
