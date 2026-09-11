from __future__ import annotations

import csv
import hashlib
import re
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.pipeline_log import record_pipeline_run


HAFNIA_RE = re.compile(r"\b(?:HfO2|HfO₂|HZO|Hf\s*[0-9.]*\s*Zr\s*[0-9.]*\s*O2|hafnia)\b", re.IGNORECASE)
PROPERTY_RE = re.compile(
    r"\b(?:polarization|2\s*P\s*r|P\s*r|coercive|endurance|retention|fatigue|wake[- ]?up|"
    r"leakage|memory window|breakdown|switching|dielectric|phase fraction)\b",
    re.IGNORECASE,
)
PROCESS_RE = re.compile(
    r"\b(?:ALD|PLD|sputter(?:ing)?|anneal(?:ing)?|RTA|PMA|PDA|electrode|substrate|interface|strain)\b",
    re.IGNORECASE,
)
COMPUTATION_RE = re.compile(
    r"\b(?:DFT|first[- ]principles|DFPT|NEB|molecular dynamics|phase[- ]field|Landau|energy barrier|"
    r"formation energy|migration barrier|free energy)\b",
    re.IGNORECASE,
)
FIGURE_LABEL_RE = re.compile(r"\b(?:Fig\.?|Figure)\s*\d+[A-Za-z]?", re.IGNORECASE)


def _queue_id(source_type: str, source_id: str) -> str:
    return f"mmq_{uuid.uuid5(uuid.NAMESPACE_URL, f'{source_type}:{source_id}').hex[:16]}"


def _priority(source_type: str, context: str, has_caption: bool = False) -> tuple[float, str, str]:
    score = {"figure": 0.5, "table": 1.5, "equation": 2.0}[source_type]
    if HAFNIA_RE.search(context):
        score += 4.0
    if PROPERTY_RE.search(context):
        score += 2.5
    if PROCESS_RE.search(context):
        score += 1.5
    if COMPUTATION_RE.search(context):
        score += 2.0
    if source_type == "figure" and has_caption:
        score += 1.0
    if source_type == "equation" and len(context.split()) < 80:
        score += 0.5

    tier = "P0" if score >= 7 else "P1" if score >= 4 else "P2"
    status = "queued" if score >= 4 else "retained_unselected"
    return round(score, 2), tier, status


