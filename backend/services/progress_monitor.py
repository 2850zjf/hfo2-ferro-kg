from __future__ import annotations

import json
import os
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
    r"processed=(?P<processed>\d+)"
    r".*?candidates=(?P<candidates>\d+)"
    r".*?llm_used=(?P<llm_used>\d+)"
    r".*?llm_failed=(?P<llm_failed>\d+)"
    r".*?empty=(?P<empty>\d+)"
    r".*?errors=(?P<errors>\d+)"
)
BENCHMARK_PROGRESS_RE = re.compile(
    r"benchmark\s+processed=(?P<processed>\d+)\s+written=(?P<written>\d+)\s+empty=(?P<empty>\d+)\s+errors=(?P<errors>\d+)"
)
TABLE_PROGRESS_RE = re.compile(
    r"tables\s+processed=(?P<processed>\d+)\s+tables=(?P<tables>\d+)\s+rows=(?P<rows>\d+)\s+failed=(?P<failed>\d+)"
)
VISUAL_PROGRESS_RE = re.compile(
    r"visual\s+processed=(?P<processed>\d+)\s+captions=(?P<captions>\d+)\s+images=(?P<images>\d+)\s+failed=(?P<failed>\d+)"
)
SAMPLE_LINK_PROGRESS_RE = re.compile(
    r"sample_links\s+processed=(?P<processed>\d+)\s+llm_used=(?P<llm_used>\d+)\s+llm_failed=(?P<llm_failed>\d+)\s+paused=(?P<paused>\d+)"
)
LITERATURE_CARD_PROGRESS_RE = re.compile(
    r"literature_cards\s+processed=(?P<processed>\d+)\s+written=(?P<written>\d+)\s+errors=(?P<errors>\d+)\s+paused=(?P<paused>\d+)"
)
CHUNK_LABEL_PROGRESS_RE = re.compile(
    r"chunk_labels\s+processed=(?P<processed>\d+)\s+written=(?P<written>\d+)\s+errors=(?P<errors>\d+)\s+paused=(?P<paused>\d+)"
)
AI_AUDIT_PROGRESS_RE = re.compile(
    r"ai_audits\s+processed=(?P<processed>\d+)\s+written=(?P<written>\d+)\s+errors=(?P<errors>\d+)\s+paused=(?P<paused>\d+)"
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
    "23_link_sample_facts.py",
    "24_build_design_graph.py",
    "25_recommend_active_learning.py",
    "26_build_literature_cards.py",
    "27_label_chunks_semantically.py",
    "28_ai_audit_sample_links.py",
    "29_build_benchmark_tiers.py",
    "30_train_tiered_design_models.py",
    "31_export_design_report.py",
    "run_rag_job.py",
    "run_full_benchmark_pipeline.py",
    "run_post_sample_link_pipeline.py",
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
        "sample_property_links": "SELECT COUNT(*) FROM sample_property_links",
        "sample_links_strong": "SELECT COUNT(*) FROM sample_property_links WHERE context_quality = 'strong'",
        "sample_links_partial": "SELECT COUNT(*) FROM sample_property_links WHERE context_quality = 'partial'",
        "sample_links_weak": "SELECT COUNT(*) FROM sample_property_links WHERE context_quality = 'weak'",
        "literature_cards": "SELECT COUNT(*) FROM llm_literature_cards WHERE status = 'ok'",
        "chunk_semantic_labels": "SELECT COUNT(*) FROM llm_chunk_labels WHERE status = 'ok'",
        "ai_fact_audits": "SELECT COUNT(*) FROM ai_fact_audits WHERE status = 'ok'",
        "ai_usable_for_model": "SELECT COUNT(*) FROM ai_fact_audits WHERE ai_review_status = 'usable_for_model'",
        "ai_needs_review": "SELECT COUNT(*) FROM ai_fact_audits WHERE ai_review_status = 'needs_human_review'",
    }
    with connect(db_path) as conn:
        for key, sql in queries.items():
            try:
                counts[key] = int(conn.execute(sql).fetchone()[0])
            except Exception:
                counts[key] = 0
    return counts


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _usage_from_payload(payload: dict[str, Any]) -> dict[str, int] | None:
    usage = payload.get("llm_usage")
    if not isinstance(usage, dict):
        preaudit = payload.get("preaudit")
        if isinstance(preaudit, dict):
            usage = preaudit.get("llm_usage")
    if not isinstance(usage, dict):
        return None
    total = _as_int(usage.get("total_tokens"))
    prompt = _as_int(usage.get("prompt_tokens"))
    completion = _as_int(usage.get("completion_tokens"))
    if total <= 0 and (prompt or completion):
        total = prompt + completion
    if total <= 0:
        return None
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }


