from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.llm_quota_guard import read_llm_pause
from backend.services.pipeline_log import recent_pipeline_runs


LLM_PROGRESS_RE = re.compile(
    r"processed=(?P<processed>\d+).*?llm_used=(?P<llm_used>\d+).*?llm_failed=(?P<llm_failed>\d+).*?empty=(?P<empty>\d+).*?errors=(?P<errors>\d+)"
)
BENCHMARK_PROGRESS_RE = re.compile(
    r"benchmark\s+processed=(?P<processed>\d+)\s+written=(?P<written>\d+)\s+empty=(?P<empty>\d+)\s+errors=(?P<errors>\d+)"
)
STEP_START_RE = re.compile(r"===== (?P<step>.+?) started (?P<stamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}) =====")
STEP_END_RE = re.compile(r"===== (?P<step>.+?) exit_code=(?P<code>-?\d+) ended (?P<stamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}) =====")


WATCH_PATTERNS = [
    "01_build_manifest.py",
    "02_parse_pdfs.py",
    "03_extract_tables.py",
    "04_chunk_documents.py",
    "05_run_extraction.py",
    "15_extract_visual_assets.py",
    "16_enrich_fact_relations.py",
    "17_export_graph_html.py",
    "18_multi_model_validate_dataset.py",
    "19_open_benchmark_extraction.py",
    "20_update_ontology_from_benchmark.py",
    "run_full_benchmark_pipeline.py",
]


def database_counts(db_path: Path | None = None) -> dict[str, int]:
    counts: dict[str, int] = {}
    queries = {
        "pdf_files": "SELECT COUNT(*) FROM pdf_files",
        "duplicate_pdfs": "SELECT COUNT(*) FROM pdf_files WHERE is_duplicate = 1",
        "parsed_pdfs": "SELECT COUNT(*) FROM pdf_files WHERE parse_status = 'parsed'",
        "table_pending_pdfs": "SELECT COUNT(*) FROM pdf_files WHERE parse_status='parsed' AND COALESCE(table_parse_status,'pending')!='parsed'",
        "parsed_pages": "SELECT COUNT(*) FROM parsed_pages",
        "pdf_tables": "SELECT COUNT(*) FROM pdf_tables",
        "visual_assets": "SELECT COUNT(*) FROM pdf_visual_assets",
        "document_chunks": "SELECT COUNT(*) FROM document_chunks",
        "high_value_chunks": "SELECT COUNT(*) FROM document_chunks WHERE is_high_value = 1",
        "extraction_candidates": "SELECT COUNT(*) FROM extraction_candidates",
        "reviewed_facts": "SELECT COUNT(*) FROM reviewed_facts",
        "benchmark_extractions": "SELECT COUNT(*) FROM benchmark_extractions",
        "benchmark_ok": "SELECT COUNT(*) FROM benchmark_extractions WHERE status = 'ok'",
        "benchmark_empty": "SELECT COUNT(*) FROM benchmark_extractions WHERE status = 'empty_result'",
        "benchmark_error": "SELECT COUNT(*) FROM benchmark_extractions WHERE status = 'error'",
    }
    with connect(db_path) as conn:
        for key, sql in queries.items():
            try:
                counts[key] = int(conn.execute(sql).fetchone()[0])
            except Exception:
                counts[key] = 0
    return counts


def token_usage(db_path: Path | None = None) -> dict[str, int | str]:
    actual_prompt = 0
    actual_completion = 0
    actual_total = 0
    actual_rows = 0
    estimated_structured = 0
    estimated_benchmark = 0
    with connect(db_path) as conn:
        try:
            rows = conn.execute("SELECT payload_json FROM benchmark_extractions").fetchall()
            for row in rows:
                payload = json.loads(row["payload_json"])
                usage = payload.get("llm_usage") or {}
                total = usage.get("total_tokens")
                if isinstance(total, int):
                    actual_rows += 1
                    actual_total += total
                    actual_prompt += int(usage.get("prompt_tokens") or 0)
                    actual_completion += int(usage.get("completion_tokens") or 0)
                else:
                    estimated_benchmark += 4200
        except Exception:
            pass
        try:
            structured_rows = conn.execute(
                """
                SELECT dc.char_count
                FROM extraction_candidates ec
                LEFT JOIN document_chunks dc ON dc.chunk_id = ec.chunk_id
                WHERE ec.extractor_version LIKE 'llm%'
                """
            ).fetchall()
            for row in structured_rows:
                estimated_structured += int((row["char_count"] or 0) / 3) + 1800
        except Exception:
            pass
    estimated_total = actual_total + estimated_structured + estimated_benchmark
    return {
        "actual_prompt_tokens": actual_prompt,
        "actual_completion_tokens": actual_completion,
        "actual_total_tokens": actual_total,
        "actual_usage_rows": actual_rows,
        "estimated_structured_tokens": estimated_structured,
        "estimated_benchmark_tokens": estimated_benchmark,
        "estimated_total_tokens": estimated_total,
        "note": "真实 usage 只统计新记录到 llm_usage 的调用；其余按 chunk 长度和输出预算估算。",
    }


