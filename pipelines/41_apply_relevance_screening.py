from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.config import PROJECT_ROOT, discover_pdf_root
from backend.db.session import connect


def apply_relevance_screening(
    db_path: Path | None,
    output_dir: Path,
    dry_run: bool,
    tier: str = "irrelevant",
) -> dict[str, object]:
    pdf_root = discover_pdf_root().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    isolated_root = PROJECT_ROOT / "data" / "unrelated_pdfs" / "pdfs"
    report_csv = output_dir / f"applied_{tier}_isolation_report.csv"
    report_json = output_dir / f"applied_{tier}_isolation_report.json"
    rows_out: list[dict[str, object]] = []
    moved = 0

    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                pf.pdf_id, pf.paper_id, pf.file_name, pf.file_path, pf.parse_status,
                p.title, p.doi, p.year,
                s.final_tier, s.extraction_policy, s.llm_tier, s.llm_confidence,
                s.rule_reasons_json, s.llm_reasons_json
            FROM pdf_files pf
            JOIN literature_relevance_screenings s ON s.pdf_id = pf.pdf_id
            LEFT JOIN papers p ON p.paper_id = pf.paper_id
            WHERE s.final_tier = ?
              AND COALESCE(pf.parse_status, '') != 'excluded_irrelevant'
              AND pf.file_path NOT LIKE '%/data/unrelated_pdfs/%'
            ORDER BY p.title, pf.file_name
            """,
            (tier,),
        ).fetchall()
        pdf_ids: list[str] = []
        for row in rows:
            old_path = Path(str(row["file_path"]))
            new_path = ""
            if not dry_run:
                try:
                    relative = old_path.resolve().relative_to(pdf_root)
                except Exception:
                    relative = Path(old_path.name)
                target_path = isolated_root / relative
                target_path.parent.mkdir(parents=True, exist_ok=True)
                if old_path.exists():
                    if target_path.exists():
                        target_path = target_path.with_name(f"{row['pdf_id']}_{target_path.name}")
                    shutil.move(str(old_path), str(target_path))
                new_path = str(target_path)
                conn.execute(
                    "UPDATE pdf_files SET file_path = ?, parse_status = ? WHERE pdf_id = ?",
                    (new_path, "excluded_irrelevant", row["pdf_id"]),
                )
                moved += 1
            pdf_ids.append(str(row["pdf_id"]))
            rows_out.append(
                {
                    "pdf_id": row["pdf_id"],
                    "paper_id": row["paper_id"],
                    "title": row["title"] or "",
                    "doi": row["doi"] or "",
                    "year": row["year"] or "",
                    "file_name": row["file_name"],
                    "old_path": str(old_path),
                    "new_path": new_path,
                    "previous_parse_status": row["parse_status"] or "",
                    "final_tier": row["final_tier"],
                    "extraction_policy": row["extraction_policy"],
                    "llm_tier": row["llm_tier"] or "",
                    "llm_confidence": row["llm_confidence"] if row["llm_confidence"] is not None else "",
                    "rule_reasons_json": row["rule_reasons_json"] or "[]",
                    "llm_reasons_json": row["llm_reasons_json"] or "[]",
                }
            )
        if pdf_ids and not dry_run:
            placeholders = ",".join("?" for _ in pdf_ids)
            for table in ["reviewed_facts", "extraction_candidates", "document_chunks", "pdf_tables", "parsed_pages"]:
                conn.execute(f"DELETE FROM {table} WHERE pdf_id IN ({placeholders})", pdf_ids)
            conn.commit()

    fieldnames = list(rows_out[0].keys()) if rows_out else []
    with report_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_out)
    report_json.write_text(json.dumps(rows_out, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "dry_run": dry_run,
        "tier": tier,
        "selected": len(rows_out),
        "moved": moved,
        "isolated_pdf_dir": str(isolated_root),
        "report_csv": str(report_csv),
        "report_json": str(report_json),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply literature relevance screening actions.")
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "data" / "relevance_screening")
    parser.add_argument("--tier", default="irrelevant")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            apply_relevance_screening(
                db_path=args.db_path,
                output_dir=args.output_dir,
                dry_run=args.dry_run,
                tier=args.tier,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
