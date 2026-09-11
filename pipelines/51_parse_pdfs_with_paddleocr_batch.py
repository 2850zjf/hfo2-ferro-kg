from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.paddleocr_parser import (
    DEFAULT_MODEL,
    DEFAULT_OPTIONAL_PAYLOAD,
    PaddleOCRConfig,
    download_paddleocr_result,
    get_paddleocr_token,
    poll_paddleocr_job,
    result_to_dict,
    submit_paddleocr_job,
)


def _load_pdf_rows(
    *,
    limit: int | None,
    include_excluded: bool,
    include_duplicates: bool,
    only_pdf_id: str | None,
) -> list[dict[str, Any]]:
    where = []
    params: list[Any] = []
    if only_pdf_id:
        where.append("pdf_id = ?")
        params.append(only_pdf_id)
    else:
        if include_excluded:
            where.append("parse_status IN ('parsed', 'excluded_irrelevant')")
        else:
            where.append("parse_status = 'parsed'")
        if not include_duplicates:
            where.append("is_duplicate = 0")
    sql = """
        SELECT pdf_id, file_name, file_path, parse_status, is_duplicate, page_count, text_page_count,
               low_text_page_count, blank_page_count
        FROM pdf_files
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY file_name"
    if limit is not None and limit > 0:
        sql += " LIMIT ?"
        params.append(int(limit))
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _manifest_done(output_dir: Path) -> bool:
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.exists():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return manifest.get("state") == "done" and bool(manifest.get("markdown_files"))


def _write_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _write_status_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "timestamp",
        "pdf_id",
        "file_name",
        "file_path",
        "parse_status",
        "status",
        "job_id",
        "pages",
        "markdown_files",
        "output_dir",
        "error",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _combined_markdown(output_dir: Path, pdf_row: dict[str, Any]) -> Path | None:
    markdown_dir = output_dir / "markdown"
    markdown_files = sorted(markdown_dir.glob("*.md"))
    if not markdown_files:
        return None
    parts = [
        f"# {pdf_row['file_name']}",
        "",
        f"- pdf_id: `{pdf_row['pdf_id']}`",
        f"- source: `{pdf_row['file_path']}`",
        "",
    ]
    for idx, md_path in enumerate(markdown_files, start=1):
        text = md_path.read_text(encoding="utf-8")
        parts.extend([f"\n\n## Page {idx}\n", text.strip(), ""])
    combined_path = output_dir / "combined.md"
    combined_path.write_text("\n".join(parts).strip() + "\n", encoding="utf-8")
    return combined_path


def _run_one_pdf(
    *,
    row: dict[str, Any],
    config: PaddleOCRConfig,
    output_root: Path,
    max_wait_seconds: float,
    force: bool,
) -> dict[str, Any]:
    pdf_id = str(row["pdf_id"])
    output_dir = output_root / pdf_id
    source_path = Path(str(row["file_path"])).expanduser()
    base_record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "pdf_id": pdf_id,
        "file_name": row.get("file_name", ""),
        "file_path": str(source_path),
        "parse_status": row.get("parse_status", ""),
        "output_dir": str(output_dir),
    }
    if not source_path.exists():
        return {**base_record, "status": "missing_file", "error": f"File not found: {source_path}"}
    if not force and _manifest_done(output_dir):
        manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
        return {
            **base_record,
            "status": "skipped_existing",
            "job_id": manifest.get("job_id", ""),
            "pages": manifest.get("pages", ""),
            "markdown_files": len(manifest.get("markdown_files", [])),
        }

    job_id = submit_paddleocr_job(source_path, config=config)
    payload = poll_paddleocr_job(job_id, config=config, max_wait_seconds=max_wait_seconds)
    result = download_paddleocr_result(source_path, job_id, payload, config, output_dir)
    combined_path = _combined_markdown(output_dir, row)
    result_dict = result_to_dict(result)
    result_dict["pdf_id"] = pdf_id
    result_dict["file_name"] = row.get("file_name", "")
    result_dict["combined_markdown_path"] = str(combined_path) if combined_path else ""
    (output_dir / "paddleocr_record.json").write_text(
        json.dumps(result_dict, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        **base_record,
        "status": "done",
        "job_id": result.job_id,
        "pages": result.pages,
        "markdown_files": len(result.markdown_files),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch parse PDF library into PaddleOCR-VL Markdown.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of PDFs; default runs all eligible PDFs.")
    parser.add_argument("--pdf-id", default=None, help="Parse one PDF id.")
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "data" / "paddleocr" / "full_pdf_markdown")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--max-wait", type=float, default=3600.0)
    parser.add_argument("--sleep-between", type=float, default=1.0)
    parser.add_argument("--force", action="store_true", help="Re-run even when manifest.json already exists.")
    parser.add_argument("--include-excluded", action="store_true", help="Also parse excluded_irrelevant PDFs.")
    parser.add_argument("--include-duplicates", action="store_true", help="Also parse duplicate PDFs.")
    parser.add_argument("--doc-orientation", action="store_true")
    parser.add_argument("--doc-unwarping", action="store_true")
    parser.add_argument("--chart-recognition", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    token = get_paddleocr_token()
    if not token and not args.dry_run:
        raise RuntimeError("Missing PaddleOCR token. Set PADDLEOCR_TOKEN or PADDLEOCR_API_TOKEN in the environment.")

    optional_payload = dict(DEFAULT_OPTIONAL_PAYLOAD)
    optional_payload["useDocOrientationClassify"] = bool(args.doc_orientation)
    optional_payload["useDocUnwarping"] = bool(args.doc_unwarping)
    optional_payload["useChartRecognition"] = bool(args.chart_recognition)
    config = PaddleOCRConfig(
        token=token or "dry-run-token",
        model=args.model,
        optional_payload=optional_payload,
        poll_interval_seconds=args.poll_interval,
    )

    rows = _load_pdf_rows(
        limit=args.limit,
        include_excluded=args.include_excluded,
        include_duplicates=args.include_duplicates,
        only_pdf_id=args.pdf_id,
    )
    args.output_root.mkdir(parents=True, exist_ok=True)
    run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = PROJECT_ROOT / "logs" / f"paddleocr_batch_{run_stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    status_csv = run_dir / "status.csv"
    events_jsonl = run_dir / "events.jsonl"
    run_manifest = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "eligible_pdfs": len(rows),
        "output_root": str(args.output_root),
        "model": args.model,
        "optional_payload": optional_payload,
        "dry_run": args.dry_run,
        "include_excluded": args.include_excluded,
        "include_duplicates": args.include_duplicates,
    }
    (run_dir / "run_manifest.json").write_text(json.dumps(run_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(run_manifest, ensure_ascii=False, indent=2), flush=True)
    status_rows: list[dict[str, Any]] = []
    if args.dry_run:
        for row in rows[:20]:
            output_dir = args.output_root / str(row["pdf_id"])
            record = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "pdf_id": row["pdf_id"],
                "file_name": row["file_name"],
                "file_path": row["file_path"],
                "parse_status": row["parse_status"],
                "status": "would_parse" if args.force or not _manifest_done(output_dir) else "would_skip_existing",
                "output_dir": str(output_dir),
            }
            status_rows.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
        _write_status_csv(status_csv, status_rows)
        return

    for index, row in enumerate(rows, start=1):
        print(f"[{index}/{len(rows)}] {row['pdf_id']} {row['file_name']}", flush=True)
        try:
            record = _run_one_pdf(
                row=row,
                config=config,
                output_root=args.output_root,
                max_wait_seconds=args.max_wait,
                force=args.force,
            )
        except Exception as exc:
            record = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "pdf_id": row.get("pdf_id", ""),
                "file_name": row.get("file_name", ""),
                "file_path": row.get("file_path", ""),
                "parse_status": row.get("parse_status", ""),
                "status": "failed",
                "output_dir": str(args.output_root / str(row.get("pdf_id", ""))),
                "error": str(exc),
            }
        status_rows.append(record)
        _write_jsonl(events_jsonl, record)
        _write_status_csv(status_csv, status_rows)
        print(json.dumps(record, ensure_ascii=False), flush=True)
        if args.sleep_between > 0 and index < len(rows):
            time.sleep(args.sleep_between)

    summary = {
        "completed_at": datetime.now().isoformat(timespec="seconds"),
        "rows": len(status_rows),
        "status_counts": {},
        "status_csv": str(status_csv),
        "events_jsonl": str(events_jsonl),
    }
    for record in status_rows:
        summary["status_counts"][record["status"]] = summary["status_counts"].get(record["status"], 0) + 1
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
