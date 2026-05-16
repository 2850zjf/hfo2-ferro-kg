from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency fallback
    load_dotenv = None


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if load_dotenv is not None:
    load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    project_root: Path = PROJECT_ROOT
    db_path: Path = PROJECT_ROOT / os.getenv(
        "HFO2_FERROKG_DB_PATH", "data/hfo2_ferrokg.sqlite3"
    )
    pdf_root: Path = PROJECT_ROOT / os.getenv("HFO2_FERROKG_PDF_ROOT", "data/raw_pdfs")
    log_level: str = os.getenv("HFO2_FERROKG_LOG_LEVEL", "INFO")
    llm_model: str = os.getenv("HFO2_FERROKG_LLM_MODEL", "gpt-4.1-mini")
    use_llm: bool = os.getenv("HFO2_FERROKG_USE_LLM", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def get_settings() -> Settings:
    return Settings()


def get_openai_api_key() -> str | None:
    return os.getenv("OPENAI_API_KEY") or None


def discover_pdf_root() -> Path:
    """Return the configured PDF root, with a local convenience fallback."""
    settings = get_settings()
    configured = settings.pdf_root
    if configured.exists() and any(configured.rglob("*.pdf")):
        return configured

    sibling_paper_pdf = settings.project_root.parent / "Paper PDF"
    if sibling_paper_pdf.exists() and any(sibling_paper_pdf.rglob("*.pdf")):
        return sibling_paper_pdf

    return configured