def _usage_from_json_text(value: str | None) -> dict[str, int] | None:
    if not value:
        return None
    try:
        payload = json.loads(value)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if "llm_usage" in payload and isinstance(payload["llm_usage"], dict):
        payload = payload["llm_usage"]
    total = _as_int(payload.get("total_tokens"))
    prompt = _as_int(payload.get("prompt_tokens"))
    completion = _as_int(payload.get("completion_tokens"))
    if total <= 0 and (prompt or completion):
        total = prompt + completion
    if total <= 0:
        return None
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }


def _estimate_tokens_from_chars(char_count: int | None, base: int = 1800) -> int:
    return max(0, int((char_count or 0) / 3) + base)


def _format_decimal(value: float) -> str:
    text = f"{value:.12f}".rstrip("0").rstrip(".")
    return text or "0"


def _estimate_inflight_tokens(processes: list[dict[str, Any]] | None = None) -> int:
    estimates = {
        "structured_llm": (20, 2600),
        "benchmark_llm": (50, 3200),
        "sample_linking": (50, 3400),
        "literature_cards": (20, 5200),
        "chunk_labels": (50, 2400),
        "ai_audits": (50, 3200),
    }
    total = 0
    for process in processes or active_pipeline_processes():
        logs = _logs_for_task(str(process.get("task") or ""))
        if not logs:
            continue
        progress = parse_progress_from_log(logs[0])
        progress_type = str(progress.get("type") or "")
        processed = _as_int(progress.get("processed"))
        if not processed or progress_type not in estimates:
            continue
        commit_every, tokens_per_item = estimates[progress_type]
        total += (processed % commit_every) * tokens_per_item
    return total


