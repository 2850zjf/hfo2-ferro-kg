from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.core.config import PROJECT_ROOT, get_llm_api_key, get_settings
from backend.db.session import connect
from backend.services.llm_extractor import json_loads_object, normalize_base_url
from backend.services.pipeline_log import record_pipeline_run


ACCEPTED_BASELINE = {"approved", "preapproved_machine", "needs_human_review"}


@dataclass(frozen=True)
class DatasetFact:
    fact_id: str
    review_status: str
    paper_id: str
    pdf_id: str
    page_number: int | None
    title: str
    doi: str | None
    material: dict[str, Any]
    sample: dict[str, Any]
    prop: dict[str, Any]
    phases: list[dict[str, Any]]
    devices: list[dict[str, Any]]
    ontology_context: dict[str, Any]
    evidence_text: str


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _fact_from_row(row: Any) -> DatasetFact:
    payload = json.loads(row["payload_json"])
    prop = payload.get("property") or {}
    return DatasetFact(
        fact_id=row["fact_id"],
        review_status=row["review_status"],
        paper_id=row["paper_id"],
        pdf_id=row["pdf_id"],
        page_number=row["page_number"],
        title=row["title"] or "",
        doi=row["doi"],
        material=payload.get("material") or {},
        sample=payload.get("sample") or {},
        prop=prop,
        phases=payload.get("phases") or [],
        devices=payload.get("devices") or [],
        ontology_context=payload.get("ontology_context") or {},
        evidence_text=prop.get("evidence_text") or "",
    )


