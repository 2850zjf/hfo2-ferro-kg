from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from backend.core.config import PROJECT_ROOT, get_settings
from backend.db.session import connect
from backend.services.ontology_context import build_ontology_context


REVIEW_STATUSES = {"pending", "preapproved_machine", "needs_human_review", "approved", "rejected"}


def assisted_review_suggestion(fact: dict[str, Any]) -> dict[str, Any]:
    risk_flags: list[str] = []
    checklist: list[str] = []
    evidence = str(fact.get("evidence_text") or "")
    property_name = str(fact.get("property_name") or "")
    material = str(fact.get("material") or "")
    material_family = str(fact.get("material_family") or "")
    unit = str(fact.get("unit") or "")
    status = str(fact.get("review_status") or "")
    context_quality = str(fact.get("context_quality") or "unknown")
    comparison_ready = bool(fact.get("comparison_ready"))
    missing_context = [str(item) for item in fact.get("missing_context_labels") or []]

    def _float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    value = _float(fact.get("value"))
    if not material or material.lower() == "none":
        risk_flags.append("材料名称缺失，不能直接批准。")
    if material_family and material_family not in {
        "HfO2",
        "HZO",
        "Si:HfO2",
        "Al:HfO2",
        "La:HfO2",
        "Y:HfO2",
        "Gd:HfO2",
        "Sr:HfO2",
        "mixed_doped_HfO2",
        "unknown_hafnia",
    }:
        risk_flags.append("材料体系不在 HfO2 本体范围内，建议拒绝或复核。")
    if not evidence or len(evidence.strip()) < 30:
        risk_flags.append("证据句太短或缺失。")
    if missing_context:
        risk_flags.append("缺少关联上下文：" + "、".join(missing_context[:6]))

    if property_name == "remanent_polarization_Pr" and re.search(
        r"\b2\s*\.?\s*P\s*\.?\s*r\b|2Pr|double remanent",
        evidence,
        re.I,
    ):
        risk_flags.append("证据句像是在报告 2Pr，不应直接当作 Pr。")
    if property_name == "double_remanent_polarization_2Pr" and not re.search(
        r"\b2\s*\.?\s*P\s*\.?\s*r\b|2Pr|double remanent",
        evidence,
        re.I,
    ):
        risk_flags.append("证据句没有明确 2Pr 标记。")
    if property_name == "remanent_polarization_Pr" and value is not None and value > 100:
        risk_flags.append("Pr > 100 μC/cm²，数值异常。")
    if property_name == "double_remanent_polarization_2Pr" and value is not None and value > 200:
        risk_flags.append("2Pr > 200 μC/cm²，数值异常。")
    if property_name == "coercive_field_Ec" and value is not None and "MV/cm" in unit and value > 10:
        risk_flags.append("Ec > 10 MV/cm，数值异常。")
    if re.search(
        r"\[[\d,\-\s]+\]|previous(?:ly)? reported|literature|recent reports?|reported by",
        evidence,
        re.I,
    ):
        risk_flags.append("证据句可能是文献引用或综述转述，需确认是否为本文原始实验值。")

    checklist.extend(
        [
            "核对材料体系是否属于 HfO2 / HZO / doped HfO2。",
            "核对 Pr 与 2Pr 是否被严格区分。",
            "核对数值、单位、页码是否与证据句一致。",
            "核对厚度、退火、电极、沉积方法、器件类型是否来自同一样品。",
        ]
    )

    if any("不在 HfO2" in flag for flag in risk_flags):
        suggested_status = "rejected"
    elif not risk_flags and comparison_ready and context_quality == "strong":
        suggested_status = "approved" if status == "preapproved_machine" else status or "approved"
    elif not risk_flags and status == "preapproved_machine":
        suggested_status = "preapproved_machine"
    else:
        suggested_status = "needs_human_review"

    note = "AI辅助审核建议：" + suggested_status
    if risk_flags:
        note += "；风险：" + "；".join(risk_flags[:4])
    else:
        note += "；未发现明显规则风险，仍建议对照 PDF 原文确认。"

    return {
        "suggested_status": suggested_status,
        "risk_flags": risk_flags,
        "checklist": checklist,
        "note": note,
    }


