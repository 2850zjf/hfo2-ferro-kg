from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path

import fitz

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.pipeline_log import record_pipeline_run


@dataclass(frozen=True)
class PDFManifestRow:
    pdf_id: str
    paper_id: str
    file_name: str
    file_path: str
    sha256: str
    file_size: int
    page_count: int | None
    is_duplicate: bool
    duplicate_of_pdf_id: str | None
    error_message: str | None = None


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(block_size):
            digest.update(chunk)
    return digest.hexdigest()


def count_pdf_pages(path: Path) -> tuple[int | None, str | None]:
    try:
        with fitz.open(path) as doc:
            return doc.page_count, None
    except Exception as exc:  # pragma: no cover - depends on malformed PDFs
        return None, str(exc)


def _stable_pdf_id(sha256: str, duplicate_index: int = 0) -> str:
    suffix = sha256[:16] if duplicate_index == 0 else f"{sha256[:12]}_{duplicate_index}"
    return f"pdf_{suffix}"


def _stable_paper_id(sha256: str) -> str:
    return f"paper_{sha256[:16]}"


def scan_pdfs(pdf_root: Path) -> list[PDFManifestRow]:
    paths = sorted(
        {
            path.resolve()
            for path in pdf_root.rglob("*")
            if path.is_file() and path.suffix.lower() == ".pdf"
        }
    )
    seen: dict[str, str] = {}
    duplicate_counts: dict[str, int] = {}
    rows: list[PDFManifestRow] = []

    for path in paths:
        file_hash = sha256_file(path)
        duplicate_of = seen.get(file_hash)
        duplicate_index = duplicate_counts.get(file_hash, 0)
        duplicate_counts[file_hash] = duplicate_index + 1
        if duplicate_of is None:
            pdf_id = _stable_pdf_id(file_hash)
            seen[file_hash] = pdf_id
        else:
            pdf_id = _stable_pdf_id(file_hash, duplicate_index)

        page_count, error = count_pdf_pages(path)
        rows.append(
            PDFManifestRow(
                pdf_id=pdf_id,
                paper_id=_stable_paper_id(file_hash),
                file_name=path.name,
                file_path=str(path.resolve()),
                sha256=file_hash,
                file_size=path.stat().st_size,
                page_count=page_count,
                is_duplicate=duplicate_of is not None,
                duplicate_of_pdf_id=duplicate_of,
                error_message=error,
            )
        )

    return rows


def save_manifest(rows: list[PDFManifestRow], csv_path: Path, db_path: Path | None = None) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(PDFManifestRow.__dataclass_fields__))
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)

    with connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO papers (paper_id, source_pdf_id)
            VALUES (?, ?)
            ON CONFLICT(paper_id) DO UPDATE SET source_pdf_id = excluded.source_pdf_id
            """,
            [(row.paper_id, row.pdf_id) for row in rows if not row.is_duplicate],
        )
        conn.executemany(
            """
            INSERT INTO pdf_files (
                pdf_id, file_name, file_path, sha256, file_size, page_count,
                parse_status, is_duplicate, duplicate_of_pdf_id, paper_id, error_message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_path) DO UPDATE SET
                file_name = excluded.file_name,
                sha256 = excluded.sha256,
                file_size = excluded.file_size,
                page_count = excluded.page_count,
                is_duplicate = excluded.is_duplicate,
                duplicate_of_pdf_id = excluded.duplicate_of_pdf_id,
                paper_id = excluded.paper_id,
                error_message = excluded.error_message
            """,
            [
                (
                    row.pdf_id,
                    row.file_name,
                    row.file_path,
                    row.sha256,
                    row.file_size,
                    row.page_count,
                    "manifested" if row.error_message is None else "manifest_error",
                    int(row.is_duplicate),
                    row.duplicate_of_pdf_id,
                    row.paper_id,
                    row.error_message,
                )
                for row in rows
            ],
        )
        conn.commit()


def build_manifest(pdf_root: Path, output_dir: Path | None = None) -> list[PDFManifestRow]:
    rows = scan_pdfs(pdf_root)
    target_dir = output_dir or PROJECT_ROOT / "data" / "manifest"
    save_manifest(rows, target_dir / "pdf_manifest.csv")
    record_pipeline_run(
        "01_build_manifest",
        "ok",
        {
            "pdf_root": str(pdf_root.resolve()),
            "manifest_rows": len(rows),
            "duplicates": sum(row.is_duplicate for row in rows),
            "read_errors": sum(1 for row in rows if row.error_message),
        },
    )
    return rows
