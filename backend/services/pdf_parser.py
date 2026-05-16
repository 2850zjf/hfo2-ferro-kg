from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import fitz

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.pipeline_log import record_pipeline_run


DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(20[12]\d)\b")
FILENAME_YEAR_RE = re.compile(r"^(20[12]\d)[_\-\s]")
REVIEW_HINT_RE = re.compile(
    r"\b(review|progress|perspective|status|prospects|bulletin|comprehensive)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedPage:
    page_id: str
    paper_id: str
    pdf_id: str
    page_number: int
    text: str
    char_count: int
    parse_status: str = "ok"


def clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_doi(raw: str | None) -> str | None:
    if not raw:
        return None
    return raw.strip().rstrip(".,;)]}").lower()


def title_from_filename(pdf_path: Path) -> str:
    title = re.sub(r"^20[12]\d[_\-\s]+", "", pdf_path.stem)
    title = title.replace("_", " ")
    title = re.sub(r"\s+", " ", title).strip()
    return title or pdf_path.stem


def year_from_filename(pdf_path: Path) -> int | None:
    match = FILENAME_YEAR_RE.search(pdf_path.name)
    return int(match.group(1)) if match else None


def classify_paper_type(title: str, filename: str) -> tuple[str, bool]:
    text = f"{title} {filename}"
    if re.search(r"\b(first[-\s]?principles|DFT|phase[-\s]?field|simulation|model)\b", text, re.I):
        return "computational", False
    if re.search(r"\b(theoretical|theory)\b", text, re.I):
        return "theoretical", False
    if REVIEW_HINT_RE.search(text):
        return "review", True
    return "experimental", False


def extract_initial_metadata(text: str, fallback_title: str) -> dict[str, str | int | None]:
    doi_match = DOI_RE.search(text)
    year_match = YEAR_RE.search(text)
    lines = [line.strip() for line in text.splitlines() if len(line.strip()) > 8]
    title = fallback_title
    for line in lines[:12]:
        lower = line.lower()
        if "journal" in lower or "doi" in lower or "copyright" in lower:
            continue
        if 20 <= len(line) <= 220:
            title = line
            break
    return {
        "doi": normalize_doi(doi_match.group(0)) if doi_match else None,
        "year": int(year_match.group(1)) if year_match else None,
        "title": title,
    }


def parse_pdf(pdf_id: str, paper_id: str, pdf_path: Path) -> tuple[list[ParsedPage], dict[str, object]]:
    pages: list[ParsedPage] = []
    with fitz.open(pdf_path) as doc:
        for page_index, page in enumerate(doc, start=1):
            text = clean_text(page.get_text("text"))
            status = "low_text_page" if len(text) < 80 else "ok"
            pages.append(
                ParsedPage(
                    page_id=f"{pdf_id}_p{page_index:04d}",
                    paper_id=paper_id,
                    pdf_id=pdf_id,
                    page_number=page_index,
                    text=text,
                    char_count=len(text),
                    parse_status=status,
                )
            )

    text_pages = sum(1 for page in pages if page.char_count >= 80)
    blank_pages = sum(1 for page in pages if page.char_count == 0)
    low_text_pages = sum(1 for page in pages if 0 < page.char_count < 80)
    quality = text_pages / max(len(pages), 1)
    metadata_text = "\n".join(page.text for page in pages[:2])
    filename_title = title_from_filename(pdf_path)
    metadata = extract_initial_metadata(metadata_text, filename_title)
    metadata["year"] = metadata["year"] or year_from_filename(pdf_path)
    paper_type, is_review = classify_paper_type(str(metadata["title"]), pdf_path.name)
    metadata["paper_type"] = paper_type
    metadata["is_review"] = is_review
    metadata["page_count"] = len(pages)
    metadata["text_page_count"] = text_pages
    metadata["blank_page_count"] = blank_pages
    metadata["low_text_page_count"] = low_text_pages
    metadata["parse_quality_score"] = round(quality, 3)
    metadata["ocr_needed"] = quality < 0.5
    return pages, metadata


def parse_pending_pdfs(
    limit: int | None = None,
    db_path: Path | None = None,
    force: bool = False,
) -> dict[str, int]:
    output_path = PROJECT_ROOT / "data" / "parsed_pages" / "parsed_pages.jsonl"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stats = {
        "seen": 0,
        "parsed": 0,
        "skipped_duplicates": 0,
        "failed": 0,
        "pages": 0,
        "ocr_needed": 0,
        "low_text_pages": 0,
        "blank_pages": 0,
        "doi_found": 0,
    }

    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT pdf_id, paper_id, file_path, is_duplicate, parse_status
            FROM pdf_files
            WHERE (? = 1 OR parse_status IN ('pending', 'manifested', 'manifest_error', 'parse_error'))
            ORDER BY file_name
            """,
            (int(force),),
        ).fetchall()
        if limit is not None:
            rows = rows[:limit]

        with output_path.open("w", encoding="utf-8") as fh:
            for row in rows:
                stats["seen"] += 1
                if row["is_duplicate"]:
                    stats["skipped_duplicates"] += 1
                    conn.execute(
                        "UPDATE pdf_files SET parse_status = ? WHERE pdf_id = ?",
                        ("duplicate_skipped", row["pdf_id"]),
                    )
                    continue
                try:
                    pages, metadata = parse_pdf(row["pdf_id"], row["paper_id"], Path(row["file_path"]))
                    conn.executemany(
                        """
                        INSERT INTO parsed_pages (
                            page_id, paper_id, pdf_id, page_number, text, char_count, parse_status
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(pdf_id, page_number) DO UPDATE SET
                            text = excluded.text,
                            char_count = excluded.char_count,
                            parse_status = excluded.parse_status
                        """,
                        [
                            (
                                page.page_id,
                                page.paper_id,
                                page.pdf_id,
                                page.page_number,
                                page.text,
                                page.char_count,
                                page.parse_status,
                            )
                            for page in pages
                        ],
                    )
                    for page in pages:
                        fh.write(json.dumps(page.__dict__, ensure_ascii=False) + "\n")

                    conn.execute(
                        """
                        UPDATE pdf_files
                        SET parse_status = ?, page_count = ?, text_page_count = ?,
                            blank_page_count = ?, low_text_page_count = ?,
                            parse_quality_score = ?, ocr_needed = ?, error_message = NULL
                        WHERE pdf_id = ?
                        """,
                        (
                            "parsed",
                            metadata["page_count"],
                            metadata["text_page_count"],
                            metadata["blank_page_count"],
                            metadata["low_text_page_count"],
                            metadata["parse_quality_score"],
                            int(bool(metadata["ocr_needed"])),
                            row["pdf_id"],
                        ),
                    )
                    conn.execute(
                        """
                        UPDATE papers
                        SET doi = COALESCE(?, doi),
                            year = COALESCE(?, year),
                            title = COALESCE(?, title),
                            paper_type = COALESCE(?, paper_type),
                            is_review = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE paper_id = ?
                        """,
                        (
                            metadata["doi"],
                            metadata["year"],
                            metadata["title"],
                            metadata["paper_type"],
                            int(bool(metadata["is_review"])),
                            row["paper_id"],
                        ),
                    )
                    stats["parsed"] += 1
                    stats["pages"] += len(pages)
                    stats["ocr_needed"] += int(bool(metadata["ocr_needed"]))
                    stats["low_text_pages"] += int(metadata["low_text_page_count"])
                    stats["blank_pages"] += int(metadata["blank_page_count"])
                    stats["doi_found"] += int(bool(metadata["doi"]))
                except Exception as exc:  # pragma: no cover - depends on malformed PDFs
                    stats["failed"] += 1
                    conn.execute(
                        "UPDATE pdf_files SET parse_status = ?, error_message = ? WHERE pdf_id = ?",
                        ("parse_error", str(exc), row["pdf_id"]),
                    )
            conn.commit()

    record_pipeline_run("02_parse_pdfs", "ok", stats)
    return stats
