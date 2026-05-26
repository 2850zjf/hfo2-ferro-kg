from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

from backend.core.config import get_llm_api_key, get_settings
from backend.db.session import connect
from backend.services.llm_extractor import normalize_base_url
from backend.services.llm_quota_guard import is_llm_budget_error, write_llm_pause
from backend.services.vector_store import search_vector_index


ACCEPTED_STATUSES = {"approved", "preapproved_machine", "needs_human_review"}

PROPERTY_ALIASES = {
    "double_remanent_polarization_2Pr": [
        "2pr",
        "2.pr",
        "2 pr",
        "double remanent",
        "double remanent polarization",
        "双剩余极化",
        "双倍剩余极化",
    ],
    "remanent_polarization_Pr": ["pr", "remanent polarization", "剩余极化"],
    "coercive_field_Ec": ["ec", "coercive", "coercive field", "矫顽场"],
    "endurance_cycles": ["endurance", "cycles", "循环", "耐久"],
    "memory_window": ["memory window", "窗口"],
    "retention_time": ["retention", "保持"],
    "leakage_current_density": ["leakage", "漏电"],
}

MATERIAL_ALIASES = {
    "HZO": ["hzo", "hfzr", "hf0.5zr0.5o2", "hf1-xzrxo2", "zirconium", "zr", "铪锆"],
    "HfO2": ["hfo2", "hafnia", "hafnium oxide", "氧化铪"],
    "La:HfO2": ["la", "lanthanum", "la:hfo2", "la-doped", "镧"],
    "Si:HfO2": ["si", "silicon", "si:hfo2", "si-doped", "硅"],
    "Al:HfO2": ["al", "aluminum", "al:hfo2", "al-doped", "铝"],
    "Y:HfO2": [" y ", "yttrium", "y:hfo2", "y-doped", "钇"],
    "Gd:HfO2": ["gd", "gadolinium", "gd:hfo2", "gd-doped", "钆"],
    "Sr:HfO2": ["sr", "strontium", "sr:hfo2", "sr-doped", "锶"],
}

POLARIZATION_PROPERTIES = {
    "double_remanent_polarization_2Pr",
    "remanent_polarization_Pr",
    "saturation_polarization_Ps",
}


@dataclass(frozen=True)
class FactHit:
    fact_id: str
    source: str
    title: str
    doi: str | None
    page_number: int | None
    review_status: str
    material: str
    material_family: str
    property_name: str
    value: float | None
    unit: str
    evidence_text: str
    context_quality: str
    context_score: float | None
    sample_id: str = ""
    sample_context: str = ""


def tokenize(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9_+\-./µμ²℃]+|[\u4e00-\u9fff]+", text or "")
        if len(token) > 1
    }


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


def _load_json(value: str | None) -> dict[str, Any]:
    try:
        data = json.loads(value or "{}")
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _sample_context(sample: dict[str, Any], phase: dict[str, Any]) -> str:
    parts = []
    for label, key in [
        ("厚度", "film_thickness_nm"),
        ("沉积", "deposition_method"),
        ("退火温度", "annealing_temperature_c"),
        ("退火时间", "annealing_time_s"),
        ("气氛", "annealing_atmosphere"),
        ("电极/stack", "device_stack"),
        ("衬底", "substrate"),
        ("器件", "device_type"),
    ]:
        value = sample.get(key)
        if value not in (None, "", []):
            parts.append(f"{label}={value}")
    if phase.get("phase_name"):
        parts.append(f"相={phase['phase_name']}")
    if phase.get("space_group"):
        parts.append(f"空间群={phase['space_group']}")
    return "；".join(parts)


def unit_matches_property(property_name: str | None, unit: str | None) -> bool:
    if not property_name:
        return True
    compact = (unit or "").lower().replace(" ", "").replace("μ", "u").replace("µ", "u").replace("²", "2")
    if property_name in POLARIZATION_PROPERTIES:
        return "c/cm" in compact
    if property_name == "coercive_field_Ec":
        return "v/cm" in compact
    if property_name == "endurance_cycles":
        return "cycle" in compact
    if property_name == "memory_window":
        return compact in {"v", "volt", "volts"} or compact.endswith("v")
    if property_name == "retention_time":
        return compact in {"s", "sec", "second", "seconds", "h", "hour", "hours", "year", "years"}
    if property_name == "leakage_current_density":
        return "a/cm" in compact
    return True


