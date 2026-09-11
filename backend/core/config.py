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


def _resolve_main_repo() -> Path:
    """Return the repository that actually owns the production ``data/`` tree.

    This project is checked out as a *linked* git worktree whose ``data/`` holds
    only a stub database and a partial ``computation/`` subtree; the frozen TEFS
    snapshots and the 1 GB production database live in the main repository.

    A linked worktree's ``.git`` is a file containing ``gitdir: <path>``, whereas
    a main repository's ``.git`` is a directory. That gitdir pointer is kept
    relative so it resolves identically on Windows and under WSL - see
    docs/windows_migration_20260912.md section 6.2.

    Note this deliberately does NOT feed ``Settings.db_path``: leaving that on
    PROJECT_ROOT is what keeps pytest pointed at the worktree stub instead of the
    production database.
    """
    override = os.getenv("HFO2_FERROKG_MAIN_REPO")
    if override:
        return Path(override).expanduser().resolve()

    gitfile = PROJECT_ROOT / ".git"
    if not gitfile.is_file():
        return PROJECT_ROOT

    try:
        lines = gitfile.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return PROJECT_ROOT

    for line in lines:
        if not line.startswith("gitdir:"):
            continue
        target = Path(line.split(":", 1)[1].strip())
        if not target.is_absolute():
            target = PROJECT_ROOT / target
        target = target.resolve()
        # <main repo>/.git/worktrees/<name>  ->  <main repo>
        if target.parent.name == "worktrees" and target.parent.parent.name == ".git":
            return target.parent.parent.parent
    return PROJECT_ROOT


MAIN_REPO = _resolve_main_repo()


@dataclass(frozen=True)
class Settings:
    project_root: Path = PROJECT_ROOT
    db_path: Path = PROJECT_ROOT / os.getenv(
        "HFO2_FERROKG_DB_PATH", "data/hfo2_ferrokg.sqlite3"
    )
    pdf_root: Path = PROJECT_ROOT / os.getenv("HFO2_FERROKG_PDF_ROOT", "data/raw_pdfs")
    log_level: str = os.getenv("HFO2_FERROKG_LOG_LEVEL", "INFO")
    llm_provider: str = os.getenv("HFO2_FERROKG_LLM_PROVIDER", "openai").lower()
    llm_model: str = os.getenv("HFO2_FERROKG_LLM_MODEL", "gpt-4.1-mini")
    llm_base_url: str | None = os.getenv("HFO2_FERROKG_LLM_BASE_URL") or os.getenv(
        "DASHSCOPE_API_BASE_URL"
    )
    llm_timeout_seconds: float = float(os.getenv("HFO2_FERROKG_LLM_TIMEOUT_SECONDS", "60"))
    llm_max_tokens: int = int(os.getenv("HFO2_FERROKG_LLM_MAX_TOKENS", "4000"))
    llm_enable_thinking: bool = os.getenv(
        "HFO2_FERROKG_LLM_ENABLE_THINKING", "false"
    ).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
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


def get_llm_api_key() -> str | None:
    settings = get_settings()
    if settings.llm_provider == "dashscope":
        return os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY") or None
    return get_openai_api_key()


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
