from __future__ import annotations

import csv
import html
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz

from backend.core.config import PROJECT_ROOT, discover_pdf_root
from backend.db.session import connect
from backend.services.pdf_manifest import count_pdf_pages, sha256_file


DEFAULT_CURATION_CSV = PROJECT_ROOT / "data" / "literature" / "hzo_missing_high_quality_download_list.csv"
DEFAULT_WORKBENCH_DIR = PROJECT_ROOT / "data" / "literature" / "manual_download_workbench"
DEFAULT_INBOX_DIR = PROJECT_ROOT / "data" / "literature" / "manual_download_inbox"


@dataclass(frozen=True)
class DownloadItem:
    rank: int
    priority: str
    download_batch: str
    theme: str
    title: str
    year: str
    venue: str
    doi: str
    download_url: str
    pdf_url: str
    landing_page_url: str
    recommended_action: str
    kg_value: str
    why_include: str
    suggested_file_name: str


def _normalize_doi(value: str | None) -> str:
    if not value:
        return ""
    doi = value.strip().lower()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    return doi.strip()


def _normalize_text(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _safe_slug(value: str, max_len: int = 90) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    slug = re.sub(r"_+", "_", slug).strip("._-")
    return (slug or "hzo_paper")[:max_len]


def _suggested_file_name(rank: int, row: dict[str, str]) -> str:
    year = str(row.get("year") or "unknown")
    doi = _normalize_doi(row.get("doi"))
    title = row.get("title") or "HZO paper"
    source = doi or title
    return f"{rank:03d}_{year}_{_safe_slug(source)}.pdf"


def load_curation_rows(curation_csv: Path = DEFAULT_CURATION_CSV) -> list[DownloadItem]:
    with curation_csv.open("r", encoding="utf-8-sig", newline="") as fh:
        raw_rows = list(csv.DictReader(fh))
    items: list[DownloadItem] = []
    for rank, row in enumerate(raw_rows, start=1):
        items.append(
            DownloadItem(
                rank=rank,
                priority=row.get("priority", ""),
                download_batch=row.get("download_batch", ""),
                theme=row.get("theme", ""),
                title=row.get("title", ""),
                year=str(row.get("year", "")),
                venue=row.get("venue", ""),
                doi=_normalize_doi(row.get("doi")),
                download_url=row.get("download_url", ""),
                pdf_url=row.get("pdf_url", ""),
                landing_page_url=row.get("landing_page_url", ""),
                recommended_action=row.get("recommended_action", ""),
                kg_value=row.get("kg_value", ""),
                why_include=row.get("why_include", ""),
                suggested_file_name=_suggested_file_name(rank, row),
            )
        )
    return items


def _write_checklist(items: list[DownloadItem], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "rank",
        "priority",
        "download_batch",
        "theme",
        "title",
        "year",
        "venue",
        "doi",
        "recommended_action",
        "download_url",
        "pdf_url",
        "landing_page_url",
        "suggested_file_name",
        "kg_value",
        "why_include",
        "download_status",
        "actual_file_name",
        "notes",
    ]
    with output_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for item in items:
            row = {field: getattr(item, field, "") for field in fieldnames if hasattr(item, field)}
            row.update({"download_status": "todo", "actual_file_name": "", "notes": ""})
            writer.writerow(row)


def _write_links_html(items: list[DownloadItem], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for item in items:
        url = item.download_url or item.pdf_url or item.landing_page_url
        rows.append(
            f"""
            <tr>
              <td>{item.rank}</td>
              <td><span class="pill">{html.escape(item.priority)}</span></td>
              <td>{html.escape(item.theme)}</td>
              <td class="title">{html.escape(item.title)}</td>
              <td>{html.escape(item.year)}</td>
              <td>{html.escape(item.venue)}</td>
              <td><code>{html.escape(item.doi)}</code></td>
              <td>{html.escape(item.recommended_action)}</td>
              <td><a href="{html.escape(url)}" target="_blank" rel="noreferrer">打开下载入口</a></td>
              <td><code>{html.escape(item.suggested_file_name)}</code></td>
            </tr>
            """
        )
    output_path.write_text(
        f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>HfO2/HZO 缺失文献第一批下载页</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #2f3441; margin: 28px; background: #f8fafc; }}
    h1 {{ margin: 0 0 8px; font-size: 26px; }}
    p {{ color: #687080; max-width: 980px; line-height: 1.55; }}
    table {{ border-collapse: collapse; width: 100%; background: white; border: 1px solid #d7dcea; }}
    th, td {{ border: 1px solid #d7dcea; padding: 8px 9px; vertical-align: top; font-size: 13px; }}
    th {{ background: #d3e8f1; text-align: left; }}
    .title {{ min-width: 330px; font-weight: 650; }}
    .pill {{ background: #ded8ed; border-radius: 999px; padding: 3px 8px; white-space: nowrap; }}
    code {{ font-size: 12px; color: #3f4654; }}
    a {{ color: #315f8f; font-weight: 650; }}
  </style>
</head>
<body>
  <h1>HfO2/HZO 缺失文献第一批下载页</h1>
  <p>只列出 Excel 中“第一批：论文主线必读/必下”的条目。请手动下载 PDF，建议按最后一列文件名保存，并放入 <code>{html.escape(str(DEFAULT_INBOX_DIR))}</code>。本页不会自动下载或上传任何文件。</p>
  <table>
    <thead>
      <tr>
        <th>#</th><th>优先级</th><th>主题</th><th>题名</th><th>年份</th><th>期刊/会议</th><th>DOI</th><th>建议动作</th><th>入口</th><th>建议文件名</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
</body>
</html>
""",
        encoding="utf-8",
    )


def _write_readme(workbench_dir: Path, inbox_dir: Path, first_batch_count: int, all_count: int) -> Path:
    readme = workbench_dir / "README.md"
    readme.write_text(
        f"""# HfO2/HZO Manual Download Workbench

This folder is for manual paper acquisition. It does not auto-download PDFs.

## What to download

- First batch: {first_batch_count} papers in `first_batch_download_links.html`
- Full strict missing queue: {all_count} papers in `download_checklist.csv`

## How to use

1. Open `first_batch_download_links.html` in a browser.
2. Download PDFs manually from DOI/OA/publisher pages.
3. Put downloaded PDFs into:

   `{inbox_dir}`

4. Run:

   `python3 pipelines/46_scan_manual_downloads.py`

5. After the scan report looks good, ingest with:

   `python3 pipelines/46_scan_manual_downloads.py --copy-to-raw-pdfs --apply`

6. Rebuild the manifest and parse only after ingest:

   `python3 pipelines/01_build_manifest.py`

## Safety

- This workflow does not store account credentials or API keys.
- It does not bypass publisher access controls.
- Default scan mode is read-only and does not copy PDFs into the main corpus.
""",
        encoding="utf-8",
    )
    return readme


def prepare_manual_download_workbench(
    curation_csv: Path = DEFAULT_CURATION_CSV,
    workbench_dir: Path = DEFAULT_WORKBENCH_DIR,
    inbox_dir: Path = DEFAULT_INBOX_DIR,
) -> dict[str, Any]:
    items = load_curation_rows(curation_csv)
    first_batch = [item for item in items if item.download_batch.startswith("第一批")]
    workbench_dir.mkdir(parents=True, exist_ok=True)
    inbox_dir.mkdir(parents=True, exist_ok=True)

    checklist_path = workbench_dir / "download_checklist.csv"
    first_batch_path = workbench_dir / "first_batch_download_checklist.csv"
    html_path = workbench_dir / "first_batch_download_links.html"

    _write_checklist(items, checklist_path)
    _write_checklist(first_batch, first_batch_path)
    _write_links_html(first_batch, html_path)
    readme_path = _write_readme(workbench_dir, inbox_dir, len(first_batch), len(items))

    return {
        "status": "ok",
        "all_items": len(items),
        "first_batch_items": len(first_batch),
        "workbench_dir": str(workbench_dir),
        "inbox_dir": str(inbox_dir),
        "checklist_csv": str(checklist_path),
        "first_batch_csv": str(first_batch_path),
        "first_batch_html": str(html_path),
        "readme": str(readme_path),
    }


def _pdf_text_probe(path: Path, max_pages: int = 2, max_chars: int = 4000) -> str:
    try:
        with fitz.open(path) as doc:
            text_parts = []
            for page_index in range(min(max_pages, doc.page_count)):
                text_parts.append(doc.load_page(page_index).get_text("text"))
        return "\n".join(text_parts)[:max_chars]
    except Exception:
        return ""


def _best_match(path: Path, probe_text: str, items: list[DownloadItem]) -> tuple[DownloadItem | None, str, float]:
    haystack = _normalize_text(f"{path.name} {probe_text}")
    best: tuple[DownloadItem | None, str, float] = (None, "unmatched", 0.0)
    for item in items:
        doi = _normalize_doi(item.doi)
        if doi and doi.replace("/", " ") in haystack.replace("_", " "):
            return item, "doi", 1.0
        title_norm = _normalize_text(item.title)
        title_tokens = [token for token in title_norm.split() if len(token) > 3]
        if not title_tokens:
            continue
        matched = sum(1 for token in title_tokens if token in haystack)
        score = matched / max(1, len(title_tokens))
        if score > best[2]:
            best = (item, "title_tokens", score)
    if best[2] >= 0.55:
        return best
    return None, "unmatched", best[2]


def scan_manual_downloads(
    curation_csv: Path = DEFAULT_CURATION_CSV,
    inbox_dir: Path = DEFAULT_INBOX_DIR,
    output_dir: Path = DEFAULT_WORKBENCH_DIR,
    copy_to_raw_pdfs: bool = False,
    apply: bool = False,
    db_path: Path | None = None,
) -> dict[str, Any]:
    items = load_curation_rows(curation_csv)
    output_dir.mkdir(parents=True, exist_ok=True)
    inbox_dir.mkdir(parents=True, exist_ok=True)
    raw_manual_dir = discover_pdf_root() / "manual_downloads"
    if copy_to_raw_pdfs and apply:
        raw_manual_dir.mkdir(parents=True, exist_ok=True)

    with connect(db_path) as conn:
        existing_hashes = {
            row["sha256"]
            for row in conn.execute("SELECT sha256 FROM pdf_files").fetchall()
            if row["sha256"]
        }

    scan_rows: list[dict[str, Any]] = []
    copied = 0
    for path in sorted(inbox_dir.glob("*.pdf")):
        row: dict[str, Any] = {
            "file_name": path.name,
            "file_path": str(path.resolve()),
            "file_size": path.stat().st_size,
            "sha256": "",
            "page_count": "",
            "status": "pending",
            "matched_rank": "",
            "matched_priority": "",
            "matched_title": "",
            "matched_doi": "",
            "match_method": "",
            "match_score": "",
            "copy_target": "",
            "error": "",
        }
        try:
            digest = sha256_file(path)
            page_count, error = count_pdf_pages(path)
            row["sha256"] = digest
            row["page_count"] = page_count or ""
            if error:
                row["status"] = "invalid_pdf"
                row["error"] = error
                scan_rows.append(row)
                continue
            if digest in existing_hashes:
                row["status"] = "duplicate_existing_corpus"
                scan_rows.append(row)
                continue
            probe = _pdf_text_probe(path)
            match, method, score = _best_match(path, probe, items)
            row["match_method"] = method
            row["match_score"] = round(score, 3)
            if match is None:
                row["status"] = "valid_unmatched"
            else:
                row["status"] = "matched_ready"
                row["matched_rank"] = match.rank
                row["matched_priority"] = match.priority
                row["matched_title"] = match.title
                row["matched_doi"] = match.doi
                target = raw_manual_dir / match.suggested_file_name
                row["copy_target"] = str(target.resolve())
                if copy_to_raw_pdfs and apply:
                    shutil.copy2(path, target)
                    copied += 1
        except Exception as exc:  # pragma: no cover - defensive for local files
            row["status"] = "scan_error"
            row["error"] = str(exc)[:500]
        scan_rows.append(row)

    csv_path = output_dir / "manual_download_scan.csv"
    json_path = output_dir / "manual_download_scan.json"
    fieldnames = [
        "file_name",
        "file_path",
        "file_size",
        "sha256",
        "page_count",
        "status",
        "matched_rank",
        "matched_priority",
        "matched_title",
        "matched_doi",
        "match_method",
        "match_score",
        "copy_target",
        "error",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(scan_rows)
    json_path.write_text(json.dumps(scan_rows, ensure_ascii=False, indent=2), encoding="utf-8")

    status_counts: dict[str, int] = {}
    for row in scan_rows:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
    report_path = output_dir / "manual_download_scan_report.md"
    report_path.write_text(
        "\n".join(
            [
                "# Manual Download Scan Report",
                "",
                f"- Inbox: `{inbox_dir}`",
                f"- PDFs scanned: {len(scan_rows)}",
                f"- Copied to raw corpus: {copied}",
                f"- Mode: {'apply' if apply else 'dry-run'}",
                "",
                "## Status Counts",
                "",
                *[f"- {key}: {value}" for key, value in sorted(status_counts.items())],
                "",
                "## Next",
                "",
                "If matches look correct, run with `--copy-to-raw-pdfs --apply`, then rebuild manifest and parse pending PDFs.",
            ]
        ),
        encoding="utf-8",
    )

    return {
        "status": "ok",
        "inbox_dir": str(inbox_dir),
        "pdfs_scanned": len(scan_rows),
        "copied": copied,
        "status_counts": status_counts,
        "scan_csv": str(csv_path),
        "scan_json": str(json_path),
        "report": str(report_path),
    }
