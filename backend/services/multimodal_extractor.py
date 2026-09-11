from __future__ import annotations

import base64
import hashlib
import io
import json
import mimetypes
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from backend.core.config import PROJECT_ROOT, get_llm_api_key, get_settings
from backend.db.session import connect
from backend.schemas.hfo2_extraction_schema import PropertyName
from backend.schemas.multimodal_extraction_schema import MultimodalExtractionResult
from backend.services.fact_normalizer import normalize_property_alias, normalize_unit
from backend.services.llm_extractor import json_loads_object, normalize_base_url
from backend.services.pipeline_log import record_pipeline_run


ONTOLOGY_VERSION = "hfo2-ferrokg-v2.3"
PROMPT_VERSION = "hfo2-multimodal-v1.1"
PROMPT_PATH = PROJECT_ROOT / "prompts" / "hfo2_multimodal_extraction_prompt.md"


@dataclass(frozen=True)
class MultimodalOutcome:
    result: MultimodalExtractionResult | None
    model: str
    usage: dict[str, int | None]
    error_message: str | None = None


def model_for_source(source_type: str, explicit_model: str | None = None) -> str:
    if explicit_model:
        return explicit_model
    if source_type in {"figure", "equation"}:
        return os.getenv("HFO2_FERROKG_VISION_MODEL", "qwen3-vl-plus")
    return get_settings().llm_model


def _clamp_confidence(value: Any, default: float = 0.5) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def multimodal_benchmark_tier(observation: Any) -> str:
    origin = str(observation.value_origin or "unclear")
    confidence = _clamp_confidence(observation.confidence, default=0.0)
    if origin in {"table_cell", "body_text", "equation_symbol"} and confidence >= 0.8:
        return "strong_only"
    if origin in {"axis_label", "caption_text", "derived"} and confidence >= 0.65:
        return "strong_partial"
    return "all_traceable"