def token_usage(
    db_path: Path | None = None,
    processes: list[dict[str, Any]] | None = None,
) -> dict[str, int | float | str]:
    actual_prompt = 0
    actual_completion = 0
    actual_total = 0
    actual_rows = 0
    estimated_structured = 0
    estimated_benchmark = 0

    with connect(db_path) as conn:
        try:
            rows = conn.execute(
                """
                SELECT ec.payload_json, dc.char_count
                FROM extraction_candidates ec
                LEFT JOIN document_chunks dc ON dc.chunk_id = ec.chunk_id
                """
            ).fetchall()
            for row in rows:
                try:
                    payload = json.loads(row["payload_json"] or "{}")
                except Exception:
                    payload = {}
                usage = _usage_from_payload(payload)
                if usage:
                    actual_rows += 1
                    actual_prompt += usage["prompt_tokens"]
                    actual_completion += usage["completion_tokens"]
                    actual_total += usage["total_tokens"]
                else:
                    estimated_structured += _estimate_tokens_from_chars(row["char_count"])
        except Exception:
            pass

        try:
            rows = conn.execute(
                """
                SELECT be.payload_json, dc.char_count
                FROM benchmark_extractions be
                LEFT JOIN document_chunks dc ON dc.chunk_id = be.chunk_id
                """
            ).fetchall()
            for row in rows:
                try:
                    payload = json.loads(row["payload_json"] or "{}")
                except Exception:
                    payload = {}
                usage = _usage_from_payload(payload)
                if usage:
                    actual_rows += 1
                    actual_prompt += usage["prompt_tokens"]
                    actual_completion += usage["completion_tokens"]
                    actual_total += usage["total_tokens"]
                else:
                    estimated_benchmark += _estimate_tokens_from_chars(row["char_count"], base=2200)
        except Exception:
            pass

        for table in [
            "sample_property_links",
            "llm_literature_cards",
            "llm_chunk_labels",
            "ai_fact_audits",
        ]:
            try:
                rows = conn.execute(f"SELECT llm_usage_json FROM {table}").fetchall()
            except Exception:
                continue
            for row in rows:
                usage = _usage_from_json_text(row["llm_usage_json"])
                if usage:
                    actual_rows += 1
                    actual_prompt += usage["prompt_tokens"]
                    actual_completion += usage["completion_tokens"]
                    actual_total += usage["total_tokens"]

    estimated_total = actual_total + estimated_structured + estimated_benchmark
    input_price = float(
        os.getenv(
            "HFO2_FERROKG_INPUT_TOKEN_PRICE",
            os.getenv("HFO2_FERROKG_PROMPT_TOKEN_PRICE", "0.0000005"),
        )
    )
    output_price = float(
        os.getenv(
            "HFO2_FERROKG_OUTPUT_TOKEN_PRICE",
            os.getenv("HFO2_FERROKG_COMPLETION_TOKEN_PRICE", "0.000002"),
        )
    )
    estimated_input_ratio = float(os.getenv("HFO2_FERROKG_ESTIMATED_INPUT_RATIO", "0.7"))
    estimated_input_ratio = min(1.0, max(0.0, estimated_input_ratio))
    estimated_blended_price = (
        input_price * estimated_input_ratio + output_price * (1.0 - estimated_input_ratio)
    )
    estimated_untracked_tokens = estimated_structured + estimated_benchmark
    actual_input_cost = actual_prompt * input_price
    actual_output_cost = actual_completion * output_price
    actual_cost = actual_input_cost + actual_output_cost
    estimated_untracked_cost = estimated_untracked_tokens * estimated_blended_price
    estimated_cost = actual_cost + estimated_untracked_cost
    estimated_inflight_tokens = _estimate_inflight_tokens(processes)
    estimated_inflight_cost = estimated_inflight_tokens * estimated_blended_price
    estimated_live_total_tokens = estimated_total + estimated_inflight_tokens
    estimated_live_cost = estimated_cost + estimated_inflight_cost
    currency = os.getenv("HFO2_FERROKG_TOKEN_CURRENCY", "CNY")
    return {
        "actual_prompt_tokens": actual_prompt,
        "actual_completion_tokens": actual_completion,
        "actual_total_tokens": actual_total,
        "actual_usage_rows": actual_rows,
        "estimated_structured_tokens": estimated_structured,
        "estimated_benchmark_tokens": estimated_benchmark,
        "estimated_total_tokens": estimated_total,
        "estimated_inflight_tokens": estimated_inflight_tokens,
        "estimated_live_total_tokens": estimated_live_total_tokens,
        "input_price_per_token": input_price,
        "output_price_per_token": output_price,
        "input_price_per_token_display": _format_decimal(input_price),
        "output_price_per_token_display": _format_decimal(output_price),
        "estimated_input_ratio": estimated_input_ratio,
        "estimated_blended_price": estimated_blended_price,
        "estimated_blended_price_display": _format_decimal(estimated_blended_price),
        "actual_input_cost": round(actual_input_cost, 4),
        "actual_output_cost": round(actual_output_cost, 4),
        "estimated_untracked_cost": round(estimated_untracked_cost, 4),
        "estimated_inflight_cost": round(estimated_inflight_cost, 4),
        "actual_cost": round(actual_cost, 4),
        "estimated_cost": round(estimated_cost, 4),
        "estimated_live_cost": round(estimated_live_cost, 4),
        "price_per_token": estimated_blended_price,
        "price_per_token_display": _format_decimal(estimated_blended_price),
        "currency": currency,
        "note": (
            f"按输入 {_format_decimal(input_price)} {currency}/token、输出 "
            f"{_format_decimal(output_price)} {currency}/token 分项计费；"
            f"缺失 usage 的旧记录按 {int(estimated_input_ratio * 100)}% 输入、"
            f"{int((1.0 - estimated_input_ratio) * 100)}% 输出估算。"
        ),
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
    $_.CommandLine -like '*23_link_sample_facts.py*' -or
    $_.CommandLine -like '*24_build_design_graph.py*' -or
    $_.CommandLine -like '*25_recommend_active_learning.py*' -or
    $_.CommandLine -like '*26_build_literature_cards.py*' -or
    $_.CommandLine -like '*27_label_chunks_semantically.py*' -or
    $_.CommandLine -like '*28_ai_audit_sample_links.py*' -or
    $_.CommandLine -like '*29_build_benchmark_tiers.py*' -or
    $_.CommandLine -like '*30_train_tiered_design_models.py*' -or
    $_.CommandLine -like '*31_export_design_report.py*' -or
    $_.CommandLine -like '*run_rag_job.py*' -or
    $_.CommandLine -like '*run_full_benchmark_pipeline.py*' -or
    $_.CommandLine -like '*run_post_sample_link_pipeline.py*'
  )
} |
Select-Object ProcessId,Name,CommandLine,@{Name='StartedAt';Expression={$_.CreationDate.ToString('o')}} |
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
                "started_at": str(item.get("StartedAt") or ""),
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
    parsers = [
        ("structured_llm", LLM_PROGRESS_RE),
        ("benchmark_llm", BENCHMARK_PROGRESS_RE),
        ("sample_linking", SAMPLE_LINK_PROGRESS_RE),
        ("literature_cards", LITERATURE_CARD_PROGRESS_RE),
        ("chunk_labels", CHUNK_LABEL_PROGRESS_RE),
        ("ai_audits", AI_AUDIT_PROGRESS_RE),
        ("table_extraction", TABLE_PROGRESS_RE),
        ("visual_assets", VISUAL_PROGRESS_RE),
    ]
    matches: list[tuple[int, str, re.Match[str]]] = []
    for progress_type, regex in parsers:
        for match in regex.finditer(text):
            matches.append((match.start(), progress_type, match))
    if not matches:
        return {"type": "unknown"}
    _, progress_type, match = sorted(matches, key=lambda item: item[0])[-1]
    return {"type": progress_type, **{key: int(value) for key, value in match.groupdict().items()}}


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