def _fact_record(row: Any) -> dict[str, Any]:
    payload = json.loads(row["payload_json"])
    prop = payload.get("property") or {}
    material = payload.get("material") or {}
    sample = payload.get("sample") or {}
    preaudit = payload.get("preaudit") or {}
    phases = payload.get("phases") or []
    devices = payload.get("devices") or []
    ontology_context = payload.get("ontology_context") or build_ontology_context(
        material,
        sample,
        prop,
        phases,
        devices,
    )
    requested = ontology_context.get("requested_review_fields", {})
    device_context = ontology_context.get("device_context", {})
    process_context = ontology_context.get("process_context", {})
    sample_context = ontology_context.get("sample_context", {})
    structure_context = ontology_context.get("structure_context", {})
    measurement_state = ontology_context.get("measurement_state_context", {})
    record = {
        "fact_id": row["fact_id"],
        "review_status": row["review_status"],
        "paper_id": row["paper_id"],
        "pdf_id": row["pdf_id"],
        "chunk_id": row["chunk_id"],
        "page_number": row["page_number"],
        "material": material.get("canonical_name") or material.get("raw_name"),
        "material_family": material.get("material_family"),
        "device_stack": sample.get("device_stack"),
        "material_system": requested.get("材料体系") or material.get("material_family"),
        "polarization_kind": requested.get("Pr 或 2Pr"),
        "film_thickness_nm": sample_context.get("film_thickness_nm"),
        "annealing_temperature_c": process_context.get("annealing_temperature_c"),
        "annealing_time_s": process_context.get("annealing_time_s"),
        "annealing_atmosphere": process_context.get("annealing_atmosphere"),
        "electrode_stack": requested.get("电极 stack") or device_context.get("electrode_stack"),
        "deposition_method": process_context.get("deposition_method"),
        "device_type": requested.get("器件类型"),
        "phase_structure": requested.get("相结构"),
        "wake_up_state": measurement_state.get("wake_up_state"),
        "endurance_state": measurement_state.get("endurance_state"),
        "context_score": ontology_context.get("context_score"),
        "context_quality": ontology_context.get("context_quality"),
        "comparison_ready": ontology_context.get("comparison_ready"),
        "missing_context_labels": ontology_context.get("missing_context_labels", []),
        "ontology_context": ontology_context,
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
    record["assistant_review"] = assisted_review_suggestion(record)
    record["ai_suggested_status"] = record["assistant_review"]["suggested_status"]
    record["ai_risk_flags"] = record["assistant_review"]["risk_flags"]
    record["ai_review_note"] = record["assistant_review"]["note"]
    return record


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


def _is_safe_pdf_path(pdf_path: Path) -> bool:
    pdf_root = get_settings().pdf_root.resolve()
    try:
        return pdf_path.resolve().is_relative_to(pdf_root)
    except AttributeError:  # pragma: no cover - Python < 3.9 compatibility
        return str(pdf_path.resolve()).startswith(str(pdf_root))


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
        "is_safe_path": _is_safe_pdf_path(pdf_path),
    }


def _validated_pdf_record(pdf_id: str, db_path: Path | None = None) -> tuple[dict[str, Any] | None, str | None]:
    record = get_pdf_viewer_record(pdf_id, db_path=db_path)
    if record is None:
        return None, f"没有在数据库中找到 PDF：{pdf_id}"
    if not record["exists"]:
        return None, "数据库里有这篇 PDF 的记录，但本地文件不存在。"
    if not record["is_safe_path"]:
        return None, "这个 PDF 不在 data/raw_pdfs 安全目录下，已阻止打开。"
    return record, None


def open_pdf_in_default_browser(
    pdf_id: str | None,
    page_number: int | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    if not pdf_id:
        return {"ok": False, "message": "缺少 pdf_id。"}
    record, error = _validated_pdf_record(pdf_id, db_path=db_path)
    if error or record is None:
        return {"ok": False, "message": error}
    url = pdf_file_url(record["file_path"], page_number)
    if not url:
        return {"ok": False, "message": "无法生成本地 PDF 链接。"}
    try:
        opened = webbrowser.open(url, new=2, autoraise=True)
    except Exception as exc:
        return {"ok": False, "message": f"默认浏览器打开失败：{exc}"}
    return {
        "ok": bool(opened),
        "message": "已请求本机默认浏览器打开 PDF。",
        "url": url,
        "file_path": record["file_path"],
    }


def open_pdf_with_default_app(
    pdf_id: str | None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    if not pdf_id:
        return {"ok": False, "message": "缺少 pdf_id。"}
    record, error = _validated_pdf_record(pdf_id, db_path=db_path)
    if error or record is None:
        return {"ok": False, "message": error}
    pdf_path = Path(record["file_path"])
    try:
        if sys.platform.startswith("win"):
            os.startfile(pdf_path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(pdf_path)])
        else:
            subprocess.Popen(["xdg-open", str(pdf_path)])
    except Exception as exc:
        return {"ok": False, "message": f"系统默认程序打开失败：{exc}"}
    return {
        "ok": True,
        "message": "已请求系统默认程序打开 PDF。",
        "file_path": record["file_path"],
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
        "material_system",
        "device_stack",
        "film_thickness_nm",
        "annealing_temperature_c",
        "annealing_time_s",
        "annealing_atmosphere",
        "electrode_stack",
        "deposition_method",
        "device_type",
        "phase_structure",
        "wake_up_state",
        "endurance_state",
        "context_quality",
        "context_score",
        "comparison_ready",
        "missing_context_labels",
        "property_name",
        "raw_property_name",
        "value",
        "unit",
        "confidence",
        "evidence_text",
        "ai_suggested_status",
        "ai_risk_flags",
        "ai_review_note",
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