def coerce_multimodal_payload(payload: dict[str, Any], row: Any) -> dict[str, Any]:
    """Constrain provider JSON to the local multimodal schema without inventing evidence."""
    source_type = str(row["source_type"])
    warnings = [str(value) for value in payload.get("warnings", []) if str(value).strip()]
    allowed_property_names = {item.value for item in PropertyName}
    allowed_observation_types = {
        "numeric_value", "trend", "comparison", "phase_evidence", "morphology",
        "device_stack", "process_condition", "mechanism", "computational_result",
        "application", "other",
    }
    allowed_value_origins = {
        "table_cell", "axis_label", "caption_text", "body_text", "visual_estimate",
        "equation_symbol", "derived", "not_applicable", "unclear",
    }
    observations: list[dict[str, Any]] = []
    for raw in payload.get("observations") or []:
        if not isinstance(raw, dict):
            continue
        item = {
            "observation_type": str(raw.get("observation_type") or "other"),
            "description": str(raw.get("description") or raw.get("evidence_text") or "Unresolved asset observation."),
            "property_name": raw.get("property_name"),
            "raw_value_text": raw.get("raw_value_text"),
            "value": _safe_float(raw.get("value")),
            "value_min": _safe_float(raw.get("value_min")),
            "value_max": _safe_float(raw.get("value_max")),
            "unit": raw.get("unit"),
            "material_ref": raw.get("material_ref"),
            "sample_ref": raw.get("sample_ref"),
            "series_or_panel": raw.get("series_or_panel"),
            "condition": raw.get("condition"),
            "value_origin": str(raw.get("value_origin") or "unclear"),
            "evidence_scope": str(raw.get("evidence_scope") or "unclear"),
            "confidence": _clamp_confidence(raw.get("confidence")),
            "evidence_text": str(raw.get("evidence_text") or raw.get("description") or "asset evidence"),
        }
        if raw.get("confidence") is None:
            warnings.append("observation_confidence_missing_default_0.5")
        if item["property_name"] not in allowed_property_names:
            alias_text = " ".join(
                str(value or "")
                for value in (
                    raw.get("property_name"), item["description"], item["evidence_text"], item["raw_value_text"]
                )
            )
            inferred_property = normalize_property_alias(alias_text)
            if (
                inferred_property is None
                and source_type in {"figure", "table"}
                and "dielectric" in str(row["context_text"] or "").lower()
                and re.search(r"\bk(?:-value|\s+is|\s+increase|\s+decrease|\s*\()", alias_text.lower())
            ):
                inferred_property = PropertyName.dielectric_constant
            item["property_name"] = inferred_property.value if inferred_property else None
        item["unit"] = normalize_unit(item["unit"])
        semantic_text = " ".join(
            str(value or "")
            for value in (item["description"], item["evidence_text"], item["raw_value_text"])
        ).lower()
        if "thickness" in semantic_text and str(item["unit"] or "").lower() == "nm":
            item["property_name"] = None
            item["observation_type"] = "process_condition"
        unit_lower = str(item["unit"] or "").lower().replace("µ", "μ")
        if (
            ("δp" in semantic_text or "polarization change" in semantic_text)
            and unit_lower in {"μc/cm²", "μc/cm2", "c/m²", "c/m2"}
        ):
            item["property_name"] = PropertyName.polarization_change_DeltaP.value
            warnings.append("property_corrected_to_polarization_change_DeltaP")
        elif "switch" in semantic_text and unit_lower in {"s", "ms", "μs", "us", "ns"}:
            item["property_name"] = PropertyName.switching_time.value
            warnings.append("property_corrected_to_switching_time_by_unit")
        elif (
            "polarization switching" in semantic_text
            and ("%" in semantic_text or str(item["unit"] or "").lower() == "fraction")
        ):
            item["property_name"] = PropertyName.switched_polarization_fraction.value
        if str(item["unit"] or "").strip().lower() in {"none", "unitless", "n/a"}:
            item["unit"] = (
                "dimensionless"
                if item["property_name"] == PropertyName.dielectric_constant.value
                else "fraction"
                if item["property_name"] == PropertyName.switched_polarization_fraction.value
                else None
            )
        has_numeric = any(item[key] is not None for key in ("value", "value_min", "value_max"))
        if item["value_origin"] not in allowed_value_origins or item["value_origin"] == "unclear":
            evidence_lower = item["evidence_text"].lower()
            if source_type == "table":
                item["value_origin"] = "table_cell"
            elif source_type == "equation":
                item["value_origin"] = "equation_symbol"
            elif "page text" in evidence_lower or "body text" in evidence_lower or "text:" in evidence_lower:
                item["value_origin"] = "body_text"
            elif "caption" in evidence_lower:
                item["value_origin"] = "caption_text"
            elif source_type == "figure" and has_numeric:
                item["value_origin"] = "visual_estimate"
            else:
                item["value_origin"] = "not_applicable"
        if item["value_origin"] == "caption_text" and "page text" in item["evidence_text"].lower():
            item["value_origin"] = "body_text"
        if source_type == "figure" and item["value_origin"] == "caption_text" and has_numeric:
            context_parts = str(row["context_text"] or "").split("PAGE_CONTEXT:", 1)
            caption_context = context_parts[0]
            page_context = context_parts[1] if len(context_parts) > 1 else ""
            numeric_tokens = re.findall(r"[-+]?\d+(?:\.\d+)?", str(item["raw_value_text"] or ""))
            if numeric_tokens and not any(token in caption_context for token in numeric_tokens):
                item["value_origin"] = (
                    "body_text" if any(token in page_context for token in numeric_tokens) else "visual_estimate"
                )
        if item["value_origin"] == "visual_estimate" and item["confidence"] > 0.55:
            item["confidence"] = 0.55
            warnings.append("visual_estimate_confidence_capped_0.55")
        if item["observation_type"] not in allowed_observation_types or item["observation_type"] == "other":
            if item["property_name"] and any(item[key] is not None for key in ("value", "value_min", "value_max")):
                item["observation_type"] = "numeric_value"
            elif any(word in item["description"].lower() for word in ("increase", "decrease", "higher", "lower", "trend")):
                item["observation_type"] = "trend"
            elif "thickness" in item["description"].lower():
                item["observation_type"] = "process_condition"
            elif source_type == "equation":
                item["observation_type"] = "computational_result"
            else:
                item["observation_type"] = "other"
        if item["evidence_scope"] not in {
            "primary_experiment", "primary_computation", "cited_secondary", "review_summary", "unclear"
        }:
            item["evidence_scope"] = "unclear"
        has_numeric = any(item[key] is not None for key in ("value", "value_min", "value_max"))
        if has_numeric and not item["unit"] and item["property_name"] == PropertyName.dielectric_constant.value:
            item["unit"] = "dimensionless"
        if has_numeric and not item["unit"] and item["property_name"] == PropertyName.switched_polarization_fraction.value:
            item["unit"] = "fraction"
        if has_numeric and not item["unit"]:
            warnings.append("numeric_observation_without_unit_removed")
            item["value"] = item["value_min"] = item["value_max"] = None
        observations.append(item)

    equation = payload.get("equation") if isinstance(payload.get("equation"), dict) else None
    if source_type == "equation":
        equation = equation or {}
        equation = {
            "is_valid_equation": bool(equation.get("is_valid_equation", False)),
            "latex": equation.get("latex"),
            "plain_text": equation.get("plain_text"),
            "equation_role": str(equation.get("equation_role") or "unknown"),
            "variables": [str(value) for value in equation.get("variables", []) if str(value).strip()],
            "assumptions_or_domain": equation.get("assumptions_or_domain"),
        }
        allowed_roles = {
            "free_energy", "constitutive_relation", "kinetics", "transport", "reliability",
            "device_model", "normalization", "computational_descriptor", "other", "unknown",
        }
        if equation["equation_role"] not in allowed_roles:
            equation["equation_role"] = "unknown"
        if equation["equation_role"] == "unknown":
            role_text = " ".join(
                str(value or "")
                for value in (
                    payload.get("semantic_summary"), equation.get("latex"), equation.get("plain_text"), row["context_text"]
                )
            ).lower()
            if "landau-khalatnikov" in role_text or "time evolution" in role_text or "dp" in role_text and "/dt" in role_text:
                equation["equation_role"] = "kinetics"
            elif "free energy" in role_text:
                equation["equation_role"] = "free_energy"
            elif "current" in role_text or "transport" in role_text:
                equation["equation_role"] = "transport"

    visual_type = str(payload.get("visual_type") or ("equation" if source_type == "equation" else "unknown"))
    allowed_visual_types = {
        "plot", "hysteresis_loop", "diffraction_pattern", "micrograph", "phase_map",
        "device_schematic", "process_schematic", "calculation_plot", "table_image",
        "equation", "other", "unknown",
    }
    if visual_type not in allowed_visual_types:
        visual_type = "unknown"
    if source_type == "equation":
        visual_type = "equation"
    elif source_type == "figure" and visual_type == "unknown":
        context_lower = str(row["context_text"] or "").lower()
        caption_lower = context_lower.split("page_context:", 1)[0]
        if "hysteresis" in caption_lower:
            visual_type = "hysteresis_loop"
        elif " vs " in caption_lower or "curve" in caption_lower or "plot" in caption_lower:
            visual_type = "plot"
        elif "xrd" in caption_lower or "diffraction" in caption_lower:
            visual_type = "diffraction_pattern"
        elif " vs " in context_lower or "curve" in context_lower or "plot" in context_lower:
            visual_type = "plot"

    summary = str(payload.get("semantic_summary") or "No reliable semantic summary was returned.")
    top_confidence = _clamp_confidence(payload.get("confidence"))
    if payload.get("confidence") is None:
        warnings.append("top_level_confidence_missing_default_0.5")
    if source_type == "equation" and equation and equation["is_valid_equation"] and not observations:
        equation_text = str(equation.get("latex") or equation.get("plain_text") or row["context_text"])
        observations.append(
            {
                "observation_type": "computational_result",
                "description": summary,
                "property_name": None,
                "raw_value_text": equation_text,
                "value": None,
                "value_min": None,
                "value_max": None,
                "unit": None,
                "material_ref": None,
                "sample_ref": None,
                "series_or_panel": None,
                "condition": equation.get("assumptions_or_domain"),
                "value_origin": "equation_symbol",
                "evidence_scope": "primary_computation" if payload.get("is_primary_evidence") else "unclear",
                "confidence": top_confidence,
                "evidence_text": equation_text,
            }
        )
    return {
        "source_type": source_type,
        "source_id": str(row["source_id"]),
        "paper_id": str(row["paper_id"] or f"paper_for_{row['pdf_id']}"),
        "pdf_id": str(row["pdf_id"]),
        "page_number": int(row["page_number"]),
        "is_hafnia_relevant": bool(payload.get("is_hafnia_relevant", False)),
        "is_primary_evidence": bool(payload.get("is_primary_evidence", False)),
        "semantic_summary": summary,
        "visual_type": visual_type,
        "observations": observations,
        "equation": equation,
        "confidence": top_confidence,
        "warnings": list(dict.fromkeys(warnings)),
    }


