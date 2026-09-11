from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.config import PROJECT_ROOT, discover_pdf_root
from backend.db.session import connect


MATERIAL_RE = re.compile(
    r"\b(hfo2|hzo|hfzro|hf0\.?5zr0\.?5o2|hf1[-−]?xzrxo2|hafnia|hafnium oxide|hafnium zirconium oxide)\b",
    re.I,
)
FERRO_RE = re.compile(r"\b(ferroelectric|ferroelectricity|remanent|polarization|coercive|wake[- ]?up|fatigue|pund)\b", re.I)
PROCESS_RE = re.compile(r"\b(ald|atomic layer deposition|sputter|pld|anneal|rta|pma|pda|tin|electrode|orthorhombic|pca21)\b", re.I)
OFF_TOPIC_RE = re.compile(r"\b(batio3|barium titanate|pzt|pbzr|bifeo3|pvdf|polymer|microglia|clinical|protein|genome)\b", re.I)


def score_pdf(row: dict[str, object]) -> tuple[int, str]:
    title = str(row.get("title") or "")
    file_name = str(row.get("file_name") or "")
    sample_text = str(row.get("sample_text") or "")
    text = f"{title}\n{file_name}\n{sample_text}"
    material_hits = len(MATERIAL_RE.findall(text))
    ferro_hits = len(FERRO_RE.findall(text))
    process_hits = len(PROCESS_RE.findall(text))
    off_topic_hits = len(OFF_TOPIC_RE.findall(text))
    hfo2_chunks = int(row.get("hfo2_chunks") or 0)
    process_chunks = int(row.get("process_chunks") or 0)
    property_chunks = int(row.get("property_chunks") or 0)
    high_value_chunks = int(row.get("high_value_chunks") or 0)
    table_chunks = int(row.get("table_chunks") or 0)

    score = 0
    score += min(30, material_hits * 5)
    score += min(20, ferro_hits * 4)
    score += min(12, process_hits * 2)
    score += min(30, high_value_chunks * 3)
    score += min(12, hfo2_chunks * 2)
    score += min(8, process_chunks)
    score += min(8, property_chunks)
    score += min(4, table_chunks)
    if off_topic_hits and material_hits == 0 and hfo2_chunks == 0:
        score -= min(20, off_topic_hits * 5)

    if high_value_chunks > 0:
        reason = "keep: high-value HfO2/HZO/process/property chunks"
    elif material_hits > 0 and (ferro_hits > 0 or process_hits > 0 or property_chunks > 0):
        reason = "keep: material keyword plus ferroelectric/process/property evidence"
    elif material_hits > 0:
        reason = "weak_keep: material keyword only"
    elif ferro_hits > 1 and process_hits > 0:
        reason = "weak_keep: ferroelectric/process context without explicit HfO2"
    elif off_topic_hits:
        reason = "isolate: off-topic terms and no HfO2/HZO evidence"
    else:
        reason = "isolate: no HfO2/HZO/ferroelectric evidence"
    return score, reason


