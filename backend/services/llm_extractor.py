from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.core.config import PROJECT_ROOT, get_llm_api_key, get_settings
from backend.schemas.hfo2_extraction_schema import HfO2ExtractionResult
from backend.services.fact_normalizer import normalize_property_alias, normalize_unit, parse_numeric
from backend.services.ontology_builder import load_ontology_prompt_context


@dataclass(frozen=True)
class LLMExtractionOutcome:
    result: HfO2ExtractionResult | None
    used_llm: bool
    error_message: str | None = None
    usage: dict[str, int | None] | None = None


def llm_is_configured() -> bool:
    return bool(get_llm_api_key())


def normalize_base_url(provider: str, base_url: str | None) -> tuple[str | None, str | None]:
    """Return an OpenAI-compatible base URL and a short note when normalized."""
    if provider != "dashscope":
        return base_url, None
    if not base_url:
        return "https://dashscope.aliyuncs.com/compatible-mode/v1", None
    cleaned = base_url.rstrip("/")
    if cleaned.endswith("/api/v1"):
        return (
            cleaned[: -len("/api/v1")] + "/compatible-mode/v1",
            "DashScope /api/v1 was normalized to the OpenAI-compatible /compatible-mode/v1 endpoint.",
        )
    return cleaned, None


def llm_status() -> dict[str, Any]:
    settings = get_settings()
    try:
        import openai

        sdk_available = True
        sdk_version = getattr(openai, "__version__", "unknown")
    except Exception:
        sdk_available = False
        sdk_version = None
    return {
        "use_llm": settings.use_llm,
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "api_key_configured": llm_is_configured(),
        "base_url_configured": bool(settings.llm_base_url),
        "timeout_seconds": settings.llm_timeout_seconds,
        "max_tokens": settings.llm_max_tokens,
        "enable_thinking": settings.llm_enable_thinking,
        "normalized_base_url_note": normalize_base_url(
            settings.llm_provider, settings.llm_base_url
        )[1],
        "openai_sdk_available": sdk_available,
        "openai_sdk_version": sdk_version,
    }


def load_prompt() -> str:
    base_prompt = (PROJECT_ROOT / "prompts" / "hfo2_extraction_prompt.md").read_text(
        encoding="utf-8"
    )
    return f"{base_prompt}\n\n{load_ontology_prompt_context()}"


def build_user_input(row) -> str:
    profile_values = infer_extraction_profiles(str(_row_get(row, "text", "")), _row_get(row, "section"))
    profiles = ", ".join(profile_values)
    profile_directives = _profile_directives(profile_values)
    metadata = "\n".join(
        f"{key}: {_row_get(row, key)}"
        for key in ["title", "doi", "year", "paper_type", "is_review", "section"]
        if _row_get(row, key) not in (None, "")
    )
    before = str(_row_get(row, "context_before", "")).strip()
    after = str(_row_get(row, "context_after", "")).strip()
    try:
        context_chars = max(0, int(os.getenv("HFO2_FERROKG_LLM_CONTEXT_CHARS", "600")))
    except ValueError:
        context_chars = 600
    context = ""
    if before or after:
        context = f"""

CONTEXT_FOR_DISAMBIGUATION_ONLY:
[before]
{before[:context_chars]}
[after]
{after[:context_chars]}
""".rstrip()
    return f"""
paper_id: {row['paper_id']}
pdf_id: {row['pdf_id']}
chunk_id: {row['chunk_id']}
page_number: {row['page_number']}
extraction_profiles: {profiles}
PROFILE_REQUIRED_CHECKS:
{profile_directives}
{metadata}

FOCAL_CHUNK:
{row['text']}
{context}
""".strip()


def _profile_directives(profiles: list[str]) -> str:
    directives: list[str] = []
    if "measurement" in profiles:
        directives.append("Extract every condition-bound numeric observation, including non-Pr/2Pr device metrics.")
    if "reliability" in profiles:
        directives.append("Populate reliability_events for every cycle/time/stress-dependent change; do not leave it only as a property.")
    if "process_phase" in profiles:
        directives.append("Populate ordered process_steps and phase evidence whenever explicitly stated.")
    if "mechanism_computation" in profiles:
        directives.append("Populate mechanisms, computations, and directional relations for explicit driver-effect or calculated claims.")
    if "device_application" in profiles:
        directives.append("Populate devices and applications with their condition-bound figures of merit.")
    if "table" in profiles or "figure_caption" in profiles:
        directives.append("Treat each row/series as a separate arm and preserve whether it is primary or cited secondary evidence.")
    return "\n".join(f"- {item}" for item in directives) or "- Apply the general hafnia evidence contract."


def infer_extraction_profiles(text: str, section: str | None = None) -> list[str]:
    lowered = text.lower()
    profiles: list[str] = []
    pattern_groups = {
        "measurement": r"\b(?:2\s*pr|p\s*r|polarization|coercive|leakage|memory\s+window|pund)\b",
        "reliability": r"\b(?:endurance|fatigue|wake[- ]?up|retention|imprint|breakdown|recovery|variability)\b",
        "process_phase": r"\b(?:ald|pld|sputter(?:ing)?|anneal(?:ing)?|rta|pma|pda|phase|pca2?1|orthorhombic|monoclinic|tetragonal)\b",
        "mechanism_computation": r"\b(?:dft|first[- ]principles|molecular\s+dynamics|phase[- ]field|oxygen\s+vacanc(?:y|ies)|energy\s+barrier|strain|carrier\s+doping)\b",
        "device_application": r"\b(?:fefet|ftj|feram|memrist(?:or|ive)?|neuromorphic|synaptic|capacitor)\b",
    }
    for profile, pattern in pattern_groups.items():
        if re.search(pattern, lowered):
            profiles.append(profile)
    if section in {"table", "figure_caption"}:
        profiles.insert(0, str(section))
    return list(dict.fromkeys(profiles)) or ["general_hafnia"]


