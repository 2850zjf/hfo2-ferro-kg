from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.pipeline_log import record_pipeline_run


def collect_summary(db_path: Path | None = None) -> dict[str, object]:
    with connect(db_path) as conn:
        paper_years = Counter(
            str(row["year"] or "unknown")
            for row in conn.execute("SELECT year FROM papers").fetchall()
        )
        statuses = Counter(
            row["review_status"]
            for row in conn.execute("SELECT review_status FROM reviewed_facts").fetchall()
        )
        families = Counter()
        properties = Counter()
        rows = conn.execute("SELECT payload_json FROM reviewed_facts").fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            material = payload.get("material") or {}
            prop = payload.get("property") or {}
            families[str(material.get("material_family", "unknown"))] += 1
            properties[str(prop.get("property_name", "unknown"))] += 1
    return {
        "paper_years": dict(sorted(paper_years.items())),
        "review_statuses": dict(statuses),
        "material_families": dict(families),
        "properties": dict(properties),
    }


def write_markdown_report(output_path: Path | None = None, db_path: Path | None = None) -> Path:
    summary = collect_summary(db_path)
    target = output_path or PROJECT_ROOT / "data" / "exports" / "summary_report.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# HfO2-FerroKG Summary Report",
        "",
        "This report is generated from machine pre-audited facts. Human review is still required before publication use.",
        "",
        "## Paper Years",
        "",
    ]
    for key, value in summary["paper_years"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Review Status", ""])
    for key, value in summary["review_statuses"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Material Families", ""])
    for key, value in summary["material_families"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Properties", ""])
    for key, value in summary["properties"].items():
        lines.append(f"- {key}: {value}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    record_pipeline_run(
        "09_export_reports",
        "ok",
        {"report_path": str(target), **{f"n_{k}": len(v) for k, v in summary.items()}},
        db_path=db_path,
    )
    return target