def load_dataset_facts(
    status_set: set[str] | None = None,
    limit: int | None = None,
    db_path: Path | None = None,
) -> list[DatasetFact]:
    statuses = status_set or ACCEPTED_BASELINE
    placeholders = ",".join("?" for _ in statuses)
    limit_clause = "LIMIT ?" if limit else ""
    params: list[Any] = list(sorted(statuses))
    if limit:
        params.append(limit)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT rf.fact_id, rf.review_status, rf.paper_id, rf.pdf_id,
                   rf.page_number, rf.payload_json, p.title, p.doi
            FROM reviewed_facts rf
            LEFT JOIN papers p ON p.paper_id = rf.paper_id
            WHERE rf.review_status IN ({placeholders})
            ORDER BY rf.created_at DESC
            {limit_clause}
            """,
            params,
        ).fetchall()
    return [_fact_from_row(row) for row in rows]


def _verdict(valid: bool, issues: list[str], confidence: float) -> dict[str, Any]:
    if valid and not issues:
        verdict = "valid"
    elif any("reject" in issue.lower() or "非 hfo2" in issue.lower() for issue in issues):
        verdict = "reject"
    else:
        verdict = "needs_check"
    return {
        "verdict": verdict,
        "confidence": round(confidence, 3),
        "issues": issues,
    }


def evidence_consistency_model(fact: DatasetFact) -> dict[str, Any]:
    issues: list[str] = []
    prop_name = str(fact.prop.get("property_name") or "")
    evidence = fact.evidence_text
    value = fact.prop.get("normalized_value", fact.prop.get("value"))
    unit = fact.prop.get("normalized_unit") or fact.prop.get("unit")
    if not evidence or len(evidence.strip()) < 30:
        issues.append("证据句缺失或过短")
    if not fact.page_number:
        issues.append("页码缺失")
    if value is None:
        issues.append("数值缺失")
    elif str(value).split(".")[0] not in evidence.replace(",", ""):
        issues.append("证据句中未直接看到数值")
    if not unit:
        issues.append("单位缺失")
    if prop_name == "remanent_polarization_Pr" and re.search(r"\b2\s*\.?\s*P\s*\.?\s*r\b|2Pr", evidence, re.I):
        issues.append("Pr/2Pr 冲突：证据像是 2Pr")
    if prop_name == "double_remanent_polarization_2Pr" and not re.search(
        r"\b2\s*\.?\s*P\s*\.?\s*r\b|2Pr|double remanent",
        evidence,
        re.I,
    ):
        issues.append("2Pr 事实缺少明确 2Pr 证据")
    if re.search(r"\[[\d,\-\s]+\]|previous(?:ly)? reported|literature|reported by", evidence, re.I):
        issues.append("可能是综述引用或二手数据")
    return _verdict(not issues, issues, 0.94 if not issues else 0.58)


def ontology_relation_model(fact: DatasetFact) -> dict[str, Any]:
    issues: list[str] = []
    context = fact.ontology_context
    missing = context.get("missing_context_labels") or []
    quality = context.get("context_quality") or "weak"
    if not fact.material:
        issues.append("材料节点缺失")
    if not fact.sample:
        issues.append("样品/工艺节点缺失")
    if not fact.prop:
        issues.append("性能节点缺失")
    if missing:
        issues.append("缺少上下文字段：" + "、".join(str(item) for item in missing[:6]))
    if quality == "weak":
        issues.append("关系上下文较弱")
    return _verdict(quality in {"strong", "partial"} and bool(fact.material and fact.prop), issues, 0.9 if quality == "strong" else 0.68)


def domain_range_model(fact: DatasetFact) -> dict[str, Any]:
    issues: list[str] = []
    prop_name = str(fact.prop.get("property_name") or "")
    value = _safe_float(fact.prop.get("normalized_value", fact.prop.get("value")))
    unit = str(fact.prop.get("normalized_unit") or fact.prop.get("unit") or "")
    family = str(fact.material.get("material_family") or "")
    if family and family not in {
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
        issues.append("非 HfO2 本体材料，建议 reject")
    if value is None:
        issues.append("数值无法解析")
    elif value < 0:
        issues.append("负数性能值异常")
    elif prop_name == "remanent_polarization_Pr" and value > 100:
        issues.append("Pr > 100 μC/cm² 异常")
    elif prop_name == "double_remanent_polarization_2Pr" and value > 200:
        issues.append("2Pr > 200 μC/cm² 异常")
    elif prop_name == "coercive_field_Ec" and "MV/cm" in unit and value > 10:
        issues.append("Ec > 10 MV/cm 异常")
    return _verdict(not issues, issues, 0.96 if not issues else 0.55)


LOCAL_MODELS = {
    "evidence_consistency": evidence_consistency_model,
    "ontology_relation": ontology_relation_model,
    "domain_range": domain_range_model,
}


def ensemble_model(fact: DatasetFact, local_results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    votes = Counter(result["verdict"] for result in local_results.values())
    valid = votes["valid"] >= 2
    issues: list[str] = []
    for result in local_results.values():
        issues.extend(result.get("issues") or [])
    confidence = sum(float(result.get("confidence") or 0) for result in local_results.values()) / max(1, len(local_results))
    return _verdict(valid, sorted(set(issues)), confidence)


def _llm_fact_prompt(fact: DatasetFact) -> str:
    payload = {
        "fact_id": fact.fact_id,
        "paper_title": fact.title,
        "doi": fact.doi,
        "page_number": fact.page_number,
        "material": fact.material,
        "sample": fact.sample,
        "property": fact.prop,
        "phases": fact.phases,
        "devices": fact.devices,
        "ontology_context": fact.ontology_context,
        "evidence_text": fact.evidence_text,
    }
    return (
        "You are validating an HfO2 ferroelectric knowledge-graph fact. "
        "Assume the current reviewed dataset is the baseline label: valid. "
        "Check whether this fact is internally consistent with the evidence and ontology. "
        "Return only JSON with keys: verdict(valid|needs_check|reject), confidence(0-1), issues(array), rationale(string). "
        "Be strict about Pr vs 2Pr, units, secondary literature, and missing sample/process context.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def llm_validate_fact(fact: DatasetFact, model: str) -> dict[str, Any]:
    if not get_llm_api_key():
        return {"verdict": "needs_check", "confidence": 0.0, "issues": ["LLM API key not configured"]}
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover
        return {"verdict": "needs_check", "confidence": 0.0, "issues": [f"OpenAI SDK unavailable: {exc}"]}

    settings = get_settings()
    base_url, _ = normalize_base_url(settings.llm_provider, settings.llm_base_url)
    client_kwargs: dict[str, Any] = {"api_key": get_llm_api_key(), "timeout": settings.llm_timeout_seconds}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)
    try:
        request_kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": "Return strict JSON only."},
                {"role": "user", "content": _llm_fact_prompt(fact)},
            ],
            "temperature": 0,
            "max_tokens": min(settings.llm_max_tokens, 1200),
            "response_format": {"type": "json_object"},
        }
        if settings.llm_provider == "dashscope":
            request_kwargs["extra_body"] = {"enable_thinking": settings.llm_enable_thinking}
        response = client.chat.completions.create(**request_kwargs)
        data = json_loads_object(response.choices[0].message.content or "{}")
        verdict = str(data.get("verdict") or "needs_check")
        if verdict not in {"valid", "needs_check", "reject"}:
            verdict = "needs_check"
        return {
            "verdict": verdict,
            "confidence": max(0.0, min(1.0, float(data.get("confidence") or 0.5))),
            "issues": [str(item) for item in data.get("issues") or []],
            "rationale": str(data.get("rationale") or ""),
        }
    except Exception as exc:
        return {"verdict": "needs_check", "confidence": 0.0, "issues": [str(exc)[:300]]}


def _summarize_model(model_name: str, details: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(details)
    verdicts = Counter(row["verdict"] for row in details)
    issue_counter: Counter[str] = Counter()
    for row in details:
        for issue in row.get("issues") or []:
            issue_counter[str(issue)] += 1
    valid = verdicts.get("valid", 0)
    return {
        "model_name": model_name,
        "total": total,
        "valid": valid,
        "needs_check": verdicts.get("needs_check", 0),
        "reject": verdicts.get("reject", 0),
        "agreement_accuracy": round(valid / total, 4) if total else 0.0,
        "average_confidence": round(
            sum(float(row.get("confidence") or 0) for row in details) / total,
            4,
        )
        if total
        else 0.0,
        "top_issues": dict(issue_counter.most_common(8)),
    }


def _write_outputs(summary: list[dict[str, Any]], details: list[dict[str, Any]]) -> dict[str, str]:
    output_dir = PROJECT_ROOT / "data" / "exports"
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_json = output_dir / "model_validation_summary.json"
    details_json = output_dir / "model_validation_details.jsonl"
    summary_csv = output_dir / "model_validation_summary.csv"
    details_csv = output_dir / "model_validation_details.csv"

    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with details_json.open("w", encoding="utf-8") as fh:
        for row in details:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    with summary_csv.open("w", newline="", encoding="utf-8-sig") as fh:
        fields = ["model_name", "total", "valid", "needs_check", "reject", "agreement_accuracy", "average_confidence", "top_issues"]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in summary:
            writer.writerow({**row, "top_issues": json.dumps(row["top_issues"], ensure_ascii=False)})

    with details_csv.open("w", newline="", encoding="utf-8-sig") as fh:
        fields = ["model_name", "fact_id", "verdict", "confidence", "issues", "baseline_label", "property_name", "material_family", "doi", "page_number", "rationale"]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in details:
            writer.writerow({**row, "issues": json.dumps(row.get("issues") or [], ensure_ascii=False)})

    return {
        "summary_json": str(summary_json),
        "details_json": str(details_json),
        "summary_csv": str(summary_csv),
        "details_csv": str(details_csv),
    }


def run_multi_model_validation(
    limit: int | None = None,
    include_llm: bool = False,
    llm_models: list[str] | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    facts = load_dataset_facts(limit=limit, db_path=db_path)
    model_details: dict[str, list[dict[str, Any]]] = {name: [] for name in LOCAL_MODELS}
    model_details["ensemble"] = []

    for fact in facts:
        local_results: dict[str, dict[str, Any]] = {}
        for model_name, validator in LOCAL_MODELS.items():
            result = validator(fact)
            local_results[model_name] = result
            model_details[model_name].append(_detail_row(model_name, fact, result))
        ensemble_result = ensemble_model(fact, local_results)
        model_details["ensemble"].append(_detail_row("ensemble", fact, ensemble_result))

    if include_llm:
        selected_models = llm_models or [get_settings().llm_model]
        for model_name in selected_models:
            key = f"llm:{model_name}"
            model_details[key] = []
            for fact in facts:
                result = llm_validate_fact(fact, model_name)
                model_details[key].append(_detail_row(key, fact, result))

    details = [row for rows in model_details.values() for row in rows]
    summary = [_summarize_model(model_name, rows) for model_name, rows in model_details.items()]
    paths = _write_outputs(summary, details)
    stats = {
        "facts": len(facts),
        "models": len(model_details),
        "include_llm": include_llm,
        "summary": summary,
        "paths": paths,
    }
    record_pipeline_run("18_multi_model_validate_dataset", "ok", stats, db_path=db_path)
    return stats


def _detail_row(model_name: str, fact: DatasetFact, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "model_name": model_name,
        "fact_id": fact.fact_id,
        "verdict": result.get("verdict") or "needs_check",
        "confidence": result.get("confidence") or 0,
        "issues": result.get("issues") or [],
        "baseline_label": "valid",
        "property_name": fact.prop.get("property_name"),
        "material_family": fact.material.get("material_family"),
        "doi": fact.doi,
        "page_number": fact.page_number,
        "rationale": result.get("rationale") or "",
    }