def infer_property(question: str) -> str | None:
    q = question.lower()
    if re.search(r"\b2\s*\.?\s*pr\b|双剩余极化|双倍剩余极化", q):
        return "double_remanent_polarization_2Pr"
    if re.search(r"\bec\b|矫顽", q):
        return "coercive_field_Ec"
    if re.search(r"\bpr\b|剩余极化|remanent", q):
        return "remanent_polarization_Pr"
    for prop, aliases in PROPERTY_ALIASES.items():
        if any(alias in q for alias in aliases):
            return prop
    return None


def infer_material_family(question: str) -> str | None:
    q = f" {question.lower()} "
    for family, aliases in MATERIAL_ALIASES.items():
        if any(alias in q for alias in aliases):
            return family
    return None


def _fact_from_reviewed_row(row: Any) -> FactHit:
    payload = _load_json(row["payload_json"])
    prop = payload.get("property") or {}
    material = payload.get("material") or {}
    sample = payload.get("sample") or {}
    phase_items = payload.get("phases") or []
    phase = phase_items[0] if isinstance(phase_items, list) and phase_items else {}
    ontology_context = payload.get("ontology_context") or {}
    return FactHit(
        fact_id=row["fact_id"],
        source="reviewed_facts",
        title=row["title"] or "Unknown paper",
        doi=row["doi"],
        page_number=row["page_number"],
        review_status=row["review_status"],
        material=material.get("canonical_name") or material.get("raw_name") or "",
        material_family=material.get("material_family") or "",
        property_name=prop.get("property_name") or "",
        value=_safe_float(prop.get("normalized_value", prop.get("value"))),
        unit=prop.get("normalized_unit") or prop.get("unit") or "",
        evidence_text=prop.get("evidence_text") or "",
        context_quality=ontology_context.get("context_quality") or "reviewed",
        context_score=_safe_float(ontology_context.get("context_score")),
        sample_context=_sample_context(sample, phase if isinstance(phase, dict) else {}),
    )


def _fact_from_sample_link_row(row: Any) -> FactHit:
    material = _load_json(row["material_json"])
    sample = _load_json(row["sample_json"])
    phase = _load_json(row["phase_json"])
    prop = _load_json(row["property_json"])
    return FactHit(
        fact_id=row["link_id"],
        source="sample_property_links",
        title=row["title"] or "Unknown paper",
        doi=row["doi"],
        page_number=row["page_number"],
        review_status="sample_linked",
        material=material.get("canonical_name") or material.get("raw_name") or "",
        material_family=material.get("material_family") or "",
        property_name=prop.get("property_name") or "",
        value=_safe_float(prop.get("normalized_value", prop.get("value"))),
        unit=prop.get("normalized_unit") or prop.get("unit") or "",
        evidence_text=row["evidence_text"] or prop.get("evidence_text") or "",
        context_quality=row["context_quality"],
        context_score=_safe_float(row["context_score"]),
        sample_id=row["sample_id"] or "",
        sample_context=_sample_context(sample, phase),
    )


def load_accepted_facts(db_path: Path | None = None) -> list[FactHit]:
    facts: list[FactHit] = []
    placeholders = ",".join("?" for _ in ACCEPTED_STATUSES)
    with connect(db_path) as conn:
        try:
            rows = conn.execute(
                """
                SELECT spl.*, p.title, p.doi
                FROM sample_property_links spl
                LEFT JOIN papers p ON p.paper_id = spl.paper_id
                WHERE spl.status = 'linked'
                """
            ).fetchall()
            facts.extend(_fact_from_sample_link_row(row) for row in rows)
        except Exception:
            pass

        rows = conn.execute(
            f"""
            SELECT rf.fact_id, rf.review_status, rf.page_number, rf.payload_json,
                   p.title, p.doi
            FROM reviewed_facts rf
            LEFT JOIN papers p ON p.paper_id = rf.paper_id
            WHERE rf.review_status IN ({placeholders})
            """,
            tuple(sorted(ACCEPTED_STATUSES)),
        ).fetchall()
    facts.extend(_fact_from_reviewed_row(row) for row in rows)
    return facts