def _active_step(starts: list[re.Match[str]], ends: dict[str, re.Match[str]]) -> re.Match[str]:
    for start in reversed(starts):
        if start.group("step") not in ends:
            return start
    return starts[-1]


def _progress_total(progress_type: str | None, processed: int) -> int | None:
    counts = database_counts()
    if progress_type == "structured_llm":
        return counts.get("high_value_chunks")
    if progress_type == "benchmark_llm":
        return counts.get("document_chunks")
    if progress_type == "sample_linking":
        return counts.get("reviewed_facts", 0) + counts.get("benchmark_ok", 0)
    if progress_type == "literature_cards":
        return counts.get("pdf_files")
    if progress_type == "chunk_labels":
        return counts.get("high_value_chunks")
    if progress_type == "ai_audits":
        return counts.get("sample_property_links")
    if progress_type == "table_extraction":
        return processed + counts.get("table_pending_pdfs", 0)
    if progress_type == "visual_assets":
        return counts.get("parsed_pdfs")
    return None


def parse_runtime_from_log(
    path: Path,
    fallback_started_at: str | None = None,
    fallback_step: str | None = None,
) -> dict[str, Any]:
    text = tail_file(path, max_chars=80000)
    starts = list(STEP_START_RE.finditer(text))
    ends = {match.group("step"): match for match in STEP_END_RE.finditer(text)}
    if not starts:
        progress = parse_progress_from_log(path)
        processed = int(progress.get("processed") or 0) if progress.get("type") != "unknown" else 0
        try:
            start_time = datetime.fromisoformat(str(fallback_started_at)) if fallback_started_at else datetime.fromtimestamp(path.stat().st_ctime)
        except Exception:
            start_time = datetime.fromtimestamp(path.stat().st_ctime)
        now = datetime.now(start_time.tzinfo) if start_time.tzinfo else datetime.now()
        elapsed_seconds = max(0.0, (now - start_time).total_seconds())
        rate = processed / (elapsed_seconds / 60) if processed and elapsed_seconds else 0.0
        eta_seconds = None
        total = _progress_total(str(progress.get("type")), processed)
        if total and processed and rate and processed < total:
            eta_seconds = (total - processed) / (rate / 60)
        return {
            "started_at": start_time.isoformat(timespec="seconds"),
            "active_step": fallback_step or str(progress.get("type") or "等待日志"),
            "elapsed_seconds": int(elapsed_seconds),
            "active_elapsed_seconds": int(elapsed_seconds),
            "elapsed": format_duration(elapsed_seconds),
            "active_elapsed": format_duration(elapsed_seconds),
            "eta": format_duration(eta_seconds),
            "rate_per_min": round(rate, 2),
        }
    now = datetime.now()
    first_start_time = datetime.fromisoformat(starts[0].group("stamp"))
    active_start = _active_step(starts, ends)
    active_start_time = datetime.fromisoformat(active_start.group("stamp"))
    elapsed_seconds = max(0.0, (now - first_start_time).total_seconds())
    active_elapsed_seconds = max(0.0, (now - active_start_time).total_seconds())
    progress = parse_progress_from_log(path)
    processed = int(progress.get("processed") or 0) if progress.get("type") != "unknown" else 0
    rate = processed / (active_elapsed_seconds / 60) if processed and active_elapsed_seconds else 0.0
    eta_seconds = None
    if processed and rate:
        progress_type = progress.get("type")
        total = _progress_total(str(progress_type), processed)
        if total and processed < total:
            eta_seconds = (total - processed) / (rate / 60)
    return {
        "started_at": starts[0].group("stamp"),
        "active_step": active_start.group("step"),
        "elapsed_seconds": int(elapsed_seconds),
        "active_elapsed_seconds": int(active_elapsed_seconds),
        "elapsed": format_duration(elapsed_seconds),
        "active_elapsed": format_duration(active_elapsed_seconds),
        "eta": format_duration(eta_seconds),
        "rate_per_min": round(rate, 2),
    }