def estimate_chunk_cost_units(text: str) -> dict[str, int]:
    words = len(text.split())
    chars = len(text)
    rough_tokens = max(1, chars // 4)
    return {"chars": chars, "words": words, "rough_tokens": rough_tokens}


def extract_chunk_with_llm(row, model: str | None = None, timeout: float | None = None) -> LLMExtractionOutcome:
    if not llm_is_configured():
        return LLMExtractionOutcome(result=None, used_llm=False, error_message="LLM API key is not set")

    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - environment specific
        return LLMExtractionOutcome(result=None, used_llm=False, error_message=f"OpenAI SDK unavailable: {exc}")

    settings = get_settings()
    selected_model = model or settings.llm_model
    request_timeout = timeout or settings.llm_timeout_seconds
    api_key = get_llm_api_key()
    base_url, _ = normalize_base_url(settings.llm_provider, settings.llm_base_url)
    client_kwargs: dict[str, Any] = {"api_key": api_key, "timeout": request_timeout}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)

    try:
        if settings.llm_provider == "dashscope":
            return _extract_chunk_with_chat_completions(
                client=client,
                row=row,
                model=selected_model,
                timeout=request_timeout,
                max_tokens=settings.llm_max_tokens,
                enable_thinking=settings.llm_enable_thinking,
            )
        response = client.responses.parse(
            model=selected_model,
            instructions=load_prompt(),
            input=build_user_input(row),
            text_format=HfO2ExtractionResult,
            temperature=0,
            max_output_tokens=settings.llm_max_tokens,
            timeout=request_timeout,
        )
        parsed = response.output_parsed
        if parsed is None:
            return LLMExtractionOutcome(
                result=None,
                used_llm=True,
                error_message="LLM response did not contain parsed structured output",
            )

        # The source identifiers are controlled by the pipeline, not by the model.
        data = parsed.model_dump()
        data["paper_id"] = row["paper_id"]
        data["pdf_id"] = row["pdf_id"]
        data["chunk_id"] = row["chunk_id"]
        data["page_number"] = row["page_number"]
        for evidence in data.get("evidences", []):
            evidence["paper_id"] = row["paper_id"]
            evidence["pdf_id"] = row["pdf_id"]
            evidence["chunk_id"] = row["chunk_id"]
            evidence["page_number"] = row["page_number"]
        usage = getattr(response, "usage", None)
        usage_payload = None
        if usage is not None:
            usage_payload = {
                "prompt_tokens": getattr(usage, "input_tokens", None),
                "completion_tokens": getattr(usage, "output_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
            }
        grounded = ground_extraction_result(HfO2ExtractionResult(**data), row)
        return LLMExtractionOutcome(
            result=grounded,
            used_llm=True,
            error_message=None,
            usage=usage_payload,
        )
    except Exception as exc:
        return LLMExtractionOutcome(result=None, used_llm=True, error_message=str(exc))


def _extract_chunk_with_chat_completions(
    client: Any,
    row: Any,
    model: str,
    timeout: float,
    max_tokens: int,
    enable_thinking: bool,
    allow_table_split: bool = True,
) -> LLMExtractionOutcome:
    """Structured extraction through OpenAI-compatible chat completions.

    DashScope exposes Qwen models through an OpenAI-compatible chat API. It may
    not enforce Pydantic schemas server-side, so we ask for JSON and then
    validate strictly with the local HfO2 schema.
    """
    system_prompt = (
        load_prompt()
        + "\n\nReturn only one valid JSON object matching the HfO2ExtractionResult schema. "
        + "Do not wrap the JSON in markdown. If the chunk lacks usable facts, return empty arrays."
    )
    user_input = build_user_input(row)

    def parse_response(content: str) -> HfO2ExtractionResult:
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            cleaned = cleaned.removeprefix("json").strip()
        data = json_loads_object(cleaned)
        data = coerce_llm_result_payload(data, row)
        return ground_extraction_result(HfO2ExtractionResult(**data), row)

    try:
        request_kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
            "timeout": timeout,
        }
        if enable_thinking is not None:
            request_kwargs["extra_body"] = {"enable_thinking": enable_thinking}
        try:
            response = client.chat.completions.create(**request_kwargs)
        except Exception as first_exc:
            error_text = str(first_exc)
            if "response_format" in error_text:
                request_kwargs.pop("response_format", None)
                response = client.chat.completions.create(**request_kwargs)
            elif "enable_thinking" in error_text or "extra_body" in error_text:
                request_kwargs.pop("extra_body", None)
                response = client.chat.completions.create(**request_kwargs)
            else:
                raise
        content = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        usage_payload = None
        if usage is not None:
            usage_payload = {
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
            }
        finish_reason = getattr(response.choices[0], "finish_reason", None)
        try:
            parsed = parse_response(content)
        except Exception as parse_exc:
            if finish_reason == "length" and max_tokens < 8000:
                retry_kwargs = dict(request_kwargs)
                retry_kwargs["max_tokens"] = min(8000, max_tokens + 2000)
                retry_kwargs["messages"] = [
                    request_kwargs["messages"][0],
                    {
                        "role": "user",
                        "content": (
                            user_input
                            + "\n\nThe previous JSON was truncated. Return a complete compact object; "
                            + "omit null keys, duplicate evidence, commentary, and lower-priority repetitions."
                        ),
                    },
                ]
                try:
                    retry_response = client.chat.completions.create(**retry_kwargs)
                    retry_content = retry_response.choices[0].message.content or ""
                    retry_usage = getattr(retry_response, "usage", None)
                    retry_usage_payload = None
                    if retry_usage is not None:
                        retry_usage_payload = {
                            "prompt_tokens": getattr(retry_usage, "prompt_tokens", None),
                            "completion_tokens": getattr(retry_usage, "completion_tokens", None),
                            "total_tokens": getattr(retry_usage, "total_tokens", None),
                        }
                    usage_payload = _combine_usage(usage_payload, retry_usage_payload)
                    parsed = parse_response(retry_content)
                    parsed = _append_result_warning(parsed, "adaptive_length_retry:1")
                except Exception as retry_exc:
                    if allow_table_split:
                        segmented = _extract_table_segments(
                            client=client,
                            row=row,
                            model=model,
                            timeout=timeout,
                            max_tokens=max_tokens,
                            enable_thinking=enable_thinking,
                        )
                        usage_payload = _combine_usage(usage_payload, segmented.usage)
                        if segmented.result is not None:
                            return LLMExtractionOutcome(
                                result=segmented.result,
                                used_llm=True,
                                usage=usage_payload,
                            )
                    return LLMExtractionOutcome(
                        result=None,
                        used_llm=True,
                        error_message=(
                            f"Structured response parse failed after adaptive length retry: {retry_exc}; "
                            f"first_error={parse_exc}; response_chars={len(content)}"
                        ),
                        usage=usage_payload,
                    )
            else:
                return LLMExtractionOutcome(
                    result=None,
                    used_llm=True,
                    error_message=(
                        f"Structured response parse failed: {parse_exc}; "
                        f"finish_reason={finish_reason or 'unknown'}; response_chars={len(content)}"
                    ),
                    usage=usage_payload,
                )
        repair_targets = _specialist_repair_targets(parsed, row)
        if repair_targets:
            repair_result, repair_usage, repair_error = _run_specialist_repair(
                client=client,
                row=row,
                model=model,
                timeout=timeout,
                max_tokens=max_tokens,
                targets=repair_targets,
            )
            usage_payload = _combine_usage(usage_payload, repair_usage)
            if repair_result is not None:
                parsed = _merge_specialist_result(parsed, repair_result, repair_targets)
            if repair_error:
                parsed = _append_result_warning(parsed, f"specialist_repair_failed:{repair_error[:240]}")
        return LLMExtractionOutcome(result=parsed, used_llm=True, usage=usage_payload)
    except Exception as exc:
        return LLMExtractionOutcome(result=None, used_llm=True, error_message=str(exc))


def _extract_table_segments(
    *,
    client: Any,
    row: Any,
    model: str,
    timeout: float,
    max_tokens: int,
    enable_thinking: bool,
) -> LLMExtractionOutcome:
    segments = _table_row_segments(row)
    if len(segments) < 2:
        return LLMExtractionOutcome(
            result=None,
            used_llm=True,
            error_message="table is not suitable for row segmentation",
        )

    results: list[HfO2ExtractionResult] = []
    combined_usage: dict[str, int | None] | None = None
    failures: list[str] = []
    for index, segment in enumerate(segments, start=1):
        outcome = _extract_chunk_with_chat_completions(
            client=client,
            row=segment,
            model=model,
            timeout=timeout,
            max_tokens=min(max_tokens, 4000),
            enable_thinking=enable_thinking,
            allow_table_split=False,
        )
        combined_usage = _combine_usage(combined_usage, outcome.usage)
        if outcome.result is None:
            failures.append(f"segment_{index}:{outcome.error_message or 'unknown error'}")
            continue
        results.append(outcome.result)

    if not results:
        return LLMExtractionOutcome(
            result=None,
            used_llm=True,
            error_message="; ".join(failures)[:1000],
            usage=combined_usage,
        )
    merged = _merge_extraction_results(results)
    merged = _append_result_warning(merged, f"table_segmented_extraction:{len(segments)}")
    if failures:
        merged = _append_result_warning(merged, f"table_segment_failures:{len(failures)}")
    return LLMExtractionOutcome(result=merged, used_llm=True, usage=combined_usage)


def _table_row_segments(row: Any, rows_per_segment: int = 4) -> list[dict[str, Any]]:
    text = str(_row_get(row, "text", ""))
    section = str(_row_get(row, "section", "")).lower()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if section != "table" or rows_per_segment < 1 or len(lines) < 4:
        return []

    header_index = next((index for index, line in enumerate(lines) if "|" in line), None)
    if header_index is None:
        return []
    prefix = lines[: header_index + 1]
    data_lines = [line for line in lines[header_index + 1 :] if "|" in line]
    if len(data_lines) <= rows_per_segment:
        return []

    try:
        base = dict(row)
    except Exception:
        base = {
            key: _row_get(row, key)
            for key in (
                "paper_id",
                "pdf_id",
                "chunk_id",
                "page_number",
                "section",
                "paper_type",
                "is_review",
                "preceding_text",
                "following_text",
            )
        }
    segments: list[dict[str, Any]] = []
    for start in range(0, len(data_lines), rows_per_segment):
        segment = dict(base)
        segment["text"] = "\n".join([*prefix, *data_lines[start : start + rows_per_segment]])
        segments.append(segment)
    return segments


def _merge_extraction_results(results: list[HfO2ExtractionResult]) -> HfO2ExtractionResult:
    if not results:
        raise ValueError("at least one extraction result is required")
    data = results[0].model_dump(mode="json")
    list_fields = (
        "materials",
        "samples",
        "phases",
        "properties",
        "devices",
        "process_steps",
        "reliability_events",
        "mechanisms",
        "computations",
        "applications",
        "relations",
        "evidences",
    )
    for field in list_fields:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for result in results:
            for item in result.model_dump(mode="json").get(field, []):
                key = json.dumps(item, ensure_ascii=False, sort_keys=True)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)
        data[field] = merged
    data["warnings"] = list(
        dict.fromkeys(
            warning
            for result in results
            for warning in result.warnings
            if str(warning).strip()
        )
    )
    return HfO2ExtractionResult(**data)