def table_asset_quality(row_count: int, col_count: int, table_text: str) -> tuple[float, str]:
    text = " ".join(str(table_text or "").split())
    if row_count < 2 or col_count < 2:
        return 0.05, "degenerate_dimensions"
    if len(text) < 30:
        return 0.15, "too_little_content"
    if re.match(r"^(?:Fig(?:ure)?\.?|Journal of|ARTICLE|ABSTRACT:)", text, re.IGNORECASE) and col_count <= 2:
        return 0.1, "layout_fragment_not_table"
    numeric_tokens = len(re.findall(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?", text))
    score = 0.45
    if row_count >= 3:
        score += 0.15
    if col_count >= 3:
        score += 0.15
    if numeric_tokens >= 3:
        score += 0.2
    if row_count * col_count >= 12:
        score += 0.05
    return min(1.0, score), "structured_table"


def _file_hash(path: str | None) -> str | None:
    if not path:
        return None
    target = Path(path)
    if not target.is_file():
        return None
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_local_asset_path(path: str | None) -> str | None:
    """Resolve historical Windows/macOS asset paths against the current project root."""
    if not path:
        return None
    target = Path(path)
    if target.is_file():
        return str(target.resolve())
    normalized = str(path).replace("\\", "/")
    for marker in ("/data/figures/", "/data/equations/"):
        if marker in normalized:
            relative = normalized.split(marker, 1)[1]
            candidate = PROJECT_ROOT / marker.strip("/") / relative
            if candidate.is_file():
                return str(candidate.resolve())
    return str(path)


def _source_rows(conn) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    figure_rows = conn.execute(
        """
        SELECT va.asset_id AS source_id, va.paper_id, va.pdf_id, va.page_number,
               va.file_path, va.width, va.height,
               COALESCE(va.caption_text, '') AS caption_text,
               COALESCE(pp.text, '') AS page_text
        FROM pdf_visual_assets va
        LEFT JOIN parsed_pages pp ON pp.pdf_id = va.pdf_id AND pp.page_number = va.page_number
        WHERE va.asset_type = 'image' AND va.extraction_status = 'ok'
        """
    ).fetchall()
    for row in figure_rows:
        caption = str(row["caption_text"] or "")
        page_text = str(row["page_text"] or "")
        context = f"CAPTION:\n{caption}\n\nPAGE_CONTEXT:\n{page_text}"[:8000]
        score, tier, status = _priority("figure", context, has_caption=bool(caption.strip()))
        width = int(row["width"] or 0)
        height = int(row["height"] or 0)
        asset_quality = 0.85 if min(width, height) >= 200 else 0.55
        path = resolve_local_asset_path(str(row["file_path"] or "") or None)
        if not path or not Path(path).is_file():
            status = "missing_asset"
        records.append(
            {
                "source_type": "figure",
                "source_id": row["source_id"],
                "paper_id": row["paper_id"],
                "pdf_id": row["pdf_id"],
                "page_number": row["page_number"],
                "file_path": path,
                "context_text": context,
                "asset_quality_score": asset_quality,
                "quality_reason": "image_with_geometry_caption" if caption.strip() else "image_without_matched_caption",
                "priority_score": score,
                "priority_tier": tier,
                "queue_status": status,
            }
        )

    table_rows = conn.execute(
        """
        SELECT pt.table_id AS source_id, pt.paper_id, pt.pdf_id, pt.page_number,
               pt.table_text, pt.row_count, pt.col_count, COALESCE(pp.text, '') AS page_text
        FROM pdf_tables pt
        LEFT JOIN parsed_pages pp ON pp.pdf_id = pt.pdf_id AND pp.page_number = pt.page_number
        WHERE pt.extraction_status = 'ok'
        """
    ).fetchall()
    for row in table_rows:
        context = f"STRUCTURED_TABLE:\n{row['table_text']}\n\nPAGE_CONTEXT:\n{row['page_text']}"[:12000]
        score, tier, status = _priority("table", context)
        asset_quality, quality_reason = table_asset_quality(
            int(row["row_count"] or 0), int(row["col_count"] or 0), str(row["table_text"] or "")
        )
        if asset_quality < 0.35:
            status = "retained_unselected"
            tier = "P2"
        records.append(
            {
                "source_type": "table",
                "source_id": row["source_id"],
                "paper_id": row["paper_id"],
                "pdf_id": row["pdf_id"],
                "page_number": row["page_number"],
                "file_path": None,
                "context_text": context,
                "asset_quality_score": asset_quality,
                "quality_reason": quality_reason,
                "priority_score": score,
                "priority_tier": tier,
                "queue_status": status,
            }
        )

    equation_rows = conn.execute(
        """
        SELECT pe.equation_id AS source_id, pe.paper_id, pe.pdf_id, pe.page_number,
               pe.image_path, pe.raw_text, pe.candidate_kind, pe.confidence,
               COALESCE(pp.text, '') AS page_text
        FROM pdf_equations pe
        LEFT JOIN parsed_pages pp ON pp.pdf_id = pe.pdf_id AND pp.page_number = pe.page_number
        WHERE pe.extraction_status IN ('candidate', 'validated')
        """
    ).fetchall()
    for row in equation_rows:
        context = (
            f"EQUATION_TEXT_CANDIDATE:\n{row['raw_text']}\n"
            f"CANDIDATE_KIND: {row['candidate_kind']}\n\nPAGE_CONTEXT:\n{row['page_text']}"
        )[:8000]
        score, tier, status = _priority("equation", context)
        asset_quality = max(0.1, min(1.0, float(row["confidence"] or 0.5)))
        path = resolve_local_asset_path(str(row["image_path"] or "") or None)
        if not path or not Path(path).is_file():
            status = "missing_asset"
        records.append(
            {
                "source_type": "equation",
                "source_id": row["source_id"],
                "paper_id": row["paper_id"],
                "pdf_id": row["pdf_id"],
                "page_number": row["page_number"],
                "file_path": path,
                "context_text": context,
                "asset_quality_score": asset_quality,
                "quality_reason": "high_recall_formula_candidate",
                "priority_score": score,
                "priority_tier": tier,
                "queue_status": status,
            }
        )
    return records


def build_multimodal_queue(
    output_dir: Path | None = None,
    compute_hashes: bool = False,
    db_path: Path | None = None,
) -> dict[str, Any]:
    target_dir = output_dir or PROJECT_ROOT / "data" / "extraction_queues" / "multimodal_v23"
    target_dir.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        rows = _source_rows(conn)
        seen_hashes: dict[str, str] = {}
        for row in rows:
            content_hash = _file_hash(row["file_path"]) if compute_hashes else None
            duplicate_of = None
            if content_hash:
                duplicate_of = seen_hashes.get(content_hash)
                seen_hashes.setdefault(content_hash, str(row["source_id"]))
            if duplicate_of:
                row["queue_status"] = "duplicate_asset"
            row["content_hash"] = content_hash
            row["duplicate_of_source_id"] = duplicate_of
            if row["file_path"] and Path(row["file_path"]).is_file():
                if row["source_type"] == "figure":
                    conn.execute(
                        "UPDATE pdf_visual_assets SET file_path = ? WHERE asset_id = ?",
                        (row["file_path"], row["source_id"]),
                    )
                elif row["source_type"] == "equation":
                    conn.execute(
                        "UPDATE pdf_equations SET image_path = ? WHERE equation_id = ?",
                        (row["file_path"], row["source_id"]),
                    )
            conn.execute(
                """
                INSERT INTO multimodal_asset_queue (
                    queue_id, source_type, source_id, paper_id, pdf_id, page_number,
                    file_path, context_text, priority_score, priority_tier, queue_status,
                    asset_quality_score, quality_reason, content_hash,
                    duplicate_of_source_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    paper_id=excluded.paper_id, pdf_id=excluded.pdf_id,
                    page_number=excluded.page_number, file_path=excluded.file_path,
                    context_text=excluded.context_text, priority_score=excluded.priority_score,
                    asset_quality_score=excluded.asset_quality_score,
                    quality_reason=excluded.quality_reason,
                    priority_tier=excluded.priority_tier, queue_status=excluded.queue_status,
                    content_hash=excluded.content_hash,
                    duplicate_of_source_id=excluded.duplicate_of_source_id,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    _queue_id(row["source_type"], row["source_id"]),
                    row["source_type"], row["source_id"], row["paper_id"], row["pdf_id"],
                    row["page_number"], row["file_path"], row["context_text"],
                    row["priority_score"], row["priority_tier"], row["queue_status"],
                    row["asset_quality_score"], row["quality_reason"],
                    content_hash, duplicate_of,
                ),
            )
        conn.commit()

    csv_path = target_dir / "multimodal_asset_queue.csv"
    fields = [
        "source_type", "source_id", "paper_id", "pdf_id", "page_number", "file_path",
        "asset_quality_score", "quality_reason", "priority_score", "priority_tier",
        "queue_status", "content_hash", "duplicate_of_source_id",
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: row.get(key) for key in fields} for row in rows)

    stats: dict[str, Any] = {
        "assets": len(rows),
        "source_counts": dict(Counter(str(row["source_type"]) for row in rows)),
        "tier_counts": dict(Counter(str(row["priority_tier"]) for row in rows)),
        "status_counts": dict(Counter(str(row["queue_status"]) for row in rows)),
        "hashes_computed": bool(compute_hashes),
        "queue_path": str(csv_path),
    }
    record_pipeline_run("56_build_multimodal_queue", "ok", stats, db_path=db_path)
    return stats