def _logs_for_task(task: str) -> list[Path]:
    log_dir = PROJECT_ROOT / "logs"
    if not log_dir.exists():
        return []
    patterns = {
        "23_link_sample_facts.py": ["sample_linking*.out.log", "sample_linking_qwen37_*.out.log"],
        "26_build_literature_cards.py": ["literature_cards*.out.log", "full_benchmark_pipeline_*.log"],
        "27_label_chunks_semantically.py": ["chunk_labels*.out.log", "full_benchmark_pipeline_*.log"],
        "28_ai_audit_sample_links.py": ["ai_audits*.out.log", "full_benchmark_pipeline_*.log"],
        "05_run_extraction.py": ["full_qwen_extraction_*.log", "full_benchmark_pipeline_*.log"],
        "19_open_benchmark_extraction.py": ["full_benchmark_pipeline_*.log"],
        "run_full_benchmark_pipeline.py": ["full_benchmark_pipeline_*.log", "full_benchmark_wrapper_*.out.log"],
        "run_post_sample_link_pipeline.py": ["post_sample_link_pipeline_*.log"],
    }
    files: list[Path] = []
    for pattern in patterns.get(task, ["*.log"]):
        files.extend(path for path in log_dir.glob(pattern) if path.is_file())
    return sorted(set(files), key=lambda path: path.stat().st_mtime, reverse=True)


def latest_pipeline_runtime(processes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    log_dir = PROJECT_ROOT / "logs"
    preferred_processes = sorted(
        processes or [],
        key=lambda item: 1 if str(item.get("task") or "") == "run_post_sample_link_pipeline.py" else 0,
    )
    for process in preferred_processes:
        logs = _logs_for_task(str(process.get("task") or ""))
        if logs:
            return parse_runtime_from_log(
                logs[0],
                fallback_started_at=str(process.get("started_at") or ""),
                fallback_step=str(process.get("task") or ""),
            ) | {"log": str(logs[0])}
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
            "active_elapsed_seconds": 0,
            "elapsed": "0s",
            "active_elapsed": "0s",
            "eta": "估算中",
            "rate_per_min": 0.0,
        }
    return parse_runtime_from_log(logs[0]) | {"log": str(logs[0])}


def monitor_snapshot() -> dict[str, Any]:
    counts = database_counts()
    processes = active_pipeline_processes()
    runtime = latest_pipeline_runtime(processes)
    tokens = token_usage(processes=processes)
    snapshot = {
        "counts": counts,
        "processes": processes,
        "logs": latest_logs(),
        "recent_runs": recent_pipeline_runs(limit=12),
        "active": bool(processes),
        "runtime": runtime,
        "tokens": tokens,
        "llm_pause": read_llm_pause(),
    }
    snapshot.update(counts)
    snapshot.update(
        {
            "status": "running" if processes else "idle",
            "active_processes": len(processes),
            "llm_paused": bool(snapshot["llm_pause"]),
            "actual_total_tokens": tokens.get("actual_total_tokens", 0),
            "estimated_total_tokens": tokens.get("estimated_total_tokens", 0),
            "estimated_live_total_tokens": tokens.get("estimated_live_total_tokens", 0),
            "actual_cost_cny": tokens.get("actual_cost", 0),
            "estimated_cost_cny": tokens.get("estimated_cost", 0),
            "estimated_live_cost_cny": tokens.get("estimated_live_cost", 0),
            "elapsed": runtime.get("elapsed", "0s"),
            "eta": runtime.get("eta", "估算中"),
            "rate_per_min": runtime.get("rate_per_min", 0),
        }
    )
    return snapshot