def retrieve_facts(question: str, limit: int = 12, db_path: Path | None = None) -> list[FactHit]:
    q_tokens = tokenize(question)
    prop_filter = infer_property(question)
    material_filter = infer_material_family(question)
    hits: list[tuple[int, FactHit]] = []
    for fact in load_accepted_facts(db_path=db_path):
        if prop_filter and fact.property_name != prop_filter:
            continue
        if material_filter:
            material_text = f"{fact.material} {fact.material_family}".lower()
            aliases = MATERIAL_ALIASES.get(material_filter, [])
            if material_filter.lower() not in material_text and not any(alias in material_text for alias in aliases):
                continue
        searchable = " ".join(
            [
                fact.title,
                fact.doi or "",
                fact.material,
                fact.material_family,
                fact.property_name,
                fact.unit,
                fact.sample_context,
                fact.evidence_text,
            ]
        )
        score = len(q_tokens & tokenize(searchable))
        if prop_filter:
            score += 3
        if material_filter:
            score += 2
        if fact.source == "sample_property_links":
            score += 3
        if fact.context_quality == "strong":
            score += 2
        elif fact.context_quality == "partial":
            score += 1
        if score > 0:
            hits.append((score, fact))
    return [fact for _, fact in sorted(hits, key=lambda item: item[0], reverse=True)[:limit]]


def fact_to_record(fact: FactHit) -> dict[str, Any]:
    return {
        "fact_id": fact.fact_id,
        "source": fact.source,
        "title": fact.title,
        "doi": fact.doi,
        "page_number": fact.page_number,
        "review_status": fact.review_status,
        "material": fact.material,
        "material_family": fact.material_family,
        "property_name": fact.property_name,
        "value": fact.value,
        "unit": fact.unit,
        "evidence_text": fact.evidence_text,
        "context_quality": fact.context_quality,
        "context_score": fact.context_score,
        "sample_id": fact.sample_id,
        "sample_context": fact.sample_context,
    }


def _dedupe_facts(facts: list[FactHit]) -> list[FactHit]:
    seen: set[str] = set()
    unique: list[FactHit] = []
    for fact in facts:
        key = fact.fact_id
        if key in seen:
            continue
        seen.add(key)
        unique.append(fact)
    return unique


def build_rag_context(question: str, db_path: Path | None = None, fact_limit: int = 24) -> dict[str, Any]:
    all_facts = retrieve_facts(question, limit=5000, db_path=db_path)
    prop = infer_property(question)
    material = infer_material_family(question)
    numeric_candidates = [fact for fact in all_facts if fact.value is not None]
    numeric = [
        fact
        for fact in numeric_candidates
        if unit_matches_property(prop or fact.property_name, fact.unit)
    ]
    statistics: dict[str, Any] = {
        "property_filter": prop,
        "material_filter": material,
        "fact_count": len(all_facts),
        "numeric_candidate_count": len(numeric_candidates),
        "excluded_numeric_count_due_to_unit": len(numeric_candidates) - len(numeric),
    }
    if numeric:
        values = [fact.value for fact in numeric if fact.value is not None]
        min_fact = min(numeric, key=lambda item: item.value if item.value is not None else float("inf"))
        max_fact = max(numeric, key=lambda item: item.value if item.value is not None else float("-inf"))
        positive = [fact for fact in numeric if fact.value is not None and fact.value > 0]
        statistics.update(
            {
                "numeric_fact_count": len(numeric),
                "min": min(values),
                "max": max(values),
                "median": median(values),
                "unit": max_fact.unit or min_fact.unit,
                "min_fact": fact_to_record(min_fact),
                "max_fact": fact_to_record(max_fact),
            }
        )
        if positive and len(positive) != len(numeric):
            positive_values = [fact.value for fact in positive if fact.value is not None]
            positive_min_fact = min(positive, key=lambda item: item.value if item.value is not None else float("inf"))
            statistics.update(
                {
                    "positive_numeric_fact_count": len(positive),
                    "positive_min": min(positive_values),
                    "positive_median": median(positive_values),
                    "positive_min_fact": fact_to_record(positive_min_fact),
                }
            )
    evidence_seed: list[FactHit] = []
    if numeric:
        evidence_seed.extend([min_fact, max_fact])
        evidence_seed.extend(numeric[:fact_limit])
    else:
        evidence_seed.extend(all_facts[:fact_limit])
    evidence_facts = _dedupe_facts(evidence_seed)[:fact_limit]
    vector_hits = [] if db_path is not None else search_vector_index(question, limit=5)
    return {
        "question": question,
        "accepted_facts_definition": "approved + preapproved_machine + needs_human_review + sample_property_links are usable baseline facts.",
        "statistics": statistics,
        "evidence_facts": [fact_to_record(fact) for fact in evidence_facts],
        "related_chunks": vector_hits,
    }