def _specialist_repair_targets(result: HfO2ExtractionResult, row: Any) -> list[str]:
    text = str(_row_get(row, "text", ""))
    profiles = infer_extraction_profiles(text, _row_get(row, "section"))
    targets: list[str] = []
    if "reliability" in profiles and not result.reliability_events:
        targets.append("reliability_events")
    if "mechanism_computation" in profiles:
        causal_signal = bool(
            re.search(
                r"\b(?:due to|because|resulting in|leads? to|causes?|promotes?|suppresses?|"
                r"stabilizes?|destabilizes?|by adjusting|we propose|mechanism)\b",
                text,
                re.IGNORECASE,
            )
        )
        computation_signal = bool(
            re.search(
                r"\b(?:dft|first[- ]principles|dfpt|neb|aimd|molecular dynamics|"
                r"phase[- ]field|landau|tcad|machine[- ]learning potential)\b",
                text,
                re.IGNORECASE,
            )
        )
        if causal_signal and not result.mechanisms:
            targets.append("mechanisms")
        if computation_signal and not result.computations:
            targets.append("computations")
        if causal_signal and not result.relations:
            targets.append("relations")
    return targets


def _run_specialist_repair(
    *,
    client: Any,
    row: Any,
    model: str,
    timeout: float,
    max_tokens: int,
    targets: list[str],
) -> tuple[HfO2ExtractionResult | None, dict[str, int | None] | None, str | None]:
    target_contracts = {
        "reliability_events": (
            "reliability_events items: material_ref, optional sample_ref, phenomenon, metric_name, "
            "initial_value/final_value/unit, cycle_count or elapsed_time_s, stress/read conditions, "
            "trend, failure_mode, confidence, evidence_scope, evidence_text. Split comparison arms."
        ),
        "mechanisms": (
            "mechanisms items: material_ref, optional sample_ref, mechanism_type, driver, outcome, "
            "affected_entity, relation_nature, direction, confidence, evidence_scope, evidence_text."
        ),
        "computations": (
            "computations items: material_ref, method_family, code/model/functional when stated, "
            "structure/defect/condition, descriptor_name, value/unit/reference_state, conclusion, "
            "confidence, evidence_text."
        ),
        "relations": (
            "relations items: subject_ref, predicate, object_ref, condition, confidence, evidence_text. "
            "The quote must contain or unambiguously bind both subject and object."
        ),
    }
    system_prompt = "\n".join(
        [
            "You are a specialist repair pass for HfO2/HZO literature extraction.",
            f"Return one compact JSON object containing only these requested arrays: {', '.join(targets)}.",
            "Return an empty requested array when the focal chunk does not explicitly support it.",
            "Every item must copy evidence_text verbatim from FOCAL_CHUNK. Context is disambiguation only.",
            "Never emit null-valued keys. Do not emit materials, properties, evidences, or long explanations.",
            "Do not turn correlation or an application statement into a causal mechanism.",
            *[target_contracts[target] for target in targets],
        ]
    )
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": build_user_input(row)},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "max_tokens": max(800, min(max_tokens, 1800)),
        "timeout": timeout,
        "extra_body": {"enable_thinking": False},
    }
    try:
        response = client.chat.completions.create(**request_kwargs)
        content = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        usage_payload = None
        if usage is not None:
            usage_payload = {
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
                "specialist_calls": 1,
            }
        payload = coerce_llm_result_payload(json_loads_object(content), row)
        repaired = ground_extraction_result(HfO2ExtractionResult(**payload), row)
        return repaired, usage_payload, None
    except Exception as exc:
        return None, None, str(exc)


def _combine_usage(
    first: dict[str, int | None] | None,
    second: dict[str, int | None] | None,
) -> dict[str, int | None] | None:
    if not first and not second:
        return None
    combined: dict[str, int | None] = {}
    for key in ["prompt_tokens", "completion_tokens", "total_tokens", "specialist_calls"]:
        combined[key] = int((first or {}).get(key) or 0) + int((second or {}).get(key) or 0)
    return combined


def _merge_specialist_result(
    base: HfO2ExtractionResult,
    repair: HfO2ExtractionResult,
    targets: list[str],
) -> HfO2ExtractionResult:
    data = base.model_dump(mode="json")
    repaired = repair.model_dump(mode="json")
    for field in targets:
        if not data.get(field) and repaired.get(field):
            data[field] = repaired[field]
    evidence_seen = {
        _normalize_evidence_text(str(item.get("evidence_text") or ""))
        for item in data.get("evidences", [])
    }
    for item in repaired.get("evidences", []):
        key = _normalize_evidence_text(str(item.get("evidence_text") or ""))
        if key and key not in evidence_seen:
            data["evidences"].append(item)
            evidence_seen.add(key)
    data["warnings"] = list(dict.fromkeys([*(data.get("warnings") or []), *(repaired.get("warnings") or [])]))
    return HfO2ExtractionResult(**data)


def _append_result_warning(result: HfO2ExtractionResult, warning: str) -> HfO2ExtractionResult:
    data = result.model_dump(mode="json")
    data["warnings"] = list(dict.fromkeys([*(data.get("warnings") or []), warning]))
    return HfO2ExtractionResult(**data)