def _image_data_url(path: str) -> str:
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(f"Multimodal asset not found: {target}")
    mime = mimetypes.guess_type(target.name)[0] or "image/png"
    content = target.read_bytes()
    try:
        from PIL import Image

        with Image.open(io.BytesIO(content)) as image:
            image.load()
            if image.mode not in {"RGB", "L"}:
                background = Image.new("RGB", image.size, "white")
                if "A" in image.getbands():
                    background.paste(image, mask=image.getchannel("A"))
                else:
                    background.paste(image.convert("RGB"))
                image = background
            if max(image.size) > 2400 or image.width * image.height > 6_000_000:
                image.thumbnail((2400, 2400))
            buffer = io.BytesIO()
            if image.mode == "L":
                image.save(buffer, format="PNG", optimize=True)
                mime = "image/png"
            else:
                image.save(buffer, format="JPEG", quality=92, optimize=True)
                mime = "image/jpeg"
            content = buffer.getvalue()
    except Exception:
        pass
    return f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"


def _load_queue_row(source_type: str, source_id: str, db_path: Path | None = None):
    with connect(db_path) as conn:
        return conn.execute(
            """
            SELECT source_type, source_id, paper_id, pdf_id, page_number,
                   file_path, context_text, priority_tier, priority_score
            FROM multimodal_asset_queue
            WHERE source_type = ? AND source_id = ?
            """,
            (source_type, source_id),
        ).fetchone()