def _format_evidence(fact: FactHit, index: int) -> list[str]:
    doi = fact.doi or "DOI 未识别"
    page = f"p. {fact.page_number}" if fact.page_number else "页码未识别"
    value = f"{fact.value:g} {fact.unit}".strip() if fact.value is not None else "数值未识别"
    sample = f" | 样品条件：{fact.sample_context}" if fact.sample_context else ""
    return [
        f"{index}. {fact.title} | {doi} | {page} | {fact.material} | {fact.property_name} = {value}{sample}",
        f"   证据：{fact.evidence_text}",
    ]


def answer_range_from_context(context: dict[str, Any]) -> str | None:
    stats = context.get("statistics") or {}
    prop = stats.get("property_filter")
    if not prop or "min" not in stats or "max" not in stats:
        return None
    material = stats.get("material_filter") or "当前筛选材料"
    unit = stats.get("unit") or ""
    min_fact = FactHit(**stats["min_fact"])
    max_fact = FactHit(**stats["max_fact"])
    lines = [
        f"基于当前 accepted facts，{material} 的 {prop} 统计如下：",
        "",
        f"- 样本数：{stats.get('numeric_fact_count', 0)} 条可统计事实",
        f"- 范围：{stats['min']:g} - {stats['max']:g} {unit}",
        f"- 中位数：{stats['median']:g} {unit}",
    ]
    if "positive_min" in stats:
        lines.append(
            f"- 排除 0 或负值后的正值范围：{stats['positive_min']:g} - {stats['max']:g} {unit}；"
            f"正值中位数：{stats['positive_median']:g} {unit}"
        )
    excluded = stats.get("excluded_numeric_count_due_to_unit") or 0
    if excluded:
        lines.append(f"- 已排除：{excluded} 条单位不匹配的数值，例如倍率、百分比或其他非目标物理量。")
    lines.extend(["", "关键证据："])
    lines.extend(_format_evidence(min_fact, 1))
    lines.extend(_format_evidence(max_fact, 2))
    lines.append("")
    lines.append("注意：Pr 和 2Pr 已分开统计；这里不会把 2Pr 自动当作 Pr。")
    return "\n".join(lines)


