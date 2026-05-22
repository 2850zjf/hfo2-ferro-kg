from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.core.config import PROJECT_ROOT
from backend.services.llm_quota_guard import clear_llm_pause
from backend.services.progress_monitor import active_pipeline_processes


def _creationflags() -> int:
    return subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def start_monitor_server() -> dict[str, Any]:
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [
            sys.executable,
            "scripts/serve_monitor.py",
            "--host",
            "127.0.0.1",
            "--port",
            "8502",
            "--refresh",
            "5",
        ],
        cwd=PROJECT_ROOT,
        stdout=(log_dir / "monitor_server.out.log").open("a", encoding="utf-8"),
        stderr=(log_dir / "monitor_server.err.log").open("a", encoding="utf-8"),
        creationflags=_creationflags(),
    )
    return {"pid": process.pid, "url": "http://127.0.0.1:8502/"}


def start_protected_full_pipeline(
    structured_workers: int = 64,
    llm_workers: int = 64,
    table_timeout: int = 75,
    benchmark_reset: bool = True,
    clear_pause: bool = True,
) -> dict[str, Any]:
    active = active_pipeline_processes()
    active_tasks = [
        row
        for row in active
        if row.get("task") in {"run_full_benchmark_pipeline.py", "05_run_extraction.py", "19_open_benchmark_extraction.py"}
    ]
    if active_tasks:
        return {"started": False, "reason": "pipeline_already_running", "active": active_tasks}

    if clear_pause:
        clear_llm_pause()

    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_log = log_dir / f"full_benchmark_wrapper_{stamp}.out.log"
    err_log = log_dir / f"full_benchmark_wrapper_{stamp}.err.log"
    command = [
        sys.executable,
        "scripts/run_full_benchmark_pipeline.py",
        "--structured-workers",
        str(structured_workers),
        "--llm-workers",
        str(llm_workers),
        "--table-timeout",
        str(table_timeout),
    ]
    if benchmark_reset:
        command.append("--benchmark-reset")
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        stdout=out_log.open("w", encoding="utf-8"),
        stderr=err_log.open("w", encoding="utf-8"),
        creationflags=_creationflags(),
    )
    return {
        "started": True,
        "pid": process.pid,
        "command": " ".join(command),
        "stdout_log": str(out_log),
        "stderr_log": str(err_log),
        "monitor_url": "http://127.0.0.1:8502/",
    }
