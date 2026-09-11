from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.pipeline_log import record_pipeline_run


DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "exports" / "corpus_coverage_v23"
DEFAULT_PADDLE_ROOT = PROJECT_ROOT / "data" / "paddleocr" / "full_pdf_markdown"


def audit_corpus_coverage(
    output_dir: Path | None = None,
    paddle_root: Path | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    target = output_dir or DEFAULT_OUTPUT_DIR
    paddle = paddle_root or DEFAULT_PADDLE_ROOT
    target.mkdir(parents=True, exist_ok=True)

    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT pf.pdf_id, pf.paper_id, pf.file_name, pf.file_path, pf.parse_status,
                   COALESCE(pf.page_count, 0) AS page_count,
                   COALESCE(pf.text_page_count, 0) AS text_page_count,
                   COALESCE(pf.low_text_page_count, 0) AS low_text_page_count,
                   COALESCE(pf.blank_page_count, 0) AS blank_page_count,
                   COALESCE(pf.is_duplicate, 0) AS is_duplicate,
                   COALESCE(lrs.final_tier, 'unscreened') AS relevance_tier,
                   COALESCE(lrs.extraction_policy, 'unspecified') AS extraction_policy,
                   COALESCE(pp.parsed_pages, 0) AS parsed_pages,
                   COALESCE(dc.chunks, 0) AS chunks,
                   COALESCE(pt.tables, 0) AS tables,
                   COALESCE(pv.images, 0) AS images,
                   COALESCE(pv.figure_captions, 0) AS figure_captions,
                   COALESCE(pe.equations, 0) AS equations
            FROM pdf_files pf
            LEFT JOIN literature_relevance_screenings lrs ON lrs.pdf_id = pf.pdf_id
            LEFT JOIN (
                SELECT pdf_id, COUNT(*) AS parsed_pages FROM parsed_pages GROUP BY pdf_id
            ) pp ON pp.pdf_id = pf.pdf_id
            LEFT JOIN (
                SELECT pdf_id, COUNT(*) AS chunks FROM document_chunks GROUP BY pdf_id
            ) dc ON dc.pdf_id = pf.pdf_id
            LEFT JOIN (
                SELECT pdf_id, COUNT(*) AS tables FROM pdf_tables GROUP BY pdf_id
            ) pt ON pt.pdf_id = pf.pdf_id
            LEFT JOIN (
                SELECT pdf_id,
                       SUM(CASE WHEN asset_type='image' THEN 1 ELSE 0 END) AS images,
                       SUM(CASE WHEN asset_type='figure_caption_geometry' THEN 1 ELSE 0 END) AS figure_captions
                FROM pdf_visual_assets GROUP BY pdf_id
            ) pv ON pv.pdf_id = pf.pdf_id
            LEFT JOIN (
                SELECT pdf_id, COUNT(*) AS equations FROM pdf_equations GROUP BY pdf_id
            ) pe ON pe.pdf_id = pf.pdf_id
            ORDER BY pf.pdf_id
            """
        ).fetchall()

    records: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        paddle_state = _paddle_state(paddle / str(record["pdf_id"]))
        record.update(paddle_state)
        record["page_coverage"] = _ratio(record["parsed_pages"], record["page_count"])
        record["eligible_for_publication_queue"] = int(
            record["parse_status"] == "parsed"
            and not record["is_duplicate"]
            and record["relevance_tier"] != "irrelevant"
            and record["extraction_policy"] != "exclude"
        )
        record["local_multimodal_assets"] = int(record["tables"]) + int(record["images"]) + int(record["equations"])
        records.append(record)

    fields = list(records[0].keys()) if records else []
    csv_path = target / "corpus_coverage.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)

    summary = _summary(records)
    json_path = target / "corpus_coverage_summary.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path = target / "corpus_coverage_report.md"
    report_path.write_text(_markdown_report(summary), encoding="utf-8")
    stats = {
        **summary,
        "csv_path": str(csv_path),
        "json_path": str(json_path),
        "report_path": str(report_path),
    }
    record_pipeline_run("60_audit_corpus_coverage", "ok", stats, db_path=db_path)
    return stats


def _paddle_state(output_dir: Path) -> dict[str, Any]:
    manifest_candidates = [output_dir / "manifest.json", output_dir / "paddleocr_record.json"]
    payload: dict[str, Any] = {}
    for path in manifest_candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {"state": "invalid_manifest"}
        break
    markdown_dir = output_dir / "markdown"
    markdown_files = len(list(markdown_dir.glob("*.md"))) if markdown_dir.exists() else 0
    combined_path = output_dir / "combined.md"
    state = str(payload.get("state") or ("partial" if markdown_files else "not_started"))
    return {
        "paddleocr_state": state,
        "paddleocr_pages": int(payload.get("pages") or payload.get("extracted_pages") or 0),
        "paddleocr_markdown_files": markdown_files,
        "paddleocr_combined_markdown": int(combined_path.exists()),
    }


def _ratio(numerator: Any, denominator: Any) -> float:
    total = int(denominator or 0)
    return round(int(numerator or 0) / total, 4) if total > 0 else 0.0


def _summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    parse_counts = Counter(str(row["parse_status"]) for row in records)
    paddle_counts = Counter(str(row["paddleocr_state"]) for row in records)
    eligible = [row for row in records if row["eligible_for_publication_queue"]]
    page_complete = [
        row for row in records
        if row["parse_status"] == "parsed"
        and int(row["page_count"] or 0) > 0
        and int(row["parsed_pages"] or 0) >= int(row["page_count"] or 0)
    ]
    return {
        "pdf_records": len(records),
        "eligible_pdfs": len(eligible),
        "parse_status_counts": dict(parse_counts),
        "page_complete_parsed_pdfs": len(page_complete),
        "parsed_pages": sum(int(row["parsed_pages"] or 0) for row in records),
        "chunks": sum(int(row["chunks"] or 0) for row in records),
        "tables": sum(int(row["tables"] or 0) for row in records),
        "images": sum(int(row["images"] or 0) for row in records),
        "figure_captions": sum(int(row["figure_captions"] or 0) for row in records),
        "equations": sum(int(row["equations"] or 0) for row in records),
        "paddleocr_state_counts": dict(paddle_counts),
        "paddleocr_done_pdfs": sum(
            1 for row in records
            if row["paddleocr_state"] == "done" and int(row["paddleocr_markdown_files"] or 0) > 0
        ),
    }


def _markdown_report(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# HfO2-FerroKG Corpus Coverage Audit v2.3",
            "",
            f"- PDF records: {summary['pdf_records']}",
            f"- Publication-queue eligible PDFs: {summary['eligible_pdfs']}",
            f"- Local parsed pages: {summary['parsed_pages']}",
            f"- Document chunks: {summary['chunks']}",
            f"- Tables: {summary['tables']}",
            f"- Images: {summary['images']}",
            f"- Geometrically located figure captions: {summary['figure_captions']}",
            f"- Equation candidates: {summary['equations']}",
            f"- PaddleOCR completed PDFs with Markdown: {summary['paddleocr_done_pdfs']}",
            "",
            "## Parse Status",
            "",
            *[f"- {key}: {value}" for key, value in sorted(summary["parse_status_counts"].items())],
            "",
            "## PaddleOCR Status",
            "",
            *[f"- {key}: {value}" for key, value in sorted(summary["paddleocr_state_counts"].items())],
            "",
            "PaddleOCR completion is reported only when a done manifest and Markdown files both exist.",
        ]
    ) + "\n"
