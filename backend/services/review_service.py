from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from backend.core.config import PROJECT_ROOT, get_settings
from backend.db.session import connect


REVIEW_STATUSES = {"pending", "preapproved_machine", "needs_human_review", "approved", "rejected"}


def _fact_record(row: Any) -> dict[str, Any]:
    payload = json.loads(row["payload_json"])
    prop = payload.get("property") or {}
    material = payload.get("material") or {}
    sample = payload.get("sample") or {}
    preaudit = payload.get("preaudit") or {}
    return {
        "fact_id": row["fact_id"],
        "review_status": row["review_status"],
        "paper_id": row["paper_id"],
        "pdf_id": row["pdf_id"],
        "chunk_id": row["chunk_id"],
        "page_number": row["page_number"],
        "material": material.get("canonical_name") or material.get("raw_name"),
        "material_family": material.get("material_family"),
        "device_stack": sample.get("device_stack"),
        "property_name": prop.get("property_name"),
        "raw_property_name": prop.get("raw_property_name"),
        "value": prop.get("normalized_value", prop.get("value")),
        "unit": prop.get("normalized_unit") or prop.get("unit"),
        "confidence": prop.get("confidence") or preaudit.get("confidence"),
        "evidence_text": prop.get("evidence_text"),
        "reviewer_notes": row["reviewer_notes"],
        "paper_title": row["paper_title"],
        "doi": row["doi"],
        "year": row["year"],
        "pdf_file_name": row["pdf_file_name"],
        "pdf_path": row["pdf_path"],
    }


def list_review_facts(
    status: str | None = None,
    property_name: str | None = None,
    limit: int = 500,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if status and status != "all":
        clauses.append("review_status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT rf.fact_id, rf.review_status, rf.paper_id, rf.pdf_id, rf.chunk_id,
                   rf.page_number, rf.payload_json, rf.reviewer_notes, rf.created_at,
                   p.title AS paper_title, p.doi, p.year,
                   pf.file_name AS pdf_file_name, pf.file_path AS pdf_path
            FROM reviewed_facts
            rf
            LEFT JOIN papers p ON p.paper_id = rf.paper_id
            LEFT JOIN pdf_files pf ON pf.pdf_id = rf.pdf_id
            {where}
            ORDER BY rf.created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    facts = [_fact_record(row) for row in rows]
    if property_name and property_name != "all":
        facts = [fact for fact in facts if fact["property_name"] == property_name]
    return facts


def pdf_file_url(pdf_path: str | None, page_number: int | None = None) -> str | None:
    if not pdf_path:
        return None
    path = Path(pdf_path)
    if not path.exists():
        return None
    url = path.resolve().as_uri()
    if page_number:
        url = f"{url}#page={quote(str(page_number))}"
    return url


def pdf_viewer_url(
    pdf_id: str | None,
    page_number: int | None = None,
    fact_id: str | None = None,
) -> str | None:
    if not pdf_id:
        return None
    params: dict[str, str] = {"pdf_id": pdf_id}
    if page_number:
        params["page"] = str(page_number)
    if fact_id:
        params["fact_id"] = fact_id
    return f"/PDF_原文预览?{urlencode(params)}"


def get_pdf_viewer_record(pdf_id: str, db_path: Path | None = None) -> dict[str, Any] | None:
    settings = get_settings()
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT pf.pdf_id, pf.file_name, pf.file_path, pf.page_count,
                   p.paper_id, p.title, p.doi, p.year
            FROM pdf_files pf
            LEFT JOIN papers p ON p.paper_id = pf.paper_id
            WHERE pf.pdf_id = ?
            """,
            (pdf_id,),
        ).fetchone()
    if row is None:
        return None

    pdf_path = Path(row["file_path"]).resolve()
    pdf_root = settings.pdf_root.resolve()
    try:
        is_safe_path = pdf_path.is_relative_to(pdf_root)
    except AttributeError:  # pragma: no cover - Python < 3.9 compatibility
        is_safe_path = str(pdf_path).startswith(str(pdf_root))
    return {
        "pdf_id": row["pdf_id"],
        "file_name": row["file_name"],
        "file_path": str(pdf_path),
        "page_count": row["page_count"],
        "paper_id": row["paper_id"],
        "paper_title": row["title"],
        "doi": row["doi"],
        "year": row["year"],
        "exists": pdf_path.exists(),
        "is_safe_path": is_safe_path,
    }


def update_review_status(
    fact_id: str,
    status: str,
    reviewer_notes: str | None = None,
    db_path: Path | None = None,
) -> None:
    if status not in REVIEW_STATUSES:
        raise ValueError(f"Unsupported review status: {status}")
    with connect(db_path) as conn:
        updated = conn.execute(
            """
            UPDATE reviewed_facts
            SET review_status = ?,
                reviewer_notes = COALESCE(?, reviewer_notes),
                updated_at = CURRENT_TIMESTAMP
            WHERE fact_id = ?
            """,
            (status, reviewer_notes, fact_id),
        ).rowcount
        conn.commit()
    if updated == 0:
        raise ValueError(f"Unknown fact_id: {fact_id}")


def export_approved_facts(
    output_path: Path | None = None,
    db_path: Path | None = None,
) -> Path:
    target = output_path or PROJECT_ROOT / "data" / "exports" / "approved_facts.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = list_review_facts(status="approved", limit=100000, db_path=db_path)
    fields = [
        "fact_id",
        "paper_id",
        "pdf_id",
        "chunk_id",
        "page_number",
        "material",
        "material_family",
        "device_stack",
        "property_name",
        "raw_property_name",
        "value",
        "unit",
        "confidence",
        "evidence_text",
        "reviewer_notes",
        "paper_title",
        "doi",
        "year",
        "pdf_file_name",
        "pdf_path",
    ]
    with target.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field) for field in fields} for row in rows)
    return target
