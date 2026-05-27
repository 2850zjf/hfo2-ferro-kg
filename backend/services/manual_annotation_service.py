from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from backend.db.session import connect
from backend.services.sample_linker import init_sample_link_tables


ANNOTATION_STATUSES = {
    "unchecked",
    "correct",
    "fixed",
    "uncertain",
    "reject",
}


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _load_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _table_exists(conn: Any, table_name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
    except Exception:
        return False
    return row is not None


def init_manual_annotation_tables(db_path: Path | None = None) -> None:
    init_sample_link_tables(db_path=db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS manual_annotations (
                annotation_id TEXT PRIMARY KEY,
                link_id TEXT NOT NULL,
                paper_id TEXT,
                pdf_id TEXT,
                page_number INTEGER,
                annotation_status TEXT NOT NULL DEFAULT 'unchecked',
                corrected_material_json TEXT NOT NULL,
                corrected_sample_json TEXT NOT NULL,
                corrected_phase_json TEXT NOT NULL,
                corrected_property_json TEXT NOT NULL,
                corrected_evidence_text TEXT,
                corrected_context_quality TEXT,
                reviewer_notes TEXT,
                applied_to_source INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(link_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_manual_annotations_link ON manual_annotations(link_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_manual_annotations_status ON manual_annotations(annotation_status)"
        )
        conn.commit()


def _row_to_item(row: Any) -> dict[str, Any]:
    material = _load_json(row["material_json"])
    sample = _load_json(row["sample_json"])
    phase = _load_json(row["phase_json"])
    prop = _load_json(row["property_json"])
    audit_flags = []
    try:
        audit_flags = json.loads(row["risk_flags_json"] or "[]")
    except Exception:
        audit_flags = []
    return {
        "link_id": row["link_id"],
        "source_kind": row["source_kind"],
        "source_id": row["source_id"],
        "paper_id": row["paper_id"],
        "pdf_id": row["pdf_id"],
        "chunk_id": row["chunk_id"],
        "page_number": row["page_number"],
        "sample_id": row["sample_id"],
        "paper_title": row["paper_title"],
        "doi": row["doi"],
        "year": row["year"],
        "pdf_file_name": row["pdf_file_name"],
        "pdf_path": row["pdf_path"],
        "source_text": row["source_text"] or "",
        "material": material,
        "sample": sample,
        "phase": phase,
        "property": prop,
        "evidence_text": row["evidence_text"] or prop.get("evidence_text") or "",
        "context_quality": row["context_quality"],
        "context_score": row["context_score"],
        "link_status": row["link_status"],
        "ai_review_status": row["ai_review_status"] or "",
        "ai_risk_flags": audit_flags if isinstance(audit_flags, list) else [],
        "ai_repair_suggestion": row["repair_suggestion"] or "",
        "manual_status": row["annotation_status"] or "unchecked",
        "manual_notes": row["reviewer_notes"] or "",
        "applied_to_source": bool(row["applied_to_source"] or 0),
    }


def list_annotation_items(
    *,
    annotation_status: str = "all",
    property_name: str = "all",
    context_quality: str = "all",
    query: str = "",
    limit: int = 500,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    init_manual_annotation_tables(db_path=db_path)
    clauses = ["spl.status = 'linked'"]
    params: list[Any] = []
    if context_quality != "all":
        clauses.append("spl.context_quality = ?")
        params.append(context_quality)
    if property_name != "all":
        clauses.append("spl.property_json LIKE ?")
        params.append(f"%{property_name}%")
    where = "WHERE " + " AND ".join(clauses)
    params.append(limit)
    with connect(db_path) as conn:
        has_ai_audits = _table_exists(conn, "ai_fact_audits")
        ai_select = (
            "afa.ai_review_status, afa.risk_flags_json, afa.repair_suggestion"
            if has_ai_audits
            else "NULL AS ai_review_status, NULL AS risk_flags_json, NULL AS repair_suggestion"
        )
        ai_join = "LEFT JOIN ai_fact_audits afa ON afa.link_id = spl.link_id" if has_ai_audits else ""
        rows = conn.execute(
            f"""
            SELECT spl.link_id, spl.source_kind, spl.source_id, spl.paper_id,
                   spl.pdf_id, spl.chunk_id, spl.page_number, spl.sample_id,
                   spl.material_json, spl.sample_json, spl.phase_json,
                   spl.property_json, spl.evidence_text, spl.context_quality,
                   spl.context_score, spl.status AS link_status,
                   p.title AS paper_title, p.doi, p.year,
                   pf.file_name AS pdf_file_name, pf.file_path AS pdf_path,
                   dc.text AS source_text,
                   {ai_select},
                   ma.annotation_status, ma.reviewer_notes, ma.applied_to_source
            FROM sample_property_links spl
            LEFT JOIN papers p ON p.paper_id = spl.paper_id
            LEFT JOIN pdf_files pf ON pf.pdf_id = spl.pdf_id
            LEFT JOIN document_chunks dc ON dc.chunk_id = spl.chunk_id
            {ai_join}
            LEFT JOIN manual_annotations ma ON ma.link_id = spl.link_id
            {where}
            ORDER BY
                CASE COALESCE(ma.annotation_status, 'unchecked')
                    WHEN 'unchecked' THEN 0
                    WHEN 'uncertain' THEN 1
                    WHEN 'fixed' THEN 2
                    WHEN 'correct' THEN 3
                    WHEN 'reject' THEN 4
                    ELSE 5
                END,
                spl.rowid DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    items = [_row_to_item(row) for row in rows]
    if annotation_status != "all":
        items = [item for item in items if item["manual_status"] == annotation_status]
    if property_name != "all":
        items = [
            item
            for item in items
            if (item.get("property") or {}).get("property_name") == property_name
        ]
    if query.strip():
        needle = query.strip().lower()
        items = [
            item
            for item in items
            if needle
            in " ".join(
                [
                    str(item.get("link_id") or ""),
                    str(item.get("paper_title") or ""),
                    str(item.get("doi") or ""),
                    str((item.get("material") or {}).get("canonical_name") or ""),
                    str((item.get("material") or {}).get("material_family") or ""),
                    str((item.get("property") or {}).get("property_name") or ""),
                    str(item.get("evidence_text") or ""),
                ]
            ).lower()
        ]
    return items


def get_annotation_item(link_id: str, db_path: Path | None = None) -> dict[str, Any] | None:
    items = list_annotation_items(limit=100000, db_path=db_path)
    for item in items:
        if item["link_id"] == link_id:
            annotation = load_annotation(link_id, db_path=db_path)
            if annotation:
                item["material"] = annotation["corrected_material"]
                item["sample"] = annotation["corrected_sample"]
                item["phase"] = annotation["corrected_phase"]
                item["property"] = annotation["corrected_property"]
                item["evidence_text"] = annotation["corrected_evidence_text"]
                item["context_quality"] = annotation["corrected_context_quality"] or item["context_quality"]
            return item
    return None


def load_annotation(link_id: str, db_path: Path | None = None) -> dict[str, Any] | None:
    init_manual_annotation_tables(db_path=db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM manual_annotations WHERE link_id = ?",
            (link_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "annotation_id": row["annotation_id"],
        "link_id": row["link_id"],
        "annotation_status": row["annotation_status"],
        "corrected_material": _load_json(row["corrected_material_json"]),
        "corrected_sample": _load_json(row["corrected_sample_json"]),
        "corrected_phase": _load_json(row["corrected_phase_json"]),
        "corrected_property": _load_json(row["corrected_property_json"]),
        "corrected_evidence_text": row["corrected_evidence_text"] or "",
        "corrected_context_quality": row["corrected_context_quality"] or "",
        "reviewer_notes": row["reviewer_notes"] or "",
        "applied_to_source": bool(row["applied_to_source"] or 0),
    }


def save_annotation(
    *,
    link_id: str,
    annotation_status: str,
    material: dict[str, Any],
    sample: dict[str, Any],
    phase: dict[str, Any],
    prop: dict[str, Any],
    evidence_text: str,
    context_quality: str,
    reviewer_notes: str,
    apply_to_source: bool = False,
    db_path: Path | None = None,
) -> dict[str, Any]:
    if annotation_status not in ANNOTATION_STATUSES:
        raise ValueError(f"Unsupported annotation status: {annotation_status}")
    base = get_annotation_item(link_id, db_path=db_path)
    if base is None:
        raise ValueError(f"Unknown link_id: {link_id}")
    annotation_id = f"ann_{uuid.uuid5(uuid.NAMESPACE_URL, link_id).hex[:16]}"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO manual_annotations (
                annotation_id, link_id, paper_id, pdf_id, page_number,
                annotation_status, corrected_material_json, corrected_sample_json,
                corrected_phase_json, corrected_property_json, corrected_evidence_text,
                corrected_context_quality, reviewer_notes, applied_to_source, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(link_id) DO UPDATE SET
                annotation_status = excluded.annotation_status,
                corrected_material_json = excluded.corrected_material_json,
                corrected_sample_json = excluded.corrected_sample_json,
                corrected_phase_json = excluded.corrected_phase_json,
                corrected_property_json = excluded.corrected_property_json,
                corrected_evidence_text = excluded.corrected_evidence_text,
                corrected_context_quality = excluded.corrected_context_quality,
                reviewer_notes = excluded.reviewer_notes,
                applied_to_source = excluded.applied_to_source,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                annotation_id,
                link_id,
                base.get("paper_id"),
                base.get("pdf_id"),
                base.get("page_number"),
                annotation_status,
                _json(material),
                _json(sample),
                _json(phase),
                _json(prop),
                evidence_text,
                context_quality,
                reviewer_notes,
                1 if apply_to_source else 0,
            ),
        )
        if apply_to_source:
            conn.execute(
                """
                UPDATE sample_property_links
                SET material_json = ?,
                    sample_json = ?,
                    phase_json = ?,
                    property_json = ?,
                    evidence_text = ?,
                    context_quality = ?,
                    linkage_method = linkage_method || '+manual',
                    status = CASE WHEN ? = 'reject' THEN 'manual_rejected' ELSE 'linked' END
                WHERE link_id = ?
                """,
                (
                    _json(material),
                    _json(sample),
                    _json(phase),
                    _json(prop),
                    evidence_text,
                    context_quality,
                    annotation_status,
                    link_id,
                ),
            )
        conn.commit()
    return load_annotation(link_id, db_path=db_path) or {}


def annotation_counts(db_path: Path | None = None) -> dict[str, int]:
    init_manual_annotation_tables(db_path=db_path)
    counts = {status: 0 for status in sorted(ANNOTATION_STATUSES)}
    with connect(db_path) as conn:
        total_linked = int(
            conn.execute(
                "SELECT COUNT(*) FROM sample_property_links WHERE status = 'linked'"
            ).fetchone()[0]
        )
        rows = conn.execute(
            "SELECT annotation_status, COUNT(*) AS n FROM manual_annotations GROUP BY annotation_status"
        ).fetchall()
    for row in rows:
        counts[row["annotation_status"]] = int(row["n"])
    annotated = sum(value for key, value in counts.items() if key != "unchecked")
    counts["unchecked"] = max(0, total_linked - annotated - counts.get("unchecked", 0)) + counts.get("unchecked", 0)
    counts["total_linked"] = total_linked
    return counts