def json_loads_object(content: str) -> dict[str, Any]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        data = json.loads(content[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM response JSON root must be an object")
    return data


def coerce_llm_result_payload(data: dict[str, Any], row: Any) -> dict[str, Any]:
    """Normalize common OpenAI-compatible model JSON variants into our schema.

    DashScope/Qwen models are good at semantic extraction, but the chat endpoint
    does not enforce the Pydantic schema server-side. This adapter keeps the
    model useful while still letting local strict validation be the final gate.
    """
    text = str(_row_get(row, "text", ""))
    excluded_reason = non_evidence_reason(text)
    if excluded_reason:
        return _empty_coerced_result(row, f"quality_gate_excluded:{excluded_reason}")
    materials = [_coerce_material(item, text) for item in _as_list(_pick(data, "materials", "material"))]
    materials = [item for item in materials if item is not None]

    sample_sources = _as_list(_pick(data, "samples", "sample", "thin_film_samples", "processes"))
    for material_like in _as_list(_pick(data, "materials", "material")):
        if isinstance(material_like, dict) and any(
            key in material_like
            for key in [
                "film_thickness_nm",
                "thickness_nm",
                "deposition_method",
                "annealing_temperature_c",
                "anneal_temperature_c",
                "top_electrode",
                "bottom_electrode",
                "device_stack",
            ]
        ):
            sample_sources.append(material_like)
    material_ref = (
        materials[0]["canonical_name"]
        if materials
        else _pick(data, "material_ref", "material_name", "canonical_name") or "HfO2"
    )
    samples = [
        _coerce_sample(item, text, str(material_ref))
        for item in sample_sources
        if isinstance(item, dict)
    ]
    samples = [item for item in samples if item is not None]

    phases = [
        _coerce_phase(item, text, str(material_ref))
        for item in _as_list(_pick(data, "phases", "phase", "phase_structures", "structures"))
        if isinstance(item, dict)
    ]
    phases = [item for item in phases if item is not None]

    property_sources = _as_list(
        _pick(data, "properties", "property", "ferroelectric_properties", "facts", "measurements")
    )
    properties = [
        _coerce_property(item, text, str(material_ref))
        for item in property_sources
        if isinstance(item, dict)
    ]
    properties = [item for item in properties if item is not None]

    devices = [
        _coerce_device(item, text)
        for item in _as_list(_pick(data, "devices", "device"))
        if isinstance(item, dict)
    ]
    devices = [item for item in devices if item is not None]

    process_steps = [
        _coerce_process_step(item, text, str(material_ref))
        for item in _as_list(_pick(data, "process_steps", "fabrication_steps", "synthesis_steps"))
        if isinstance(item, dict)
    ]
    process_steps = [item for item in process_steps if item is not None]

    reliability_events = [
        _coerce_reliability(item, text, str(material_ref))
        for item in _as_list(_pick(data, "reliability_events", "reliability", "degradation_events"))
        if isinstance(item, dict)
    ]
    reliability_events = [item for item in reliability_events if item is not None]

    mechanisms = [
        _coerce_mechanism(item, text, str(material_ref))
        for item in _as_list(_pick(data, "mechanisms", "mechanism_claims"))
        if isinstance(item, dict)
    ]
    mechanisms = [item for item in mechanisms if item is not None]

    computations = [
        _coerce_computation(item, text, str(material_ref))
        for item in _as_list(_pick(data, "computations", "computational_observations", "theoretical_insights"))
        if isinstance(item, dict)
    ]
    computations = [item for item in computations if item is not None]

    applications = [
        _coerce_application(item, text, str(material_ref))
        for item in _as_list(_pick(data, "applications", "application_claims"))
        if isinstance(item, dict)
    ]
    applications = [item for item in applications if item is not None]
    if not applications:
        applications = _infer_explicit_applications(text, str(material_ref), devices)

    relations = [
        _coerce_relation(item, text)
        for item in _as_list(_pick(data, "relations", "causal_relations", "comparisons"))
        if isinstance(item, dict)
    ]
    relations = [item for item in relations if item is not None]

    default_scope = _default_evidence_scope(row)
    for items in (properties, reliability_events, mechanisms):
        for item in items:
            if default_scope == "cited_secondary":
                item["evidence_scope"] = default_scope
            elif item.get("evidence_scope") in {None, "", "unknown"} and default_scope != "unknown":
                item["evidence_scope"] = default_scope

    evidences = [
        _coerce_evidence(item, row)
        for item in _as_list(_pick(data, "evidences", "evidence", "sources"))
        if isinstance(item, dict)
    ]
    if default_scope == "cited_secondary":
        for evidence in evidences:
            evidence["evidence_scope"] = default_scope
    evidence_texts = {
        _normalize_evidence_text(str(item.get("evidence_text") or ""))
        for item in evidences
    }
    role_items = {
        "material": materials,
        "process": [*samples, *process_steps],
        "phase": phases,
        "measurement": properties,
        "application": [*devices, *applications],
        "reliability": reliability_events,
        "mechanism": mechanisms,
        "computation": computations,
        "other": relations,
    }
    for role, items in role_items.items():
        for item in items:
            evidence_text = str(item.get("evidence_text") or "")
            normalized_text = _normalize_evidence_text(evidence_text)
            if not normalized_text or normalized_text in evidence_texts:
                continue
            evidences.append(
                {
                    "paper_id": _row_get(row, "paper_id"),
                    "pdf_id": _row_get(row, "pdf_id"),
                    "chunk_id": _row_get(row, "chunk_id"),
                    "page_number": _row_get(row, "page_number"),
                    "evidence_text": evidence_text,
                    "source_type": _source_type_from_row(row),
                    "evidence_scope": _resolved_evidence_scope(item, row),
                    "evidence_role": role,
                }
            )
            evidence_texts.add(normalized_text)

    warnings = _as_list(_pick(data, "warnings", "notes", "uncertainties"))
    return {
        "paper_id": _row_get(row, "paper_id"),
        "pdf_id": _row_get(row, "pdf_id"),
        "chunk_id": _row_get(row, "chunk_id"),
        "page_number": _row_get(row, "page_number"),
        "materials": materials,
        "samples": samples,
        "phases": phases,
        "properties": properties,
        "devices": devices,
        "process_steps": process_steps,
        "reliability_events": reliability_events,
        "mechanisms": mechanisms,
        "computations": computations,
        "applications": applications,
        "relations": relations,
        "evidences": evidences,
        "warnings": [str(item) for item in warnings if str(item).strip()],
    }


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        value = row[key]
    except Exception:
        value = default
    return default if value is None else value


def non_evidence_reason(text: str) -> str | None:
    """Detect biographies and bibliography fragments that cannot support KG facts."""
    normalized = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    if not normalized:
        return "blank"

    biography_signals = [
        "received his", "received her", "ph.d.", "b.s.", "m.s.",
        "research interests", "is currently", "currently an", "joined ",
        "postdoc", "post-doc", "professor at", "degree from",
    ]
    if sum(signal in normalized for signal in biography_signals) >= 3:
        return "author_biography"

    year_count = len(re.findall(r"\b(?:19|20)\d{2}\b", normalized))
    citation_count = len(re.findall(r"\bet\s+al\.?\b|\bvol\.?\s*\d+|\bpp?\.\s*\d+", normalized))
    bibliography_terms = sum(
        token in normalized
        for token in [
            "advanced functional materials", "applied physics letters",
            "physical review", "ieee electron", "nanotechnology",
            "doi.org", "http://", "https://",
        ]
    )
    measurement_signal = bool(
        re.search(
            r"\b(?:2\s*pr|p\s*r|e\s*c|hzo|hfo2|hafnia|orthorhombic|monoclinic|"
            r"ald|pld|anneal|retention|endurance)\b[^.\n]{0,90}?"
            r"(?:\d+(?:\.\d+)?\s*(?:nm|v|mv|mv/cm|kv/cm|mv/cm|uc/cm|%|cycles?))",
            normalized,
        )
    )
    if year_count >= 4 and citation_count + bibliography_terms >= 8:
        return "bibliography_fragment"
    if not measurement_signal and year_count >= 4 and citation_count + bibliography_terms >= 4:
        return "bibliography_fragment"
    if (
        not measurement_signal
        and normalized.startswith("table")
        and len(normalized) < 900
        and year_count >= 2
        and citation_count + bibliography_terms >= 2
    ):
        return "bibliography_fragment"
    if (
        not measurement_signal
        and normalized.startswith("table")
        and len(normalized) < 500
        and re.search(r"\b(?:ieee|journal|letters|publishing|review)\b", normalized)
        and re.search(r"\b\d{2,4}\s+\d{3,5}(?:-\d{3,5})?\b", normalized)
    ):
        return "citation_title_only"
    return None


def is_cited_comparison_table(text: str, section: str | None = "table") -> bool:
    """Detect table rows that summarize separately cited literature records."""
    if str(section or "").lower() != "table":
        return False
    lines = [line.strip() for line in str(text or "").splitlines() if "|" in line]
    if len(lines) < 3:
        return False
    cited_rows = sum(
        1
        for line in lines
        if re.search(r"(?:\[\s*\d{1,3}\s*\]|\b\d{1,3})\s*$", line)
    )
    return cited_rows >= 3 and cited_rows / len(lines) >= 0.5


def _empty_coerced_result(row: Any, warning: str) -> dict[str, Any]:
    return {
        "paper_id": _row_get(row, "paper_id"),
        "pdf_id": _row_get(row, "pdf_id"),
        "chunk_id": _row_get(row, "chunk_id"),
        "page_number": _row_get(row, "page_number"),
        "materials": [],
        "samples": [],
        "phases": [],
        "properties": [],
        "devices": [],
        "process_steps": [],
        "reliability_events": [],
        "mechanisms": [],
        "computations": [],
        "applications": [],
        "relations": [],
        "evidences": [],
        "warnings": [warning],
    }


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _pick(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, "", []):
            return mapping[key]
    return None


def _evidence_from_item(item: dict[str, Any], text: str, *needles: Any) -> str:
    explicit = _pick(
        item,
        "evidence_text",
        "evidence",
        "source_text",
        "source_sentence",
        "sentence",
        "quote",
    )
    if isinstance(explicit, str) and explicit.strip():
        explicit_text = explicit.strip()
        if len(explicit_text) >= 8:
            return explicit_text[:700]
        needles = (explicit_text, *needles)

    lowered = text.lower()
    for needle in needles:
        if needle is None:
            continue
        needle_text = str(needle).strip()
        if not needle_text:
            continue
        pos = lowered.find(needle_text.lower())
        if pos != -1:
            left = max(text.rfind(".", 0, pos), text.rfind("\n", 0, pos))
            right_candidates = [
                found
                for found in [text.find(".", pos + len(needle_text)), text.find("\n", pos + len(needle_text))]
                if found != -1
            ]
            right = min(right_candidates) if right_candidates else min(len(text), pos + 260)
            evidence = re.sub(r"\s+", " ", text[left + 1 : right + 1]).strip()[:700]
            if len(evidence) >= 8:
                return evidence
    fallback = re.sub(r"\s+", " ", text[:700]).strip()
    return fallback if len(fallback) >= 8 else "Evidence context unavailable in parsed chunk."


def _coerce_material(item: Any, text: str) -> dict[str, Any] | None:
    if isinstance(item, str):
        item = {"raw_name": item}
    if not isinstance(item, dict):
        return None
    raw_name = _pick(item, "raw_name", "material_name", "name", "material", "composition", "formula")
    if not raw_name:
        return None
    canonical = _pick(item, "canonical_name", "formula", "composition", "material_name", "name") or raw_name
    family = _pick(item, "material_family", "family", "material_system", "material_type")
    dopants = _as_list(_pick(item, "dopant_elements", "dopants", "dopant", "alloying_elements"))
    dopants = [str(value) for value in dopants if str(value).strip()]
    inferred_family = _infer_material_family(str(raw_name), str(canonical), dopants)
    zr_fraction = _pick(item, "zr_fraction", "Zr_fraction", "x", "zr_content")
    return {
        "raw_name": str(raw_name),
        "canonical_name": str(canonical),
        "formula": str(_pick(item, "formula", "composition", "canonical_name") or canonical),
        "material_family": _normalize_material_family(str(family or inferred_family)),
        "base_material": str(_pick(item, "base_material") or "HfO2"),
        "dopant_elements": dopants or _infer_dopants(str(raw_name), str(canonical)),
        "dopant_concentration": _optional_string(_pick(item, "dopant_concentration", "dopant_content")),
        "zr_fraction": _optional_float(zr_fraction),
        "composition_descriptor": _optional_string(
            _pick(item, "composition_descriptor", "composition_note", "stoichiometry", "doping_strategy")
        ),
        "layer_sequence": _optional_string(_pick(item, "layer_sequence", "laminate_sequence", "superlattice_sequence")),
        "superlattice_period": _optional_string(_pick(item, "superlattice_period", "period", "bilayer_period")),
        "evidence_text": _evidence_from_item(item, text, raw_name, canonical),
    }


def _coerce_sample(item: dict[str, Any], text: str, material_ref: str) -> dict[str, Any] | None:
    sample = {
        "material_ref": str(_pick(item, "material_ref", "material_name", "material", "composition") or material_ref),
        "sample_ref": _optional_string(_pick(item, "sample_ref", "sample_id", "sample_label")),
        "sample_name": _optional_string(_pick(item, "sample_name", "sample_label")),
        "film_thickness_nm": _optional_positive_float(
            _pick(item, "film_thickness_nm", "thickness_nm", "film_thickness")
        ),
        "deposition_method": _optional_string(_pick(item, "deposition_method", "deposition", "fabrication_method")),
        "top_electrode": _optional_string(_pick(item, "top_electrode", "top_contact")),
        "bottom_electrode": _optional_string(_pick(item, "bottom_electrode", "bottom_contact")),
        "substrate": _optional_string(_pick(item, "substrate")),
        "annealing_temperature_c": _optional_float(
            _pick(item, "annealing_temperature_c", "anneal_temperature_c", "annealing_temperature")
        ),
        "annealing_time_s": _optional_float(_pick(item, "annealing_time_s", "anneal_time_s", "annealing_time")),
        "annealing_atmosphere": _optional_string(
            _pick(item, "annealing_atmosphere", "anneal_atmosphere", "atmosphere")
        ),
        "annealing_method": _optional_string(_pick(item, "annealing_method", "anneal_method", "thermal_process")),
        "oxygen_partial_pressure": _optional_string(
            _pick(item, "oxygen_partial_pressure", "po2", "p_o2", "oxygen_pressure")
        ),
        "oxygen_vacancy_context": _optional_string(
            _pick(
                item,
                "oxygen_vacancy_context",
                "oxygen_vacancies",
                "oxygen_deficiency",
                "vacancy_context",
                "defect_context",
            )
        ),
        "oxygen_reservoir": _optional_string(_pick(item, "oxygen_reservoir", "oxygen_source", "oxygen_sink")),
        "interface_layer": _optional_string(_pick(item, "interface_layer", "interlayer", "buffer_layer", "seed_layer")),
        "interface_termination": _optional_string(
            _pick(item, "interface_termination", "termination", "surface_termination")
        ),
        "device_stack": _optional_string(_pick(item, "device_stack", "stack", "electrode_stack")),
        "sample_form": _optional_string(_pick(item, "sample_form", "device_type", "form")) or "unknown",
        "growth_orientation": _optional_string(
            _pick(item, "growth_orientation", "substrate_orientation", "epitaxial_orientation")
        ),
        "strain_state": _optional_string(_pick(item, "strain_state", "strain", "stress_state")),
        "grain_size_nm": _optional_positive_float(_pick(item, "grain_size_nm", "grain_size")),
        "electrode_area": _optional_string(_pick(item, "electrode_area", "capacitor_area", "device_area")),
        "evidence_text": _evidence_from_item(item, text, _pick(item, "device_stack", "stack"), material_ref),
    }
    if not any(value for key, value in sample.items() if key not in {"material_ref", "sample_form", "evidence_text"}):
        return None
    return sample


def _coerce_phase(item: dict[str, Any], text: str, material_ref: str) -> dict[str, Any] | None:
    raw_phase = _pick(item, "phase_name", "phase", "structure", "crystal_phase")
    space_group = _pick(item, "space_group", "spacegroup")
    phase_name = _normalize_phase(str(raw_phase or space_group or "unknown"))
    return {
        "material_ref": str(_pick(item, "material_ref", "material_name", "material") or material_ref),
        "phase_name": phase_name,
        "space_group": _optional_string(space_group),
        "phase_fraction": _optional_string(_pick(item, "phase_fraction", "fraction", "phase_content")),
        "crystal_orientation": _optional_string(
            _pick(item, "crystal_orientation", "orientation", "texture", "out_of_plane_orientation")
        ),
        "domain_orientation": _optional_string(
            _pick(item, "domain_orientation", "ferroelectric_domain_orientation", "domain_texture")
        ),
        "characterization_method": _optional_string(
            _pick(item, "characterization_method", "method", "measurement_method")
        ),
        "phase_fraction_value": _fraction_float(_pick(item, "phase_fraction_value", "phase_fraction", "fraction")),
        "lattice_parameters": _optional_string(_pick(item, "lattice_parameters", "lattice_parameter")),
        "grain_size_nm": _optional_positive_float(_pick(item, "grain_size_nm", "grain_size")),
        "evidence_text": _evidence_from_item(item, text, raw_phase, space_group),
    }


def _coerce_property(item: dict[str, Any], text: str, material_ref: str) -> dict[str, Any] | None:
    raw_name = _pick(item, "raw_property_name", "property_name", "property", "name", "metric")
    enum_name = _pick(item, "property_name", "normalized_property_name")
    if enum_name not in _property_values():
        alias = normalize_property_alias(str(raw_name or enum_name or ""))
        enum_name = alias.value if alias else None
    if enum_name is None and raw_name:
        enum_name = "other_hafnia_property"
    if enum_name is None:
        return None
    raw_value = _pick(
        item,
        "value",
        "raw_value",
        "numeric_value",
        "reported_value",
        "value_text",
        "raw_value_text",
    )
    evidence = _evidence_from_item(item, text, raw_name, raw_value)
    value = _optional_float(raw_value)
    value_min = _optional_float(_pick(item, "value_min", "minimum", "min_value", "lower_bound"))
    value_max = _optional_float(_pick(item, "value_max", "maximum", "max_value", "upper_bound"))
    unit = _optional_string(_pick(item, "unit", "raw_unit", "value_unit"))
    inferred = _infer_property_observation(evidence, str(raw_name or enum_name))
    if value is None and value_min is None and value_max is None:
        value = inferred["value"]
        value_min = inferred["value_min"]
        value_max = inferred["value_max"]
    unit = unit or inferred["unit"] or _infer_unit(evidence, str(enum_name))
    raw_value_text = _optional_string(_pick(item, "raw_value_text", "value_text", "reported_value"))
    if raw_value_text is None:
        raw_value_text = inferred["raw_value_text"]
    comparison_operator = _normalize_comparison_operator(
        _pick(item, "comparison_operator", "operator", "qualifier"),
        evidence,
        value=value,
        value_min=value_min,
        value_max=value_max,
    )
    if value is not None and unit is None:
        return None
    normalized_unit = normalize_unit(unit)
    confidence = _optional_float(_pick(item, "confidence")) or 0.55
    return {
        "material_ref": str(_pick(item, "material_ref", "material_name", "material") or material_ref),
        "property_name": enum_name,
        "raw_property_name": str(raw_name or enum_name),
        "raw_value_text": raw_value_text,
        "value": value,
        "value_min": value_min,
        "value_max": value_max,
        "comparison_operator": comparison_operator,
        "unit": unit,
        "normalized_value": value,
        "normalized_unit": normalized_unit,
        "measurement_temperature": _optional_string(_pick(item, "measurement_temperature")),
        "measurement_frequency": _optional_string(_pick(item, "measurement_frequency")),
        "measurement_method": _optional_string(_pick(item, "measurement_method", "protocol")),
        "waveform": _optional_string(_pick(item, "waveform")),
        "pulse_width": _optional_string(_pick(item, "pulse_width", "pulse_duration")),
        "sweep_rate": _optional_string(_pick(item, "sweep_rate")),
        "read_voltage": _optional_string(_pick(item, "read_voltage")),
        "stress_voltage": _optional_string(_pick(item, "stress_voltage", "cycling_voltage")),
        "electric_field": _optional_string(_pick(item, "electric_field", "measurement_field")),
        "measurement_state": _optional_string(
            _pick(item, "measurement_state", "wake_up_state", "cycling_state", "endurance_state")
        ),
        "cycle_number": _optional_float(_pick(item, "cycle_number", "cycles", "cycling_number")),
        "device_type": _optional_string(_pick(item, "device_type")),
        "uncertainty": _optional_string(_pick(item, "uncertainty", "error_bar")),
        "statistical_scope": _optional_string(_pick(item, "statistical_scope", "sample_count", "statistics")),
        "evidence_scope": _normalize_evidence_scope(_pick(item, "evidence_scope", "source_scope")),
        "confidence": max(0.0, min(1.0, confidence)),
        "evidence_text": evidence,
        "is_reported_value": bool(_pick(item, "is_reported_value") if "is_reported_value" in item else True),
        "is_derived_value": bool(_pick(item, "is_derived_value") if "is_derived_value" in item else False),
        "derivation_rule": _optional_string(_pick(item, "derivation_rule")),
        "review_status": _optional_string(_pick(item, "review_status")) or "pending",
    }


def _coerce_device(item: dict[str, Any], text: str) -> dict[str, Any] | None:
    device_type = _pick(item, "device_type", "type", "name")
    if not device_type:
        return None
    return {
        "device_type": str(device_type),
        "device_stack": _optional_string(_pick(item, "device_stack", "stack")),
        "channel_material": _optional_string(_pick(item, "channel_material", "channel")),
        "gate_stack": _optional_string(_pick(item, "gate_stack")),
        "area": _optional_string(_pick(item, "area", "capacitor_area", "device_area")),
        "evidence_text": _evidence_from_item(item, text, device_type, _pick(item, "device_stack", "stack")),
    }


def _coerce_evidence(item: dict[str, Any], row: Any) -> dict[str, Any]:
    text = str(_row_get(row, "text", ""))
    evidence = _evidence_from_item(item, text)
    source_type = str(_pick(item, "source_type", "type") or "text")
    if source_type not in {"text", "table", "figure_caption", "abstract", "metadata", "manual"}:
        source_type = "text"
    return {
        "paper_id": _row_get(row, "paper_id"),
        "pdf_id": _row_get(row, "pdf_id"),
        "chunk_id": _row_get(row, "chunk_id"),
        "page_number": _row_get(row, "page_number"),
        "evidence_text": evidence,
        "source_type": source_type,
        "evidence_scope": _normalize_evidence_scope(_pick(item, "evidence_scope", "source_scope")),
        "evidence_role": _normalize_evidence_role(_pick(item, "evidence_role", "role")),
        "figure_or_table_id": _optional_string(_pick(item, "figure_or_table_id", "figure_id", "table_id")),
    }


def _source_type_from_row(row: Any) -> str:
    section = str(_row_get(row, "section", "")).lower()
    if section == "table":
        return "table"
    if section == "figure_caption":
        return "figure_caption"
    if section == "abstract":
        return "abstract"
    return "text"


def _default_evidence_scope(row: Any) -> str:
    if bool(_row_get(row, "is_review", False)):
        return "review_secondary"
    paper_type = str(_row_get(row, "paper_type", "")).lower()
    if paper_type in {"computational", "theoretical"}:
        return "primary_computation"
    section = str(_row_get(row, "section", "")).lower()
    if is_cited_comparison_table(str(_row_get(row, "text", "")), section):
        return "cited_secondary"
    if section in {"results", "methods", "experimental", "table", "figure_caption"}:
        return "primary_experiment"
    return "unknown"


def _resolved_evidence_scope(item: dict[str, Any], row: Any) -> str:
    default_scope = _default_evidence_scope(row)
    if default_scope == "cited_secondary":
        return default_scope
    scope = str(item.get("evidence_scope") or "unknown")
    return default_scope if scope == "unknown" else scope


def _infer_explicit_applications(
    text: str,
    material_ref: str,
    devices: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    patterns = [
        (r"\bneuromorphic(?:\s+(?:computing|paradigms?))?\b", "neuromorphic computing"),
        (r"\bin[- ]memory computing\b", "in-memory computing"),
        (r"\bcompute[- ]in[- ]memory\b", "compute-in-memory"),
        (r"\bsynaptic weight modulation\b", "synaptic weight modulation"),
    ]
    device_type = str(devices[0].get("device_type")) if devices else None
    applications: list[dict[str, Any]] = []
    for pattern, label in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        evidence = _evidence_from_item({}, text, match.group(0))
        applications.append(
            {
                "material_ref": material_ref,
                "device_type": device_type,
                "application": label,
                "target_metric": None,
                "value": None,
                "unit": None,
                "operating_condition": None,
                "confidence": 0.7,
                "evidence_text": evidence,
            }
        )
    return applications


def _coerce_process_step(item: dict[str, Any], text: str, material_ref: str) -> dict[str, Any] | None:
    step_type = _normalize_process_step(_pick(item, "step_type", "type", "action"))
    evidence = _evidence_from_item(item, text, _pick(item, "method"), _pick(item, "action"))
    return {
        "material_ref": str(_pick(item, "material_ref", "material") or material_ref),
        "sample_ref": _optional_string(_pick(item, "sample_ref", "sample_id")),
        "step_order": _optional_int(_pick(item, "step_order", "order", "step")),
        "step_type": step_type,
        "method": _optional_string(_pick(item, "method", "technique")),
        "precursors": [str(value) for value in _as_list(_pick(item, "precursors", "precursor")) if str(value).strip()],
        "temperature_c": _optional_float(_pick(item, "temperature_c", "temperature")),
        "time_s": _optional_float(_pick(item, "time_s", "time", "duration_s")),
        "atmosphere": _optional_string(_pick(item, "atmosphere")),
        "pressure": _optional_string(_pick(item, "pressure")),
        "cycle_count": _optional_float(_pick(item, "cycle_count", "cycles")),
        "condition": _optional_string(_pick(item, "condition", "conditions")),
        "evidence_text": evidence,
    }


def _coerce_reliability(item: dict[str, Any], text: str, material_ref: str) -> dict[str, Any] | None:
    raw = _pick(item, "phenomenon", "reliability_type", "event", "metric_name")
    if not raw:
        return None
    return {
        "material_ref": str(_pick(item, "material_ref", "material") or material_ref),
        "sample_ref": _optional_string(_pick(item, "sample_ref", "sample_id")),
        "phenomenon": _normalize_reliability(raw),
        "metric_name": _optional_string(_pick(item, "metric_name", "metric")),
        "initial_value": _optional_float(_pick(item, "initial_value", "before_value")),
        "final_value": _optional_float(_pick(item, "final_value", "after_value", "value")),
        "unit": _optional_string(_pick(item, "unit")),
        "cycle_count": _optional_float(_pick(item, "cycle_count", "cycles", "endurance_cycles")),
        "elapsed_time_s": _optional_float(_pick(item, "elapsed_time_s", "retention_time_s", "time_s")),
        "electric_field": _optional_string(_pick(item, "electric_field", "stress_field")),
        "stress_voltage": _optional_string(_pick(item, "stress_voltage", "cycling_voltage")),
        "pulse_width": _optional_string(_pick(item, "pulse_width", "pulse_duration")),
        "frequency": _optional_string(_pick(item, "frequency")),
        "temperature": _optional_string(_pick(item, "temperature")),
        "trend": _normalize_trend(_pick(item, "trend", "direction")),
        "failure_mode": _optional_string(_pick(item, "failure_mode", "mechanism")),
        "device_type": _optional_string(_pick(item, "device_type")),
        "confidence": _bounded_confidence(_pick(item, "confidence")),
        "evidence_scope": _normalize_evidence_scope(_pick(item, "evidence_scope", "source_scope")),
        "evidence_text": _evidence_from_item(item, text, raw, _pick(item, "value")),
    }


def _coerce_mechanism(item: dict[str, Any], text: str, material_ref: str) -> dict[str, Any] | None:
    mechanism = _pick(item, "mechanism_type", "mechanism_name", "mechanism", "name")
    outcome = _pick(item, "outcome", "effect", "conclusion")
    if not mechanism or not outcome:
        return None
    return {
        "material_ref": str(_pick(item, "material_ref", "material") or material_ref),
        "sample_ref": _optional_string(_pick(item, "sample_ref", "sample_id")),
        "mechanism_type": str(mechanism),
        "driver": _optional_string(_pick(item, "driver", "cause", "input_variable")),
        "outcome": str(outcome),
        "affected_entity": _optional_string(_pick(item, "affected_entity", "target", "affected_property")),
        "relation_nature": _normalize_relation_nature(_pick(item, "relation_nature", "claim_type")),
        "direction": _normalize_direction(_pick(item, "direction")),
        "confidence": _bounded_confidence(_pick(item, "confidence")),
        "evidence_scope": _normalize_evidence_scope(_pick(item, "evidence_scope", "source_scope")),
        "evidence_text": _evidence_from_item(item, text, mechanism, outcome),
    }


def _coerce_computation(item: dict[str, Any], text: str, material_ref: str) -> dict[str, Any] | None:
    descriptor = _pick(item, "descriptor_name", "descriptor", "property_name", "metric")
    method = _pick(item, "method_family", "method", "model_or_method")
    if not descriptor or not method:
        return None
    return {
        "material_ref": str(_pick(item, "material_ref", "material") or material_ref),
        "method_family": _normalize_computation_method(method),
        "code_or_model": _optional_string(_pick(item, "code_or_model", "code", "model")),
        "functional": _optional_string(_pick(item, "functional", "xc_functional")),
        "pseudopotential": _optional_string(_pick(item, "pseudopotential")),
        "structure_or_phase": _optional_string(_pick(item, "structure_or_phase", "phase", "structure")),
        "supercell": _optional_string(_pick(item, "supercell")),
        "kpoint_mesh": _optional_string(_pick(item, "kpoint_mesh", "kpoints")),
        "defect_type": _optional_string(_pick(item, "defect_type", "defect")),
        "charge_state": _optional_string(_pick(item, "charge_state")),
        "strain": _optional_string(_pick(item, "strain")),
        "electric_field": _optional_string(_pick(item, "electric_field")),
        "temperature": _optional_string(_pick(item, "temperature")),
        "descriptor_name": str(descriptor),
        "value": _optional_float(_pick(item, "value", "descriptor_value")),
        "unit": _optional_string(_pick(item, "unit")),
        "reference_state": _optional_string(_pick(item, "reference_state", "reference")),
        "conclusion": _optional_string(_pick(item, "conclusion", "design_relevance")),
        "confidence": _bounded_confidence(_pick(item, "confidence")),
        "evidence_text": _evidence_from_item(item, text, descriptor, _pick(item, "value")),
    }


def _coerce_application(item: dict[str, Any], text: str, material_ref: str) -> dict[str, Any] | None:
    application = _pick(item, "application", "use_case", "target_application")
    if not application:
        return None
    return {
        "material_ref": str(_pick(item, "material_ref", "material") or material_ref),
        "device_type": _optional_string(_pick(item, "device_type", "device")),
        "application": str(application),
        "target_metric": _optional_string(_pick(item, "target_metric", "metric")),
        "value": _optional_float(_pick(item, "value")),
        "unit": _optional_string(_pick(item, "unit")),
        "operating_condition": _optional_string(_pick(item, "operating_condition", "condition")),
        "confidence": _bounded_confidence(_pick(item, "confidence")),
        "evidence_text": _evidence_from_item(item, text, application, _pick(item, "value")),
    }


def _coerce_relation(item: dict[str, Any], text: str) -> dict[str, Any] | None:
    subject = _pick(item, "subject_ref", "subject", "cause")
    obj = _pick(item, "object_ref", "object", "effect")
    predicate = _normalize_predicate(_pick(item, "predicate", "relation", "direction"))
    if not subject or not obj or not predicate:
        return None
    return {
        "subject_ref": str(subject),
        "predicate": predicate,
        "object_ref": str(obj),
        "condition": _optional_string(_pick(item, "condition", "conditions")),
        "confidence": _bounded_confidence(_pick(item, "confidence")),
        "evidence_text": _evidence_from_item(item, text, subject, obj),
    }


EVIDENCE_LIST_FIELDS = (
    "materials",
    "samples",
    "phases",
    "properties",
    "devices",
    "process_steps",
    "reliability_events",
    "mechanisms",
    "computations",
    "applications",
    "relations",
    "evidences",
)


def ground_extraction_result(result: HfO2ExtractionResult, row: Any) -> HfO2ExtractionResult:
    """Drop claims whose quoted evidence cannot be located in the focal chunk."""
    focal_text = str(_row_get(row, "text", ""))
    data = result.model_dump(mode="json")
    warnings = list(data.get("warnings") or [])
    for field in EVIDENCE_LIST_FIELDS:
        grounded: list[dict[str, Any]] = []
        dropped = 0
        for item in data.get(field, []):
            evidence = str(item.get("evidence_text") or "")
            if evidence_is_grounded(evidence, focal_text):
                grounded.append(item)
            else:
                dropped += 1
        data[field] = grounded
        if dropped:
            warnings.append(f"dropped_unanchored_{field}:{dropped}")
    data["warnings"] = list(dict.fromkeys(str(item) for item in warnings if str(item).strip()))
    return HfO2ExtractionResult(**data)


def evidence_is_grounded(evidence: str, focal_text: str) -> bool:
    evidence_norm = _normalize_evidence_text(evidence)
    focal_norm = _normalize_evidence_text(focal_text)
    if len(evidence_norm) < 8 or not focal_norm:
        return False
    if evidence_norm in focal_norm:
        return True
    # Allow copied table/caption fragments whose punctuation was normalized by the model.
    compact_evidence = re.sub(r"[^a-z0-9]+", "", evidence_norm)
    compact_focal = re.sub(r"[^a-z0-9]+", "", focal_norm)
    return len(compact_evidence) >= 24 and compact_evidence in compact_focal


def _normalize_evidence_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    text = text.replace("μ", "u").replace("µ", "u")
    text = text.replace("−", "-").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip().strip('"\'')


def _bounded_confidence(value: Any) -> float:
    numeric = _optional_float(value)
    return max(0.0, min(1.0, numeric if numeric is not None else 0.5))


def _optional_int(value: Any) -> int | None:
    numeric = _optional_float(value)
    return int(numeric) if numeric is not None else None


def _fraction_float(value: Any) -> float | None:
    numeric = _optional_float(value)
    if numeric is None:
        return None
    if 1 < numeric <= 100:
        numeric /= 100
    return numeric if 0 <= numeric <= 1 else None


def _normalize_evidence_scope(value: Any) -> str:
    text = str(value or "").lower()
    if "review" in text:
        return "review_secondary"
    if "cited" in text or "secondary" in text:
        return "cited_secondary"
    if "comput" in text or "theor" in text:
        return "primary_computation"
    if "experiment" in text or "primary" in text:
        return "primary_experiment"
    return "unknown"


def _normalize_evidence_role(value: Any) -> str:
    text = str(value or "").lower()
    for role in ["material", "process", "phase", "measurement", "reliability", "mechanism", "computation", "application"]:
        if role in text:
            return role
    return "other"


def _normalize_process_step(value: Any) -> str:
    text = str(value or "").lower()
    if any(token in text for token in ["anneal", "rta", "pma", "pda", "thermal"]):
        return "annealing"
    if any(token in text for token in ["electrode", "contact"]):
        return "electrode_deposition"
    if any(token in text for token in ["interface", "surface", "plasma", "ozone"]):
        return "interface_treatment"
    if any(token in text for token in ["pattern", "etch", "lithograph"]):
        return "patterning"
    if any(token in text for token in ["measure", "pund", "hysteresis", "cycling"]):
        return "measurement"
    if any(token in text for token in ["deposit", "ald", "pld", "sputter", "cvd", "sol-gel", "csd"]):
        return "deposition"
    return "other"


def _normalize_reliability(value: Any) -> str:
    text = str(value or "").lower().replace("-", "_").replace(" ", "_")
    mapping = {
        "wake": "wake_up",
        "fatigue": "fatigue",
        "endurance": "endurance",
        "retention": "retention",
        "imprint": "imprint",
        "breakdown": "breakdown",
        "leakage": "leakage_degradation",
        "variab": "variability",
        "recover": "recovery",
    }
    for token, label in mapping.items():
        if token in text:
            return label
    return "other"


def _normalize_trend(value: Any) -> str:
    text = str(value or "").lower()
    if any(token in text for token in ["improv", "increase", "enhanc", "recover"]):
        return "improved"
    if any(token in text for token in ["degrad", "decrease", "fatigue", "loss"]):
        return "degraded"
    if any(token in text for token in ["stable", "unchanged", "constant"]):
        return "stable"
    if any(token in text for token in ["non-monot", "nonmonot", "peak"]):
        return "non_monotonic"
    return "unknown"


def _normalize_relation_nature(value: Any) -> str:
    text = str(value or "").lower()
    for label in ["causal", "correlational", "hypothesized", "computed", "review_summary"]:
        if label.replace("_", " ") in text or label in text:
            return label
    if "suggest" in text or "may" in text:
        return "hypothesized"
    return "unknown"


def _normalize_direction(value: Any) -> str:
    text = str(value or "").lower()
    if any(token in text for token in ["stabil", "promot"]):
        return "stabilize"
    if any(token in text for token in ["destabil", "suppress"]):
        return "destabilize"
    if any(token in text for token in ["increase", "enhance", "higher"]):
        return "increase"
    if any(token in text for token in ["decrease", "reduce", "lower"]):
        return "decrease"
    if "mixed" in text or "non-monot" in text:
        return "mixed"
    return "unknown"


def _normalize_computation_method(value: Any) -> str:
    text = str(value or "").lower()
    mapping = [
        ("machine learning", "ML_potential"), ("ml potential", "ML_potential"),
        ("phase field", "phase_field"), ("phase-field", "phase_field"),
        ("dfpt", "DFPT"), ("neb", "NEB"), ("aimd", "AIMD"),
        ("molecular dynamics", "MD"), ("landau", "Landau"),
        ("tcad", "TCAD"), ("compact", "compact_model"),
        ("kinetic", "kinetic_model"), ("dft", "DFT"),
        ("first-principles", "DFT"),
    ]
    for token, label in mapping:
        if token in text:
            return label
    return "other"


def _normalize_predicate(value: Any) -> str | None:
    text = str(value or "").lower()
    mapping = [
        ("destabil", "DESTABILIZES"), ("stabil", "STABILIZES"),
        ("transform", "TRANSFORMS_TO"), ("correl", "CORRELATES_WITH"),
        ("suppress", "SUPPRESSES"), ("promot", "PROMOTES"),
        ("limit", "LIMITS"), ("compar", "COMPARED_WITH"),
        ("decreas", "DECREASES"), ("reduc", "DECREASES"),
        ("increas", "INCREASES"), ("enhanc", "INCREASES"),
        ("cause", "CAUSES"),
    ]
    for token, label in mapping:
        if token in text:
            return label
    upper = str(value or "").upper()
    allowed = {label for _, label in mapping}
    return upper if upper in allowed else None


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return parse_numeric(str(value))


def _optional_positive_float(value: Any) -> float | None:
    parsed = _optional_float(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _infer_property_observation(evidence: str, raw_property_name: str) -> dict[str, Any]:
    normalized = _normalize_evidence_text(evidence)
    property_key = raw_property_name.lower().replace("-", "_").replace(" ", "_")
    unit_pattern = (
        r"(?:uC/cm(?:\^?2|2)|mC/m(?:\^?2|2)|MV/cm|kV/cm|V/cm|"
        r"mV|V|pF|nF|uF|eV|meV|nm|um|ms|us|ns|s|Hz|kHz|MHz|GHz|%|cycles?|states?)"
    )
    number = r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"

    search_text = normalized
    if "negative" in property_key:
        match = re.search(rf"negative[^.;\n]{{0,100}}?({number})\s*({unit_pattern})", normalized, re.IGNORECASE)
        if match:
            return _single_observation(match)
    if "positive" in property_key:
        match = re.search(rf"positive[^.;\n]{{0,100}}?({number})\s*({unit_pattern})", normalized, re.IGNORECASE)
        if match:
            return _single_observation(match)
    if "read_voltage" in property_key:
        match = re.search(rf"read[^.;\n]{{0,100}}?({number})\s*(mV|V)\b", normalized, re.IGNORECASE)
        if match:
            return _single_observation(match)
    if "state" in property_key:
        match = re.search(rf"({number})\s*(?:to|-)\s*({number})\s*(capacitive\s+states?|states?)", normalized, re.IGNORECASE)
        if match:
            low, high = sorted((_optional_float(match.group(1)), _optional_float(match.group(2))))
            return {
                "value": None,
                "value_min": low,
                "value_max": high,
                "unit": "states",
                "raw_value_text": match.group(0),
            }

    range_match = re.search(
        rf"({number})\s*(?:to|-)\s*({number})\s*({unit_pattern})",
        search_text,
        re.IGNORECASE,
    )
    if range_match:
        first = _optional_float(range_match.group(1))
        second = _optional_float(range_match.group(2))
        low, high = sorted((first, second)) if first is not None and second is not None else (first, second)
        return {
            "value": None,
            "value_min": low,
            "value_max": high,
            "unit": range_match.group(3),
            "raw_value_text": range_match.group(0),
        }

    matches = list(re.finditer(rf"({number})\s*({unit_pattern})", search_text, re.IGNORECASE))
    if len(matches) == 1:
        return _single_observation(matches[0])
    return {"value": None, "value_min": None, "value_max": None, "unit": None, "raw_value_text": None}


def _single_observation(match: re.Match[str]) -> dict[str, Any]:
    return {
        "value": _optional_float(match.group(1)),
        "value_min": None,
        "value_max": None,
        "unit": match.group(2),
        "raw_value_text": match.group(0),
    }


def _normalize_comparison_operator(
    operator: Any,
    evidence: str,
    *,
    value: float | None,
    value_min: float | None,
    value_max: float | None,
) -> str:
    raw = str(operator or "").strip().lower()
    aliases = {
        "=": "eq", "eq": "eq", "equal": "eq",
        "<": "lt", "lt": "lt", "less_than": "lt",
        "<=": "le", "≤": "le", "le": "le", "at_most": "le",
        ">": "gt", "gt": "gt", "greater_than": "gt",
        ">=": "ge", "≥": "ge", "ge": "ge", "at_least": "ge",
        "~": "approx", "≈": "approx", "approx": "approx", "approximately": "approx",
        "range": "range", "between": "range",
    }
    if raw in aliases:
        return aliases[raw]
    if value_min is not None or value_max is not None:
        return "range"
    lowered = evidence.lower()
    if any(token in lowered for token in ["approximately", "about ", "around ", "near ", "∼", "~", "≈"]):
        return "approx"
    if value is not None:
        return "eq"
    return "unknown"


def _property_values() -> set[str]:
    return {
        "remanent_polarization_Pr",
        "double_remanent_polarization_2Pr",
        "coercive_field_Ec",
        "saturation_polarization_Ps",
        "dielectric_constant",
        "leakage_current_density",
        "endurance_cycles",
        "retention_time",
        "memory_window",
        "wake_up",
        "fatigue",
        "breakdown_field",
        "band_gap",
        "imprint_voltage",
        "dielectric_loss",
        "switching_time",
        "on_off_ratio",
        "threshold_voltage_shift",
        "subthreshold_swing",
        "grain_size",
        "phase_fraction",
        "oxygen_vacancy_concentration",
        "vacancy_formation_energy",
        "vacancy_migration_barrier",
        "phase_energy_difference",
        "switching_energy_barrier",
        "computed_polarization",
        "interface_energy",
        "other_hafnia_property",
    }


def _normalize_material_family(value: str) -> str:
    lower = value.lower()
    if "hzo" in lower or "zr" in lower:
        return "HZO"
    if "si" in lower:
        return "Si:HfO2"
    if "al" in lower:
        return "Al:HfO2"
    if "la" in lower:
        return "La:HfO2"
    if "y:" in lower or lower.startswith("y") or " y-" in lower:
        return "Y:HfO2"
    if "gd" in lower:
        return "Gd:HfO2"
    if "sr" in lower:
        return "Sr:HfO2"
    if "hfo2" in lower or "hafnia" in lower or "hafnium" in lower:
        return "HfO2"
    return "unknown_hafnia"


def _infer_material_family(raw_name: str, canonical: str, dopants: list[str]) -> str:
    combined = " ".join([raw_name, canonical, *dopants])
    return _normalize_material_family(combined)


def _infer_dopants(raw_name: str, canonical: str) -> list[str]:
    combined = f"{raw_name} {canonical}"
    dopants = []
    for element in ["Zr", "Si", "Al", "La", "Y", "Gd", "Sr"]:
        if re.search(rf"\b{element}\b", combined, re.IGNORECASE):
            dopants.append(element)
    return dopants


def _normalize_phase(value: str) -> str:
    lower = value.lower()
    if "pca" in lower or "orth" in lower:
        return "orthorhombic"
    if "monoclinic" in lower or "p21/c" in lower:
        return "monoclinic"
    if "tetragonal" in lower or "p42" in lower:
        return "tetragonal"
    if "cubic" in lower:
        return "cubic"
    if "rhombo" in lower:
        return "rhombohedral"
    if "amorph" in lower:
        return "amorphous"
    if "mixed" in lower:
        return "mixed"
    return "unknown"


def _infer_unit(evidence: str, property_name: str) -> str | None:
    if re.search(r"(?:μ|µ|u)\s*C\s*/?\s*cm(?:\^?2|²|−2|-2)", evidence, re.IGNORECASE):
        return "μC/cm²"
    if re.search(r"\bkV\s*/?\s*cm\b", evidence, re.IGNORECASE):
        return "kV/cm"
    if re.search(r"\bMV\s*/?\s*cm\b", evidence, re.IGNORECASE):
        return "MV/cm"
    if "cycles" in evidence.lower() or property_name == "endurance_cycles":
        return "cycles"
    if property_name == "memory_window" and re.search(r"\bV\b", evidence):
        return "V"
    return None