def extract_multimodal_asset(
    source_type: str,
    source_id: str,
    model: str | None = None,
    db_path: Path | None = None,
) -> MultimodalOutcome:
    selected_model = model_for_source(source_type, model)
    if not get_llm_api_key():
        return MultimodalOutcome(None, selected_model, {}, "LLM API key is not configured")
    row = _load_queue_row(source_type, source_id, db_path=db_path)
    if row is None:
        return MultimodalOutcome(None, selected_model, {}, "Asset is not present in the multimodal queue")
    settings = get_settings()
    base_url, _ = normalize_base_url(settings.llm_provider, settings.llm_base_url)
    try:
        from openai import OpenAI

        client_kwargs: dict[str, Any] = {"api_key": get_llm_api_key(), "timeout": settings.llm_timeout_seconds}
        if base_url:
            client_kwargs["base_url"] = base_url
        client = OpenAI(**client_kwargs)
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
        user_text = (
            f"source_type: {row['source_type']}\nsource_id: {row['source_id']}\n"
            f"paper_id: {row['paper_id']}\npdf_id: {row['pdf_id']}\npage_number: {row['page_number']}\n\n"
            f"SOURCE_CONTEXT:\n{str(row['context_text'])[:12000]}"
        )
        content: Any = user_text
        if source_type in {"figure", "equation"}:
            if not row["file_path"]:
                raise FileNotFoundError("Queued visual asset has no local file path")
            content = [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": _image_data_url(str(row["file_path"]))}},
            ]
        request_kwargs: dict[str, Any] = {
            "model": selected_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            "temperature": 0,
            "max_tokens": min(max(settings.llm_max_tokens, 2500), 5000),
            "response_format": {"type": "json_object"},
        }
        if settings.llm_provider == "dashscope":
            request_kwargs["extra_body"] = {"enable_thinking": False}
        try:
            response = client.chat.completions.create(**request_kwargs)
        except Exception as exc:
            if "response_format" not in str(exc):
                raise
            request_kwargs.pop("response_format", None)
            response = client.chat.completions.create(**request_kwargs)
        payload = json_loads_object(response.choices[0].message.content or "{}")
        result = MultimodalExtractionResult(**coerce_multimodal_payload(payload, row))
        usage_obj = getattr(response, "usage", None)
        usage = {
            "prompt_tokens": getattr(usage_obj, "prompt_tokens", None),
            "completion_tokens": getattr(usage_obj, "completion_tokens", None),
            "total_tokens": getattr(usage_obj, "total_tokens", None),
        } if usage_obj is not None else {}
        return MultimodalOutcome(result, selected_model, usage)
    except Exception as exc:
        return MultimodalOutcome(None, selected_model, {}, str(exc))


def _extraction_id(source_type: str, source_id: str, model: str) -> str:
    key = f"{ONTOLOGY_VERSION}:{model}:{source_type}:{source_id}"
    return f"mmx_{uuid.uuid5(uuid.NAMESPACE_URL, key).hex[:16]}"


