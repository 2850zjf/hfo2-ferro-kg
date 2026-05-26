from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.core.config import get_llm_api_key, get_settings
from backend.services.llm_extractor import json_loads_object, normalize_base_url


@dataclass(frozen=True)
class LLMJsonResult:
    payload: dict[str, Any]
    usage: dict[str, int | None]
    model: str


def llm_json_chat(
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float = 0,
    enable_thinking: bool | None = None,
    timeout: float | None = None,
) -> LLMJsonResult:
    """Call the configured OpenAI-compatible chat endpoint and parse one JSON object."""
    if not get_llm_api_key():
        raise RuntimeError("LLM API key is not configured")
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - environment specific
        raise RuntimeError(f"OpenAI SDK unavailable: {exc}") from exc

    settings = get_settings()
    selected_model = model or settings.llm_model
    base_url, _ = normalize_base_url(settings.llm_provider, settings.llm_base_url)
    client_kwargs: dict[str, Any] = {
        "api_key": get_llm_api_key(),
        "timeout": timeout or settings.llm_timeout_seconds,
    }
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)

    request_kwargs: dict[str, Any] = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens or min(settings.llm_max_tokens, 4000),
        "response_format": {"type": "json_object"},
    }
    if settings.llm_provider == "dashscope":
        request_kwargs["extra_body"] = {
            "enable_thinking": settings.llm_enable_thinking
            if enable_thinking is None
            else bool(enable_thinking)
        }

    response = client.chat.completions.create(**request_kwargs)
    content = response.choices[0].message.content or "{}"
    usage_obj = getattr(response, "usage", None)
    usage = (
        {
            "prompt_tokens": getattr(usage_obj, "prompt_tokens", None),
            "completion_tokens": getattr(usage_obj, "completion_tokens", None),
            "total_tokens": getattr(usage_obj, "total_tokens", None),
        }
        if usage_obj is not None
        else {}
    )
    return LLMJsonResult(payload=json_loads_object(content), usage=usage, model=selected_model)