def active_pipeline_processes() -> list[dict[str, str | int]]:
    command = """
Get-CimInstance Win32_Process |
Where-Object {
  $_.Name -in @('python.exe','powershell.exe') -and (
    $_.CommandLine -like '*01_build_manifest.py*' -or
    $_.CommandLine -like '*02_parse_pdfs.py*' -or
    $_.CommandLine -like '*03_extract_tables.py*' -or
    $_.CommandLine -like '*04_chunk_documents.py*' -or
    $_.CommandLine -like '*05_run_extraction.py*' -or
    $_.CommandLine -like '*15_extract_visual_assets.py*' -or
    $_.CommandLine -like '*16_enrich_fact_relations.py*' -or
    $_.CommandLine -like '*17_export_graph_html.py*' -or
    $_.CommandLine -like '*18_multi_model_validate_dataset.py*' -or
    $_.CommandLine -like '*19_open_benchmark_extraction.py*' -or
    $_.CommandLine -like '*20_update_ontology_from_benchmark.py*' -or
    $_.CommandLine -like '*run_full_benchmark_pipeline.py*'
  )
} |
Select-Object ProcessId,Name,CommandLine |
ConvertTo-Json -Compress
"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
    except Exception:
        return []
    text = result.stdout.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    rows: list[dict[str, str | int]] = []
    current = str(Path(__file__).resolve())
    for item in data:
        command_line = str(item.get("CommandLine") or "")
        if current in command_line or "Get-CimInstance Win32_Process" in command_line:
            continue
        rows.append(
            {
                "pid": int(item.get("ProcessId") or 0),
                "name": str(item.get("Name") or ""),
                "task": infer_task_name(command_line),
                "command": command_line,
            }
        )
    return rows


def infer_task_name(command_line: str) -> str:
    for pattern in WATCH_PATTERNS:
        if pattern in command_line:
            return pattern
    return "pipeline"


def latest_logs(limit: int = 8) -> list[dict[str, Any]]:
    log_dir = PROJECT_ROOT / "logs"
    if not log_dir.exists():
        return []
    files = sorted(
        [path for path in log_dir.glob("*.log") if path.is_file()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:limit]
    return [
        {
            "name": path.name,
            "path": str(path),
            "size": path.stat().st_size,
            "modified": path.stat().st_mtime,
            "tail": tail_file(path),
            "progress": parse_progress_from_log(path),
            "runtime": parse_runtime_from_log(path),
        }
        for path in files
    ]


def tail_file(path: Path, max_chars: int = 5000) -> str:
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - max_chars * 2))
            data = fh.read()
        text = data.decode("utf-8", errors="replace").replace("\x00", "")
        return text[-max_chars:]
    except Exception as exc:
        return f"无法读取日志：{exc}"


def parse_progress_from_log(path: Path) -> dict[str, int | str]:
    text = tail_file(path, max_chars=12000)
    llm_matches = [match.groupdict() for match in LLM_PROGRESS_RE.finditer(text)]
    if llm_matches:
        latest = llm_matches[-1]
        return {"type": "structured_llm", **{key: int(value) for key, value in latest.items()}}
    benchmark_matches = [match.groupdict() for match in BENCHMARK_PROGRESS_RE.finditer(text)]
    if benchmark_matches:
        latest = benchmark_matches[-1]
        return {"type": "benchmark_llm", **{key: int(value) for key, value in latest.items()}}
    return {"type": "unknown"}


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "估算中"
    seconds = int(max(0, seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def parse_runtime_from_log(path: Path) -> dict[str, Any]:
    text = tail_file(path, max_chars=80000)
    starts = list(STEP_START_RE.finditer(text))
    ends = {match.group("step"): match for match in STEP_END_RE.finditer(text)}
    if not starts:
        return {
            "started_at": None,
            "active_step": "等待日志",
            "elapsed_seconds": 0,
            "elapsed": "0s",
            "eta": "估算中",
            "rate_per_min": 0.0,
        }
    first_start = datetime.fromisoformat(starts[0].group("stamp"))
    now = datetime.now()
    active_step = starts[-1].group("step")
    for start in reversed(starts):
        if start.group("step") not in ends:
            active_step = start.group("step")
            break
    elapsed_seconds = max(0.0, (now - first_start).total_seconds())
    progress = parse_progress_from_log(path)
    processed = int(progress.get("processed") or 0) if progress.get("type") != "unknown" else 0
    rate = processed / (elapsed_seconds / 60) if processed and elapsed_seconds else 0.0
    eta_seconds = None
    if processed and rate:
        total = None
        if progress.get("type") == "structured_llm":
            counts = database_counts()
            total = counts.get("high_value_chunks")
        elif progress.get("type") == "benchmark_llm":
            counts = database_counts()
            total = counts.get("document_chunks")
        if total and processed < total:
            eta_seconds = (total - processed) / (rate / 60)
    return {
        "started_at": starts[0].group("stamp"),
        "active_step": active_step,
        "elapsed_seconds": int(elapsed_seconds),
        "elapsed": format_duration(elapsed_seconds),
        "eta": format_duration(eta_seconds),
        "rate_per_min": round(rate, 2),
    }


def latest_pipeline_runtime() -> dict[str, Any]:
    log_dir = PROJECT_ROOT / "logs"
    logs = sorted(
        log_dir.glob("full_benchmark_pipeline_*.log"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not logs:
        return {
            "started_at": None,
            "active_step": "等待启动",
            "elapsed_seconds": 0,
            "elapsed": "0s",
            "eta": "估算中",
            "rate_per_min": 0.0,
        }
    return parse_runtime_from_log(logs[0]) | {"log": str(logs[0])}


def monitor_snapshot() -> dict[str, Any]:
    counts = database_counts()
    processes = active_pipeline_processes()
    return {
        "counts": counts,
        "processes": processes,
        "logs": latest_logs(),
        "recent_runs": recent_pipeline_runs(limit=12),
        "active": bool(processes),
        "runtime": latest_pipeline_runtime(),
        "tokens": token_usage(),
        "llm_pause": read_llm_pause(),
    }