def store_multimodal_outcome(
    outcome: MultimodalOutcome,
    row: Any,
    db_path: Path | None = None,
    run_kind: str = "model_call",
) -> str:
    extraction_id = _extraction_id(str(row["source_type"]), str(row["source_id"]), outcome.model)
    result = outcome.result
    payload_json = json.dumps(result.model_dump(mode="json") if result else {}, ensure_ascii=False)
    status = "pending_review" if result else "extraction_error"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO multimodal_extraction_runs (
                run_id, extraction_id, source_type, source_id, paper_id, pdf_id,
                page_number, payload_json, model_name, ontology_version,
                prompt_version, run_kind, status, confidence, error_message,
                prompt_tokens, completion_tokens, total_tokens
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"mmrun_{uuid.uuid4().hex[:20]}", extraction_id, row["source_type"],
                row["source_id"], row["paper_id"], row["pdf_id"], row["page_number"],
                payload_json, outcome.model, ONTOLOGY_VERSION, PROMPT_VERSION,
                run_kind, status, result.confidence if result else None,
                outcome.error_message, outcome.usage.get("prompt_tokens"),
                outcome.usage.get("completion_tokens"), outcome.usage.get("total_tokens"),
            ),
        )
        conn.execute(
            """
            INSERT INTO multimodal_extractions (
                extraction_id, source_type, source_id, paper_id, pdf_id, page_number,
                payload_json, model_name, ontology_version, prompt_version, status,
                confidence, error_message, prompt_tokens, completion_tokens, total_tokens, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(source_type, source_id, model_name, ontology_version) DO UPDATE SET
                payload_json=excluded.payload_json, prompt_version=excluded.prompt_version,
                status=excluded.status, confidence=excluded.confidence,
                error_message=excluded.error_message,
                prompt_tokens=COALESCE(excluded.prompt_tokens, multimodal_extractions.prompt_tokens),
                completion_tokens=COALESCE(excluded.completion_tokens, multimodal_extractions.completion_tokens),
                total_tokens=COALESCE(excluded.total_tokens, multimodal_extractions.total_tokens),
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                extraction_id, row["source_type"], row["source_id"], row["paper_id"],
                row["pdf_id"], row["page_number"], payload_json, outcome.model,
                ONTOLOGY_VERSION, PROMPT_VERSION, status,
                result.confidence if result else None, outcome.error_message,
                outcome.usage.get("prompt_tokens"), outcome.usage.get("completion_tokens"),
                outcome.usage.get("total_tokens"),
            ),
        )
        conn.execute("DELETE FROM multimodal_evidence WHERE extraction_id = ?", (extraction_id,))
        if result:
            for index, observation in enumerate(result.observations, start=1):
                evidence_id = f"mme_{uuid.uuid5(uuid.NAMESPACE_URL, f'{extraction_id}:{index}').hex[:16]}"
                conn.execute(
                    """
                    INSERT INTO multimodal_evidence (
                        evidence_id, extraction_id, source_type, source_id, paper_id, pdf_id,
                        page_number, observation_type, description, property_name, raw_value_text,
                        value, value_min, value_max, unit, material_ref, sample_ref,
                        series_or_panel, condition_text, value_origin, evidence_scope,
                        confidence, evidence_text,
                        review_status, benchmark_tier
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        evidence_id, extraction_id, result.source_type, result.source_id,
                        result.paper_id, result.pdf_id, result.page_number,
                        observation.observation_type, observation.description,
                        observation.property_name.value if observation.property_name else None,
                        observation.raw_value_text, observation.value, observation.value_min,
                        observation.value_max, observation.unit, observation.material_ref,
                        observation.sample_ref, observation.series_or_panel, observation.condition,
                        observation.value_origin, observation.evidence_scope, observation.confidence,
                        observation.evidence_text, "needs_human_review",
                        multimodal_benchmark_tier(observation),
                    ),
                )
            if result.source_type == "equation" and result.equation:
                conn.execute(
                    """
                    UPDATE pdf_equations
                    SET latex_text = ?, variables_json = ?, equation_role = ?,
                        extraction_status = ?
                    WHERE equation_id = ?
                    """,
                    (
                        result.equation.latex,
                        json.dumps(result.equation.variables, ensure_ascii=False),
                        result.equation.equation_role,
                        "semantic_candidate" if result.equation.is_valid_equation else "rejected_non_equation",
                        result.source_id,
                    ),
                )
        conn.execute(
            "UPDATE multimodal_asset_queue SET queue_status = ?, updated_at=CURRENT_TIMESTAMP WHERE source_type=? AND source_id=?",
            ("processed" if result else "extraction_error", row["source_type"], row["source_id"]),
        )
        conn.commit()
    return extraction_id