def classify_and_isolate(
    db_path: Path | None,
    output_dir: Path,
    threshold: int,
    dry_run: bool,
    limit: int | None,
) -> dict[str, object]:
    pdf_root = discover_pdf_root().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_csv = output_dir / "irrelevant_pdf_isolation_report.csv"
    report_json = output_dir / "irrelevant_pdf_isolation_report.json"
    isolated_root = output_dir / "pdfs"
    rows_out: list[dict[str, object]] = []
    moved = 0
    selected = 0

    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                pf.pdf_id,
                pf.paper_id,
                pf.file_name,
                pf.file_path,
                pf.is_duplicate,
                p.title,
                p.doi,
                p.year,
                COALESCE(dc_stats.total_chunks, 0) AS total_chunks,
                COALESCE(dc_stats.hfo2_chunks, 0) AS hfo2_chunks,
                COALESCE(dc_stats.process_chunks, 0) AS process_chunks,
                COALESCE(dc_stats.property_chunks, 0) AS property_chunks,
                COALESCE(dc_stats.high_value_chunks, 0) AS high_value_chunks,
                COALESCE(dc_stats.table_chunks, 0) AS table_chunks,
                COALESCE(pp_text.sample_text, '') AS sample_text
            FROM pdf_files pf
            LEFT JOIN papers p ON p.paper_id = pf.paper_id
            LEFT JOIN (
                SELECT
                    pdf_id,
                    COUNT(*) AS total_chunks,
                    SUM(contains_hfo2_keyword) AS hfo2_chunks,
                    SUM(contains_process_keyword) AS process_chunks,
                    SUM(contains_property_keyword) AS property_chunks,
                    SUM(is_high_value) AS high_value_chunks,
                    SUM(contains_table) AS table_chunks
                FROM document_chunks
                GROUP BY pdf_id
            ) dc_stats ON dc_stats.pdf_id = pf.pdf_id
            LEFT JOIN (
                SELECT pdf_id, GROUP_CONCAT(SUBSTR(text, 1, 900), '\n') AS sample_text
                FROM (
                    SELECT pdf_id, page_number, text
                    FROM parsed_pages
                    WHERE page_number <= 3
                    ORDER BY pdf_id, page_number
                )
                GROUP BY pdf_id
            ) pp_text ON pp_text.pdf_id = pf.pdf_id
            WHERE pf.is_duplicate = 0
              AND pf.file_path NOT LIKE '%/data/unrelated_pdfs/%'
            ORDER BY pf.file_name
            """
        ).fetchall()
        if limit is not None:
            rows = rows[:limit]

        isolated_pdf_ids: list[str] = []
        for row in rows:
            row_dict = dict(row)
            score, reason = score_pdf(row_dict)
            should_isolate = score < threshold and reason.startswith("isolate")
            status = "isolate" if should_isolate else "keep"
            old_path = Path(str(row_dict["file_path"]))
            new_path = ""
            selected += int(should_isolate)
            if should_isolate:
                isolated_pdf_ids.append(str(row_dict["pdf_id"]))
                try:
                    relative = old_path.resolve().relative_to(pdf_root)
                except Exception:
                    relative = Path(old_path.name)
                target_path = isolated_root / relative
                new_path = str(target_path)
                if not dry_run:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    if old_path.exists():
                        if target_path.exists():
                            target_path = target_path.with_name(f"{row_dict['pdf_id']}_{target_path.name}")
                            new_path = str(target_path)
                        shutil.move(str(old_path), str(target_path))
                        conn.execute(
                            "UPDATE pdf_files SET file_path = ? WHERE pdf_id = ?",
                            (str(target_path), row_dict["pdf_id"]),
                        )
                    conn.execute(
                        "UPDATE pdf_files SET parse_status = ? WHERE pdf_id = ?",
                        ("excluded_irrelevant", row_dict["pdf_id"]),
                    )
                    moved += 1
            rows_out.append(
                {
                    "status": status,
                    "score": score,
                    "reason": reason,
                    "pdf_id": row_dict["pdf_id"],
                    "paper_id": row_dict["paper_id"],
                    "title": row_dict.get("title") or "",
                    "doi": row_dict.get("doi") or "",
                    "year": row_dict.get("year") or "",
                    "file_name": row_dict["file_name"],
                    "old_path": str(old_path),
                    "new_path": new_path,
                    "total_chunks": row_dict["total_chunks"],
                    "hfo2_chunks": row_dict["hfo2_chunks"],
                    "process_chunks": row_dict["process_chunks"],
                    "property_chunks": row_dict["property_chunks"],
                    "high_value_chunks": row_dict["high_value_chunks"],
                    "table_chunks": row_dict["table_chunks"],
                }
            )
        if not dry_run and isolated_pdf_ids:
            placeholders = ",".join("?" for _ in isolated_pdf_ids)
            for table in ["reviewed_facts", "extraction_candidates", "document_chunks", "pdf_tables", "parsed_pages"]:
                conn.execute(f"DELETE FROM {table} WHERE pdf_id IN ({placeholders})", isolated_pdf_ids)
        if not dry_run:
            conn.commit()

    fieldnames = list(rows_out[0].keys()) if rows_out else []
    with report_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_out)
    report_json.write_text(json.dumps(rows_out, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "dry_run": dry_run,
        "threshold": threshold,
        "pdfs_reviewed": len(rows_out),
        "selected_for_isolation": selected,
        "moved": moved,
        "output_dir": str(output_dir),
        "report_csv": str(report_csv),
        "report_json": str(report_json),
        "isolated_pdf_dir": str(isolated_root),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "data" / "unrelated_pdfs")
    parser.add_argument("--threshold", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    print(json.dumps(classify_and_isolate(args.db_path, args.output_dir, args.threshold, args.dry_run, args.limit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
