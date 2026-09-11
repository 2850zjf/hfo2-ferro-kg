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


CAPTION_RE = re.compile(r"(?im)^\s*((?:Fig\.?|Figure)\s*\d+[A-Za-z]?(?:\s*\([a-z]\))?)[\s.:;-]+")


def _asset_id(pdf_id: str, page_number: int, asset_type: str, asset_index: int) -> str:
    key = f"{pdf_id}:{page_number}:{asset_type}:{asset_index}"
    return f"vis_{uuid.uuid5(uuid.NAMESPACE_URL, key).hex[:16]}"


def _link_id(image_asset_id: str, caption_asset_id: str) -> str:
    return f"vlink_{uuid.uuid5(uuid.NAMESPACE_URL, f'{image_asset_id}:{caption_asset_id}').hex[:16]}"


def _caption_blocks(page: fitz.Page) -> list[dict[str, Any]]:
    captions: list[dict[str, Any]] = []
    for block in page.get_text("blocks", sort=True):
        x0, y0, x1, y1, text = block[:5]
        cleaned = " ".join(str(text or "").split())
        match = CAPTION_RE.search(str(text or ""))
        if not match or len(cleaned) < 20:
            continue
        captions.append(
            {
                "label": " ".join(match.group(1).split()),
                "text": cleaned[:1800],
                "bbox": fitz.Rect(x0, y0, x1, y1),
            }
        )
    return captions


def _image_placements(doc: fitz.Document, page: fitz.Page) -> dict[int, fitz.Rect]:
    placements: dict[int, fitz.Rect] = {}
    seen_xrefs: set[int] = set()
    for index, info in enumerate(page.get_images(full=True), start=1):
        xref = int(info[0])
        if xref in seen_xrefs:
            continue
        seen_xrefs.add(xref)
        try:
            extracted = doc.extract_image(xref)
            if int(extracted.get("width") or 0) < 120 or int(extracted.get("height") or 0) < 120:
                continue
            rects = page.get_image_rects(xref)
            if not rects:
                continue
            union = fitz.Rect(rects[0])
            for rect in rects[1:]:
                union |= fitz.Rect(rect)
            placements[index] = union
        except Exception:
            continue
    return placements


def caption_match_score(image_bbox: fitz.Rect, caption_bbox: fitz.Rect) -> tuple[float, float]:
    overlap = max(0.0, min(image_bbox.x1, caption_bbox.x1) - max(image_bbox.x0, caption_bbox.x0))
    overlap_ratio = overlap / max(1.0, min(image_bbox.width, caption_bbox.width))
    if caption_bbox.y0 >= image_bbox.y1:
        vertical_gap = caption_bbox.y0 - image_bbox.y1
    elif image_bbox.y0 >= caption_bbox.y1:
        vertical_gap = image_bbox.y0 - caption_bbox.y1 + 35.0
    else:
        vertical_gap = 0.0
    distance = vertical_gap + 90.0 * (1.0 - min(1.0, overlap_ratio))
    confidence = max(0.0, min(1.0, 1.0 - distance / 260.0))
    return distance, confidence