def run_multimodal_queue(
    limit: int = 3,
    source_types: Iterable[str] = ("figure", "table", "equation"),
    priority_tiers: Iterable[str] = ("P0",),
    model: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    sources = tuple(dict.fromkeys(str(value) for value in source_types))
    tiers = tuple(dict.fromkeys(str(value) for value in priority_tiers))
    if not sources or not tiers:
        raise ValueError("At least one source type and priority tier are required")
    source_marks = ",".join("?" for _ in sources)
    tier_marks = ",".join("?" for _ in tiers)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT source_type, source_id, paper_id, pdf_id, page_number, file_path,
                   context_text, priority_tier, priority_score
            FROM multimodal_asset_queue
            WHERE queue_status IN ('queued', 'extraction_error')
              AND source_type IN ({source_marks})
              AND priority_tier IN ({tier_marks})
            ORDER BY CASE priority_tier WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 ELSE 2 END,
                     priority_score DESC, source_type, source_id
            LIMIT ?
            """,
            (*sources, *tiers, int(limit)),
        ).fetchall()

    stats: dict[str, Any] = {
        "selected": len(rows), "processed": 0, "errors": 0,
        "observations": 0, "source_counts": {},
        "model_policy": model or "qwen3.7-max:text_table;qwen3-vl-plus:figure_equation",
    }
    source_counts: dict[str, int] = {}
    for row in rows:
        source_counts[row["source_type"]] = source_counts.get(row["source_type"], 0) + 1
        outcome = extract_multimodal_asset(row["source_type"], row["source_id"], model=model, db_path=db_path)
        store_multimodal_outcome(outcome, row, db_path=db_path)
        if outcome.result:
            stats["processed"] += 1
            stats["observations"] += len(outcome.result.observations)
        else:
            stats["errors"] += 1
    stats["source_counts"] = source_counts
    record_pipeline_run("57_extract_multimodal_semantics", "ok" if not stats["errors"] else "partial", stats, db_path=db_path)
    return stats


def run_single_multimodal_asset(
    source_type: str,
    source_id: str,
    model: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    row = _load_queue_row(source_type, source_id, db_path=db_path)
    if row is None:
        raise ValueError(f"Unknown multimodal asset: {source_type}:{source_id}")
    outcome = extract_multimodal_asset(source_type, source_id, model=model, db_path=db_path)
    extraction_id = store_multimodal_outcome(outcome, row, db_path=db_path)
    stats = {
        "source_type": source_type,
        "source_id": source_id,
        "extraction_id": extraction_id,
        "model": outcome.model,
        "status": "pending_review" if outcome.result else "extraction_error",
        "observations": len(outcome.result.observations) if outcome.result else 0,
        "confidence": outcome.result.confidence if outcome.result else None,
        "semantic_summary": outcome.result.semantic_summary if outcome.result else None,
        "error_message": outcome.error_message,
        "usage": outcome.usage,
    }
    record_pipeline_run(
        "57_extract_multimodal_semantics",
        "ok" if outcome.result else "partial",
        stats,
        db_path=db_path,
    )
    return stats


def revalidate_multimodal_extractions(
    limit: int | None = None,
    db_path: Path | None = None,
) -> dict[str, int]:
    with connect(db_path) as conn:
        sql = """
            SELECT me.*, q.context_text, q.file_path, q.priority_tier, q.priority_score
            FROM multimodal_extractions me
            JOIN multimodal_asset_queue q
              ON q.source_type = me.source_type AND q.source_id = me.source_id
            WHERE me.status = 'pending_review'
            ORDER BY me.updated_at, me.extraction_id
        """
        if limit is not None:
            sql += " LIMIT ?"
            rows = conn.execute(sql, (int(limit),)).fetchall()
        else:
            rows = conn.execute(sql).fetchall()
    stats = {"selected": len(rows), "revalidated": 0, "errors": 0, "observations": 0}
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
            result = MultimodalExtractionResult(**coerce_multimodal_payload(payload, row))
            outcome = MultimodalOutcome(result=result, model=row["model_name"], usage={})
            store_multimodal_outcome(outcome, row, db_path=db_path, run_kind="local_revalidation")
            stats["revalidated"] += 1
            stats["observations"] += len(result.observations)
        except Exception:
            stats["errors"] += 1
    record_pipeline_run(
        "59_revalidate_multimodal_extractions",
        "ok" if not stats["errors"] else "partial",
        stats,
        db_path=db_path,
    )
    return stats
