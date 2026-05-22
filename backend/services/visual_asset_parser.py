from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

import fitz

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.pipeline_log import record_pipeline_run


FIGURE_CAPTION_RE = re.compile(
    r"(?:(?:Fig\.?|Figure)\s*\d+[A-Za-z]?(?:\s*\([a-z]\))?[\s.:;-]+.{40,900}?)(?=\n\s*(?:Fig\.?|Figure|Table|References|Acknowledg|Supplementary|\d+\s+[A-Z])|\Z)",
    re.IGNORECASE | re.DOTALL,
)


def _clean_caption(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _asset_id(pdf_id: str, page_number: int, asset_type: str, asset_index: int) -> str:
    return f"vis_{uuid.uuid5(uuid.NAMESPACE_URL, f'{pdf_id}:{page_number}:{asset_type}:{asset_index}').hex[:16]}"


def _extract_caption_assets(paper_id: str, pdf_id: str, page_number: int, text: str) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    for index, match in enumerate(FIGURE_CAPTION_RE.finditer(text), start=1):
        caption = _clean_caption(match.group(0))
        if len(caption) < 40:
            continue
        assets.append(
            {
                "asset_id": _asset_id(pdf_id, page_number, "figure_caption", index),
                "paper_id": paper_id,
                "pdf_id": pdf_id,
                "page_number": page_number,
                "asset_type": "figure_caption",
                "asset_index": index,
                "file_path": None,
                "caption_text": caption[:1200],
                "bbox_json": None,
                "width": None,
                "height": None,
                "extraction_status": "ok",
                "error_message": None,
            }
        )
    return assets


def _extract_page_images(doc: fitz.Document, pdf_id: str, paper_id: str, page_number: int, output_dir: Path) -> list[dict[str, Any]]:
    page = doc[page_number - 1]
    assets: list[dict[str, Any]] = []
    seen_xrefs: set[int] = set()
    for index, image_info in enumerate(page.get_images(full=True), start=1):
        xref = int(image_info[0])
        if xref in seen_xrefs:
            continue
        seen_xrefs.add(xref)
        try:
            extracted = doc.extract_image(xref)
            width = int(extracted.get("width") or 0)
            height = int(extracted.get("height") or 0)
            if width < 120 or height < 120:
                continue
            ext = str(extracted.get("ext") or "png")
            image_dir = output_dir / pdf_id
            image_dir.mkdir(parents=True, exist_ok=True)
            image_path = image_dir / f"p{page_number:04d}_img{index:03d}.{ext}"
            image_path.write_bytes(extracted["image"])
            assets.append(
                {
                    "asset_id": _asset_id(pdf_id, page_number, "image", index),
                    "paper_id": paper_id,
                    "pdf_id": pdf_id,
                    "page_number": page_number,
                    "asset_type": "image",
                    "asset_index": index,
                    "file_path": str(image_path),
                    "caption_text": None,
                    "bbox_json": None,
                    "width": width,
                    "height": height,
                    "extraction_status": "ok",
                    "error_message": None,
                }
            )
        except Exception as exc:
            assets.append(
                {
                    "asset_id": _asset_id(pdf_id, page_number, "image_error", index),
                    "paper_id": paper_id,
                    "pdf_id": pdf_id,
                    "page_number": page_number,
                    "asset_type": "image_error",
                    "asset_index": index,
                    "file_path": None,
                    "caption_text": None,
                    "bbox_json": None,
                    "width": None,
                    "height": None,
                    "extraction_status": "error",
                    "error_message": str(exc)[:500],
                }
            )
    return assets


def extract_visual_assets(
    limit_pdfs: int | None = None,
    db_path: Path | None = None,
    reset_existing: bool = False,
    save_images: bool = True,
) -> dict[str, int]:
    output_dir = PROJECT_ROOT / "data" / "figures"
    output_dir.mkdir(parents=True, exist_ok=True)
    stats = {
        "pdfs": 0,
        "pages": 0,
        "figure_captions": 0,
        "images": 0,
        "image_errors": 0,
        "failed_pdfs": 0,
    }

    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pdf_visual_assets (
                asset_id TEXT PRIMARY KEY,
                paper_id TEXT,
                pdf_id TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                asset_type TEXT NOT NULL,
                asset_index INTEGER NOT NULL,
                file_path TEXT,
                caption_text TEXT,
                bbox_json TEXT,
                width INTEGER,
                height INTEGER,
                extraction_status TEXT DEFAULT 'ok',
                error_message TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(pdf_id, page_number, asset_type, asset_index)
            )
            """
        )
        rows = conn.execute(
            """
            SELECT pdf_id, paper_id, file_path
            FROM pdf_files
            WHERE parse_status = 'parsed'
              AND is_duplicate = 0
              AND (? = 1 OR NOT EXISTS (
                  SELECT 1 FROM pdf_visual_assets va WHERE va.pdf_id = pdf_files.pdf_id
              ))
            ORDER BY file_name
            """,
            (int(reset_existing),),
        ).fetchall()
        if limit_pdfs is not None:
            rows = rows[:limit_pdfs]
        if reset_existing:
            conn.execute("DELETE FROM pdf_visual_assets")

        for row in rows:
            stats["pdfs"] += 1
            if save_images:
                try:
                    doc = fitz.open(row["file_path"])
                except Exception as exc:
                    stats["failed_pdfs"] += 1
                    conn.execute(
                        """
                        INSERT INTO pdf_visual_assets (
                            asset_id, paper_id, pdf_id, page_number, asset_type,
                            asset_index, extraction_status, error_message
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            _asset_id(row["pdf_id"], 0, "pdf_error", 0),
                            row["paper_id"],
                            row["pdf_id"],
                            0,
                            "pdf_error",
                            0,
                            "error",
                            str(exc)[:500],
                        ),
                    )
                    continue
            else:
                doc = None
            pages = conn.execute(
                """
                SELECT page_number, text
                FROM parsed_pages
                WHERE pdf_id = ?
                ORDER BY page_number
                """,
                (row["pdf_id"],),
            ).fetchall()
            for page in pages:
                stats["pages"] += 1
                assets = _extract_caption_assets(
                    row["paper_id"],
                    row["pdf_id"],
                    page["page_number"],
                    page["text"],
                )
                if doc is not None:
                    assets.extend(
                        _extract_page_images(
                            doc,
                            row["pdf_id"],
                            row["paper_id"],
                            page["page_number"],
                            output_dir,
                        )
                    )
                stats["figure_captions"] += sum(1 for item in assets if item["asset_type"] == "figure_caption")
                stats["images"] += sum(1 for item in assets if item["asset_type"] == "image")
                stats["image_errors"] += sum(1 for item in assets if item["asset_type"] == "image_error")
                if assets:
                    conn.executemany(
                        """
                        INSERT INTO pdf_visual_assets (
                            asset_id, paper_id, pdf_id, page_number, asset_type,
                            asset_index, file_path, caption_text, bbox_json, width,
                            height, extraction_status, error_message
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(pdf_id, page_number, asset_type, asset_index) DO UPDATE SET
                            file_path = excluded.file_path,
                            caption_text = excluded.caption_text,
                            bbox_json = excluded.bbox_json,
                            width = excluded.width,
                            height = excluded.height,
                            extraction_status = excluded.extraction_status,
                            error_message = excluded.error_message
                        """,
                        [
                            (
                                item["asset_id"],
                                item["paper_id"],
                                item["pdf_id"],
                                item["page_number"],
                                item["asset_type"],
                                item["asset_index"],
                                item["file_path"],
                                item["caption_text"],
                                json.dumps(item["bbox_json"], ensure_ascii=False) if item["bbox_json"] else None,
                                item["width"],
                                item["height"],
                                item["extraction_status"],
                                item["error_message"],
                            )
                            for item in assets
                        ],
                    )
            if doc is not None:
                doc.close()
            conn.commit()

    record_pipeline_run("15_extract_visual_assets", "ok", stats, db_path=db_path)
    return stats

