from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from backend.db.session import connect


def record_pipeline_run(
    step_name: str,
    status: str,
    stats: dict[str, Any],
    message: str | None = None,
    db_path: Path | None = None,
) -> str:
    run_id = f"run_{uuid.uuid4().hex[:16]}"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO pipeline_runs (run_id, step_name, status, stats_json, message)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                run_id,
                step_name,
                status,
                json.dumps(stats, ensure_ascii=False, sort_keys=True),
                message,
            ),
        )
        conn.commit()
    return run_id


def recent_pipeline_runs(limit: int = 20, db_path: Path | None = None) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT run_id, step_name, status, stats_json, message, created_at
            FROM pipeline_runs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "run_id": row["run_id"],
            "step_name": row["step_name"],
            "status": row["status"],
            "stats": json.loads(row["stats_json"]),
            "message": row["message"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]