def synthesize_answer_with_llm(context: dict[str, Any], model: str | None = None) -> tuple[str | None, str | None]:
    if not get_llm_api_key():
        return None, "LLM API key is not configured."
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover
        return None, f"OpenAI SDK unavailable: {exc}"

    settings = get_settings()
    selected_model = model or settings.llm_model
    base_url, _ = normalize_base_url(settings.llm_provider, settings.llm_base_url)
    client_kwargs: dict[str, Any] = {
        "api_key": get_llm_api_key(),
        "timeout": settings.llm_timeout_seconds,
    }
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)
    system_prompt = """
你是 HfO2-FerroKG 的证据推理型 RAG 回答器。
只能使用用户提供的 JSON context，不允许编造论文、DOI、页码、数值或趋势。
回答必须使用中文。
每个关键结论都要引用 fact_id、论文标题或 DOI、页码、证据句。
如果是数值范围问题，必须严格使用 context.statistics 中的统计值。
严格区分 Pr 和 2Pr；除非 context 明确给出派生规则，否则不要把 2Pr 除以 2 当作 Pr。
如果证据不足，回答“当前数据库没有足够证据回答该问题。”
""".strip()
    request_kwargs: dict[str, Any] = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
        "temperature": 0,
        "max_tokens": min(settings.llm_max_tokens, 2200),
    }
    if settings.llm_provider == "dashscope":
        request_kwargs["extra_body"] = {"enable_thinking": settings.llm_enable_thinking}

    last_error = ""
    for attempt in range(1, 4):
        try:
            response = client.chat.completions.create(**request_kwargs)
            return response.choices[0].message.content or "", None
        except Exception as exc:
            last_error = str(exc)
            if is_llm_budget_error(last_error):
                write_llm_pause(
                    last_error,
                    {
                        "pipeline": "rag_answer",
                        "question": context.get("question"),
                        "model": selected_model,
                    },
                )
                return None, last_error
            if attempt < 3 and (
                "connection" in last_error.lower()
                or "timeout" in last_error.lower()
                or "timed out" in last_error.lower()
                or "temporarily" in last_error.lower()
            ):
                time.sleep(1.5 * attempt)
                continue
            return None, last_error
    return None, last_error


def _friendly_llm_error(error: str | None) -> str:
    if is_llm_budget_error(error):
        return (
            "LLM 调用被服务商拒绝，通常是额度、账单、限流或 API key 状态问题。"
            "系统已自动切回本地结构化证据回答；恢复账户状态或更换可用 key 后，可以重新开启 LLM 综合回答。"
        )
    if error:
        if "connection" in error.lower() or "timeout" in error.lower():
            return (
                "LLM 综合回答遇到临时连接问题，已重试后退回本地结构化证据回答。"
                "这通常是网络波动、服务商连接被重置，或后台并行抽取占用大量并发连接导致的。"
                f"错误摘要：{error[:240]}"
            )
        return f"LLM 综合回答失败，已退回本地结构化证据回答。错误摘要：{error[:300]}"
    return "LLM 综合回答失败，已退回本地结构化证据回答。"


def answer_question(
    question: str,
    db_path: Path | None = None,
    use_llm: bool = False,
    llm_model: str | None = None,
) -> str:
    context = build_rag_context(question, db_path=db_path)
    facts = [FactHit(**record) for record in context["evidence_facts"]]
    vector_hits = [] if db_path is not None else search_vector_index(question, limit=5)
    if not facts and not vector_hits:
        return "当前数据库没有足够证据回答该问题。"

    fallback_note = ""
    if use_llm:
        llm_answer, error = synthesize_answer_with_llm(context, model=llm_model)
        if llm_answer:
            return llm_answer
        fallback_note = _friendly_llm_error(error) + "\n\n"

    range_answer = answer_range_from_context(context)
    if range_answer:
        return fallback_note + range_answer

    lines = [
        "基于当前 accepted facts，检索到以下证据。正式论文结论建议优先引用你最终人工确认后的 approved facts。",
        "",
    ]
    if facts:
        lines.append("结构化事实证据：")
        for index, fact in enumerate(facts, start=1):
            lines.extend(_format_evidence(fact, index))
    if vector_hits:
        lines.append("")
        lines.append("相关原文 chunk：")
        for index, hit in enumerate(vector_hits, start=1):
            doi = hit.get("doi") or "DOI 未识别"
            page = f"p. {hit.get('page_number')}" if hit.get("page_number") else "页码未识别"
            title = hit.get("title") or "Unknown paper"
            text = " ".join(str(hit.get("text", "")).split())[:360]
            lines.append(f"{index}. {title} | {doi} | {page} | score={hit['score']:.3f}")
            lines.append(f"   原文：{text}")
    if re.search(r"\bpr\b|polarization|2pr|剩余极化", question, re.I):
        lines.append("")
        lines.append("注意：系统严格区分 Pr 与 2Pr。")
    return fallback_note + "\n".join(lines)
