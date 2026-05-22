from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.core.config import PROJECT_ROOT


PAUSE_PATH = PROJECT_ROOT / "data" / "runtime" / "llm_pause.json"

_BUDGET_PATTERNS = [
    "insufficient_quota",
    "quota exceeded",
    "quotaexceeded",
    "quota_exceeded",
    "exceeded your current quota",
    "billing",
    "balance",
    "credit",
    "credits",
    "arrearage",
    "resource exhausted",
    "resource_exhausted",
    "rate limit",
    "rate_limit",
    "too many requests",
    "429",
    "invalid api key",
    "invalid_api_key",
    "unauthorized",
    "authentication",
    "access denied",
    "accessdenied",
    "api key",
    "api-key",
]


def is_llm_budget_error(error_message: str | None) -> bool:
    text = str(error_message or "").lower()
    if not text:
        return False
    return any(pattern in text for pattern in _BUDGET_PATTERNS)


def write_llm_pause(reason: str, context: dict[str, Any] | None = None) -> Path:
    PAUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "paused",
        "reason": reason[:2000],
        "context": context or {},
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "resume_hint": (
            "配置新的 API key 或额度恢复后，重新运行同一条 pipeline。"
            "已完成的 chunk 会保留，未完成的 chunk 会继续处理。"
        ),
    }
    PAUSE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return PAUSE_PATH


def read_llm_pause() -> dict[str, Any] | None:
    if not PAUSE_PATH.exists():
        return None
    try:
        return json.loads(PAUSE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"status": "paused", "reason": PAUSE_PATH.read_text(encoding="utf-8", errors="replace")}


def clear_llm_pause() -> None:
    try:
        PAUSE_PATH.unlink()
    except FileNotFoundError:
        pass
