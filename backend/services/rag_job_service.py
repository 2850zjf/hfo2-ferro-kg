from __future__ import annotations

import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.core.config import PROJECT_ROOT, get_settings
from backend.db.session import connect
from backend.services.rag_answerer import answer_question


TERMINAL_STATUSES = {"completed", "failed"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def create_rag_job(
    question: str,
    *,
    use_llm: bool = True,
    llm_model: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    job_id = f"rag_{uuid.uuid4().hex[:16]}"
    model = llm_model or get_settings().llm_model
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO rag_jobs (
                job_id, question, use_llm, llm_model, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'queued', ?, ?)
            """,
            (job_id, question, 1 if use_llm else 0, model, _now(), _now()),
        )
        conn.commit()
    return get_rag_job(job_id, db_path=db_path)


def get_rag_job(job_id: str, *, db_path: Path | None = None) -> dict[str, Any]:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM rag_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    return _row_to_dict(row)


def list_rag_jobs(*, limit: int = 12, db_path: Path | None = None) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM rag_jobs
            ORDER BY datetime(created_at) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def update_rag_job(
    job_id: str,
    *,
    status: str | None = None,
    answer_markdown: str | None = None,
    error_message: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    updates: list[str] = ["updated_at = ?"]
    values: list[Any] = [_now()]
    if status is not None:
        updates.append("status = ?")
        values.append(status)
    if answer_markdown is not None:
        updates.append("answer_markdown = ?")
        values.append(answer_markdown)
    if error_message is not None:
        updates.append("error_message = ?")
        values.append(error_message)
    if started_at is not None:
        updates.append("started_at = ?")
        values.append(started_at)
    if completed_at is not None:
        updates.append("completed_at = ?")
        values.append(completed_at)
    values.append(job_id)
    with connect(db_path) as conn:
        conn.execute(
            f"UPDATE rag_jobs SET {', '.join(updates)} WHERE job_id = ?",
            tuple(values),
        )
        conn.commit()
    return get_rag_job(job_id, db_path=db_path)


def run_rag_job(job_id: str, *, db_path: Path | None = None) -> dict[str, Any]:
    job = get_rag_job(job_id, db_path=db_path)
    if not job:
        raise ValueError(f"Unknown RAG job: {job_id}")
    if job.get("status") in TERMINAL_STATUSES:
        return job

    update_rag_job(job_id, status="running", started_at=_now(), db_path=db_path)
    try:
        answer = answer_question(
            str(job["question"]),
            use_llm=bool(job.get("use_llm")),
            llm_model=str(job.get("llm_model") or get_settings().llm_model),
            db_path=db_path,
        )
    except Exception as exc:
        return update_rag_job(
            job_id,
            status="failed",
            error_message=str(exc),
            completed_at=_now(),
            db_path=db_path,
        )

    return update_rag_job(
        job_id,
        status="completed",
        answer_markdown=answer,
        error_message="",
        completed_at=_now(),
        db_path=db_path,
    )


def start_background_rag_job(
    question: str,
    *,
    use_llm: bool = True,
    llm_model: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    job = create_rag_job(question, use_llm=use_llm, llm_model=llm_model, db_path=db_path)
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{job['job_id']}.out.log"
    stderr_path = log_dir / f"{job['job_id']}.err.log"
    command = [sys.executable, "scripts/run_rag_job.py", str(job["job_id"])]
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        stdout=stdout_path.open("w", encoding="utf-8"),
        stderr=stderr_path.open("w", encoding="utf-8"),
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )
    job["pid"] = process.pid
    job["stdout_log"] = str(stdout_path)
    job["stderr_log"] = str(stderr_path)
    return job
