from __future__ import annotations

import csv
import json
import multiprocessing as mp
import uuid
from pathlib import Path
from typing import Any

import pdfplumber

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.pipeline_log import record_pipeline_run


def normalize_cell(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def normalize_table(table: list[list[object]]) -> list[list[str]]:
    return [[normalize_cell(cell) for cell in row] for row in table if any(normalize_cell(cell) for cell in row)]


def table_to_text(table: list[list[str]]) -> str:
    if not table:
        return ""
    return "\n".join(" | ".join(row) for row in table)


def table_dimensions(table: list[list[str]]) -> tuple[int, int]:
    return len(table), max((len(row) for row in table), default=0)


def table_id_for(pdf_id: str, page_number: int, table_index: int) -> str:
    return f"{pdf_id}_table_p{page_number:04d}_{table_index:03d}"


def _extract_tables_worker(row: dict[str, Any], queue: mp.Queue) -> None:
    try:
        tables_for_pdf = []
        with pdfplumber.open(row["file_path"]) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                for table_index, table in enumerate(page.extract_tables() or [], start=1):
                    normalized = normalize_table(table)
                    if normalized:
                        row_count, col_count = table_dimensions(normalized)
                        tables_for_pdf.append(
                            {
                                "table_id": table_id_for(row["pdf_id"], page_number, table_index),
                                "paper_id": row["paper_id"],
                                "pdf_id": row["pdf_id"],
                                "page_number": page_number,
                                "table_index": table_index,
                                "row_count": row_count,
                                "col_count": col_count,
                                "table_json": json.dumps(normalized, ensure_ascii=False),
                                "table_text": table_to_text(normalized),
                                "rows": normalized,
                            }
                        )
        queue.put({"ok": True, "tables": tables_for_pdf})
    except Exception as exc:
        queue.put({"ok": False, "error": str(exc)})


def _extract_tables_with_timeout(row: dict[str, Any], timeout_seconds: int) -> tuple[list[dict[str, Any]], str | None]:
    queue: mp.Queue = mp.Queue()
    process = mp.Process(target=_extract_tables_worker, args=(row, queue))
    process.start()
    process.join(timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join(5)
        return [], f"table extraction timed out after {timeout_seconds}s"
    if queue.empty():
        return [], None if process.exitcode == 0 else f"table extraction worker exited with code {process.exitcode}"
    payload = queue.get()
    if payload.get("ok"):
        return payload.get("tables") or [], None
    return [], payload.get("error") or "unknown table extraction error"


def extract_tables(
    limit_pdfs: int | None = None,
    db_path: Path | None = None,
    incremental: bool = False,
    timeout_seconds: int = 120,
    progress_every: int | None = 10,
) -> dict[str, int]:
    output_dir = PROJECT_ROOT / "data" / "tables"
    output_dir.mkdir(parents=True, exist_ok=True)
    stats = {"pdfs": 0, "tables": 0, "table_rows": 0, "failed": 0}

    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT pdf_id, paper_id, file_path
            FROM pdf_files
            WHERE parse_status IN ('parsed', 'manifested')
              AND (? = 0 OR COALESCE(table_parse_status, 'pending') != 'parsed')
            ORDER BY file_name
            """,
            (int(incremental),),
        ).fetchall()
        if limit_pdfs is not None:
            rows = rows[:limit_pdfs]

        for row in rows:
            stats["pdfs"] += 1
            tables_for_pdf = []
            conn.execute("DELETE FROM pdf_tables WHERE pdf_id = ?", (row["pdf_id"],))
            tables_for_pdf, error = _extract_tables_with_timeout(dict(row), timeout_seconds)
            if error:
                stats["failed"] += 1
                conn.execute(
                    """
                    INSERT INTO pdf_table_errors (error_id, pdf_id, error_message)
                    VALUES (?, ?, ?)
                    """,
                    (f"table_error_{uuid.uuid4().hex[:16]}", row["pdf_id"], str(error)),
                )
                conn.execute(
                    "UPDATE pdf_files SET table_parse_status = ?, error_message = ? WHERE pdf_id = ?",
                    ("table_error", str(error), row["pdf_id"]),
                )
            else:
                for table_record in tables_for_pdf:
                    stats["tables"] += 1
                    stats["table_rows"] += int(table_record["row_count"])
                    conn.execute(
                        """
                        INSERT INTO pdf_tables (
                            table_id, paper_id, pdf_id, page_number, table_index,
                            row_count, col_count, table_json, table_text
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(pdf_id, page_number, table_index) DO UPDATE SET
                            row_count = excluded.row_count,
                            col_count = excluded.col_count,
                            table_json = excluded.table_json,
                            table_text = excluded.table_text,
                            extraction_status = 'ok',
                            error_message = NULL
                        """,
                        (
                            table_record["table_id"],
                            table_record["paper_id"],
                            table_record["pdf_id"],
                            table_record["page_number"],
                            table_record["table_index"],
                            table_record["row_count"],
                            table_record["col_count"],
                            table_record["table_json"],
                            table_record["table_text"],
                        ),
                    )
                if tables_for_pdf:
                    target = output_dir / f"{row['pdf_id']}_tables.json"
                    target.write_text(
                        json.dumps(tables_for_pdf, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    csv_target = output_dir / f"{row['pdf_id']}_tables.csv"
                    with csv_target.open("w", newline="", encoding="utf-8-sig") as fh:
                        writer = csv.writer(fh)
                        writer.writerow(["table_id", "page_number", "table_index", "row_text"])
                        for table_record in tables_for_pdf:
                            for table_row in table_record["rows"]:
                                writer.writerow(
                                    [
                                        table_record["table_id"],
                                        table_record["page_number"],
                                        table_record["table_index"],
                                        " | ".join(table_row),
                                    ]
                                )
                conn.execute(
                    "UPDATE pdf_files SET table_parse_status = ? WHERE pdf_id = ?",
                    ("parsed", row["pdf_id"]),
                )
            if progress_every and stats["pdfs"] % progress_every == 0:
                print(
                    "tables processed={pdfs} tables={tables} rows={table_rows} failed={failed}".format(
                        **stats
                    ),
                    flush=True,
                )
        conn.commit()

    record_pipeline_run("03_extract_tables", "ok", stats, db_path=db_path)
    return stats