def link_visual_assets(
    limit_pdfs: int | None = None,
    reset_existing: bool = True,
    db_path: Path | None = None,
    progress_every: int = 50,
) -> dict[str, int]:
    stats = {
        "pdfs": 0, "pages": 0, "images_located": 0, "captions_located": 0,
        "links": 0, "unmatched_images": 0, "failed_pdfs": 0,
    }
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT pf.pdf_id, pf.paper_id, pf.file_path
            FROM pdf_files pf
            JOIN pdf_visual_assets va ON va.pdf_id = pf.pdf_id AND va.asset_type = 'image'
            WHERE pf.parse_status = 'parsed' AND COALESCE(pf.is_duplicate, 0) = 0
            ORDER BY pf.file_name
            """
        ).fetchall()
        if limit_pdfs is not None:
            rows = rows[:limit_pdfs]
        if reset_existing:
            conn.execute("DELETE FROM pdf_asset_links")
            conn.execute("DELETE FROM pdf_visual_assets WHERE asset_type = 'figure_caption_geometry'")
            conn.execute(
                "UPDATE pdf_visual_assets SET caption_text=NULL, caption_asset_id=NULL, bbox_json=NULL WHERE asset_type='image'"
            )
        for row in rows:
            stats["pdfs"] += 1
            try:
                with fitz.open(row["file_path"]) as doc:
                    for page_number in range(1, len(doc) + 1):
                        image_assets = conn.execute(
                            """
                            SELECT asset_id, asset_index FROM pdf_visual_assets
                            WHERE pdf_id=? AND page_number=? AND asset_type='image'
                            ORDER BY asset_index
                            """,
                            (row["pdf_id"], page_number),
                        ).fetchall()
                        if not image_assets:
                            continue
                        stats["pages"] += 1
                        page = doc[page_number - 1]
                        placements = _image_placements(doc, page)
                        captions = _caption_blocks(page)
                        stats["captions_located"] += len(captions)
                        caption_records: list[dict[str, Any]] = []
                        for index, caption in enumerate(captions, start=1):
                            asset_id = _asset_id(row["pdf_id"], page_number, "figure_caption_geometry", index)
                            caption_records.append({**caption, "asset_id": asset_id, "asset_index": index})
                            conn.execute(
                                """
                                INSERT INTO pdf_visual_assets (
                                    asset_id, paper_id, pdf_id, page_number, asset_type,
                                    asset_index, caption_text, bbox_json, extraction_status
                                ) VALUES (?, ?, ?, ?, 'figure_caption_geometry', ?, ?, ?, 'ok')
                                ON CONFLICT(pdf_id, page_number, asset_type, asset_index) DO UPDATE SET
                                    caption_text=excluded.caption_text, bbox_json=excluded.bbox_json,
                                    extraction_status='ok', error_message=NULL
                                """,
                                (
                                    asset_id, row["paper_id"], row["pdf_id"], page_number, index,
                                    caption["text"], json.dumps(list(caption["bbox"])),
                                ),
                            )
                        for image in image_assets:
                            bbox = placements.get(int(image["asset_index"]))
                            if bbox is None:
                                stats["unmatched_images"] += 1
                                continue
                            stats["images_located"] += 1
                            conn.execute(
                                "UPDATE pdf_visual_assets SET bbox_json=? WHERE asset_id=?",
                                (json.dumps(list(bbox)), image["asset_id"]),
                            )
                            if not caption_records:
                                stats["unmatched_images"] += 1
                                continue
                            ranked = sorted(
                                (
                                    (*caption_match_score(bbox, caption["bbox"]), caption)
                                    for caption in caption_records
                                ),
                                key=lambda item: item[0],
                            )
                            distance, confidence, caption = ranked[0]
                            if confidence < 0.25:
                                stats["unmatched_images"] += 1
                                continue
                            conn.execute(
                                """
                                UPDATE pdf_visual_assets
                                SET caption_text=?, caption_asset_id=?
                                WHERE asset_id=?
                                """,
                                (caption["text"], caption["asset_id"], image["asset_id"]),
                            )
                            conn.execute(
                                """
                                INSERT INTO pdf_asset_links (
                                    link_id, paper_id, pdf_id, page_number, image_asset_id,
                                    caption_asset_id, geometry_distance, confidence
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                ON CONFLICT(image_asset_id, caption_asset_id) DO UPDATE SET
                                    geometry_distance=excluded.geometry_distance,
                                    confidence=excluded.confidence
                                """,
                                (
                                    _link_id(image["asset_id"], caption["asset_id"]), row["paper_id"],
                                    row["pdf_id"], page_number, image["asset_id"], caption["asset_id"],
                                    distance, confidence,
                                ),
                            )
                            stats["links"] += 1
            except Exception:
                stats["failed_pdfs"] += 1
            conn.commit()
            if progress_every and stats["pdfs"] % progress_every == 0:
                print(
                    "visual links processed={pdfs} links={links} unmatched={unmatched_images} failed={failed_pdfs}".format(**stats),
                    flush=True,
                )
    record_pipeline_run("58_link_visual_assets", "ok", stats, db_path=db_path)
    return stats
