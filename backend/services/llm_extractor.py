from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.core.config import PROJECT_ROOT, get_openai_api_key, get_settings
from backend.schemas.hfo2_extraction_schema import HfO2ExtractionResult
from backend.services.ontology_builder import load_ontology_prompt_context


@dataclass(frozen=True)
class LLMExtractionOutcome:
    result: HfO2ExtractionResult | None
    used_llm: bool
    error_message: str | None = None


def llm_is_configured() -> bool:
    return bool(get_openai_api_key())


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
        "model": settings.llm_model,
        "api_key_configured": llm_is_configured(),
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


def extract_chunk_with_llm(row, model: str | None = None, timeout: float = 60.0) -> LLMExtractionOutcome:
    if not llm_is_configured():
        return LLMExtractionOutcome(result=None, used_llm=False, error_message="OPENAI_API_KEY is not set")

    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - environment specific
        return LLMExtractionOutcome(result=None, used_llm=False, error_message=f"OpenAI SDK unavailable: {exc}")

    settings = get_settings()
    selected_model = model or settings.llm_model
    client = OpenAI(api_key=get_openai_api_key())

    try:
        response = client.responses.parse(
            model=selected_model,
            instructions=load_prompt(),
            input=build_user_input(row),
            text_format=HfO2ExtractionResult,
            temperature=0,
            max_output_tokens=3000,
            timeout=timeout,
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
        return LLMExtractionOutcome(
            result=HfO2ExtractionResult(**data),
            used_llm=True,
            error_message=None,
        )
    except Exception as exc:
        return LLMExtractionOutcome(result=None, used_llm=True, error_message=str(exc))
