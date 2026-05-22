from __future__ import annotations

import json
import re
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
    return f"""
paper_id: {row['paper_id']}
pdf_id: {row['pdf_id']}
chunk_id: {row['chunk_id']}
page_number: {row['page_number']}

chunk_text:
{row['text']}
""".strip()


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
        return LLMExtractionOutcome(
            result=HfO2ExtractionResult(**data),
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
        return HfO2ExtractionResult(**data)

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
        return LLMExtractionOutcome(result=parse_response(content), used_llm=True, usage=usage_payload)
    except Exception as exc:
        return LLMExtractionOutcome(result=None, used_llm=True, error_message=str(exc))


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

    evidences = [
        _coerce_evidence(item, row)
        for item in _as_list(_pick(data, "evidences", "evidence", "sources"))
        if isinstance(item, dict)
    ]
    for prop in properties:
        evidences.append(
            {
                "paper_id": _row_get(row, "paper_id"),
                "pdf_id": _row_get(row, "pdf_id"),
                "chunk_id": _row_get(row, "chunk_id"),
                "page_number": _row_get(row, "page_number"),
                "evidence_text": prop["evidence_text"],
                "source_type": "text",
            }
        )

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
        "evidences": evidences,
        "warnings": [str(item) for item in warnings if str(item).strip()],
    }


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        value = row[key]
    except Exception:
        value = default
    return default if value is None else value


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
        return explicit.strip()[:700]

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
            return re.sub(r"\s+", " ", text[left + 1 : right + 1]).strip()[:700]
    return re.sub(r"\s+", " ", text[:700]).strip()


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
        "evidence_text": _evidence_from_item(item, text, raw_name, canonical),
    }


def _coerce_sample(item: dict[str, Any], text: str, material_ref: str) -> dict[str, Any] | None:
    sample = {
        "material_ref": str(_pick(item, "material_ref", "material_name", "material", "composition") or material_ref),
        "film_thickness_nm": _optional_float(_pick(item, "film_thickness_nm", "thickness_nm", "film_thickness")),
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
        "device_stack": _optional_string(_pick(item, "device_stack", "stack", "electrode_stack")),
        "sample_form": _optional_string(_pick(item, "sample_form", "device_type", "form")) or "unknown",
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
        "characterization_method": _optional_string(
            _pick(item, "characterization_method", "method", "measurement_method")
        ),
        "evidence_text": _evidence_from_item(item, text, raw_phase, space_group),
    }


def _coerce_property(item: dict[str, Any], text: str, material_ref: str) -> dict[str, Any] | None:
    raw_name = _pick(item, "raw_property_name", "property_name", "property", "name", "metric")
    enum_name = _pick(item, "property_name", "normalized_property_name")
    if enum_name not in _property_values():
        alias = normalize_property_alias(str(raw_name or enum_name or ""))
        enum_name = alias.value if alias else None
    if enum_name is None:
        return None
    evidence = _evidence_from_item(item, text, raw_name, _pick(item, "value"))
    value = _optional_float(_pick(item, "value", "raw_value", "numeric_value"))
    unit = _optional_string(_pick(item, "unit", "raw_unit")) or _infer_unit(evidence, str(enum_name))
    if value is not None and unit is None:
        return None
    normalized_unit = normalize_unit(unit)
    confidence = _optional_float(_pick(item, "confidence")) or 0.55
    return {
        "material_ref": str(_pick(item, "material_ref", "material_name", "material") or material_ref),
        "property_name": enum_name,
        "raw_property_name": str(raw_name or enum_name),
        "value": value,
        "unit": unit,
        "normalized_value": value,
        "normalized_unit": normalized_unit,
        "measurement_temperature": _optional_string(_pick(item, "measurement_temperature")),
        "measurement_frequency": _optional_string(_pick(item, "measurement_frequency")),
        "electric_field": _optional_string(_pick(item, "electric_field", "measurement_field")),
        "device_type": _optional_string(_pick(item, "device_type")),
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
    }


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
