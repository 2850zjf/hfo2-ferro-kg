from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.computation_planner import plan_computational_feedback_tasks
from backend.services.computation_validation import (
    DEFAULT_JOBS_DIR,
    import_computation_results,
    list_computation_jobs,
    prepare_computation_validation_jobs,
)
from backend.services.pipeline_log import record_pipeline_run
from backend.services.simulation_runtime import check_simulation_runtime


DEFAULT_WORKFLOW_DIR = PROJECT_ROOT / "data" / "computation" / "workflow"
DEFAULT_CLOUD_DIR = PROJECT_ROOT / "data" / "computation" / "cloud"


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _update_job_status(
    job_id: str,
    *,
    status: str,
    safety_status: str,
    error_message: str | None = None,
    db_path: Path | None = None,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE computation_jobs
            SET status = ?,
                safety_status = ?,
                error_message = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE job_id = ?
            """,
            (status, safety_status, error_message, job_id),
        )
        conn.commit()


def write_tencent_cloud_template(output_dir: Path | None = None) -> dict[str, Any]:
    """Write a non-secret cloud connector template.

    This creates documentation only. It does not store credentials and does not submit jobs.
    """

    target_dir = output_dir or DEFAULT_CLOUD_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    config_path = target_dir / "tencent_cloud_compute_config.template.json"
    readme_path = target_dir / "tencent_cloud_compute_runner.md"
    config = {
        "provider": "tencent_cloud",
        "mode": "template_only",
        "ssh": {
            "host": "FILL_ON_LOCAL_MACHINE",
            "user": "FILL_ON_LOCAL_MACHINE",
            "port": 22,
            "key_path_env": "HFO2_FERROKG_TENCENT_SSH_KEY_PATH",
        },
        "remote": {
            "work_root": "/path/on/tencent_cloud/hfo2-ferro-kg-compute",
            "scheduler": "slurm|pbs|bash",
            "vasp_command": "srun vasp_std|mpirun -np N vasp_std|vasp_std",
            "ferrox_command": "mpirun -n N /path/to/FerroX/main3d...ex inputs_hzo_mfim",
            "jax_python_command": "python computations/simulation/jax_landau_smoke.py",
            "python_command": "python",
            "potcar_policy": "POTCAR stays on licensed compute machine and is never committed.",
        },
        "local": {
            "job_packages_dir": str(DEFAULT_JOBS_DIR),
            "results_import_command": "python3 pipelines/45_import_computation_results.py <results.csv> --job-id <job_id>",
        },
        "safety": {
            "store_secrets_in_this_file": False,
            "requires_manual_confirmation_before_submit": True,
            "default_submit_mode": "dry_run",
        },
    }
    _write_json(config_path, config)
    readme_path.write_text(
        """# Tencent Cloud Compute Runner Template

This is a configuration template for the HfO2-FerroKG computation workflow.

It does not submit jobs and does not contain secrets.

## Required user-provided information

- Tencent Cloud host, user, port, and SSH key path stored in an environment variable.
- Remote working directory.
- Scheduler type: Slurm, PBS, or plain bash.
- VASP command and module/conda activation commands on the remote machine.
- FerroX executable and MPI/GPU runtime selected on the remote machine.
- POTCAR location on the licensed machine.
- Whether Materials Project structure fetching is done locally or on the remote machine.

## Safety boundary

- No SSH key, password, API key, POTCAR, or cloud token should be written into this repo.
- First cloud submission should be dry-run only.
- Start with HfO2 phase-stability smoke test before HZO/dopant/interface jobs.
- Use the local JAX/FerroX checks only as runtime smoke tests; calibrate coefficients before science runs.
""",
        encoding="utf-8",
    )
    return {
        "status": "template_written",
        "config_template": str(config_path),
        "readme": str(readme_path),
    }


def fetch_mp_structures_for_prepared_jobs(
    max_jobs: int = 4,
    jobs_dir: Path | None = None,
    timeout_s: int = 300,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Fetch Materials Project structures for prepared phase-stability jobs.

    This uses the per-job 00_fetch_mp_structures.sh script. It does not run VASP,
    does not create POTCAR, and does not submit local/cloud jobs.
    """

    if not os.getenv("MP_API_KEY"):
        return {
            "status": "blocked_missing_mp_api_key",
            "attempted": 0,
            "safety_note": "MP_API_KEY is required in the shell environment. Do not write it into files.",
        }

    all_jobs = list_computation_jobs(limit=10000, db_path=db_path)
    candidates = [
        row
        for row in all_jobs
        if row.get("task_family") == "phase_stability"
        and row.get("status") in {"prepared", "structure_fetch_failed", "structures_ready"}
    ]
    selected = candidates[:max_jobs]
    runs: list[dict[str, Any]] = []
    for job in selected:
        job_id = str(job.get("job_id"))
        work_dir = Path(str(job.get("work_dir") or ""))
        script = work_dir / "00_fetch_mp_structures.sh"
        log_path = work_dir / "00_fetch_mp_structures.log"
        run = {
            "job_id": job_id,
            "work_dir": str(work_dir),
            "script": str(script),
            "status": "not_run",
            "log_path": str(log_path),
        }
        if not script.exists():
            run["status"] = "missing_script"
            _update_job_status(
                job_id,
                status="structure_fetch_failed",
                safety_status="structure_fetch_script_missing",
                error_message="00_fetch_mp_structures.sh not found.",
                db_path=db_path,
            )
            runs.append(run)
            continue
        try:
            result = subprocess.run(
                [str(script)],
                cwd=work_dir,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout_s,
                check=False,
            )
            log_path.write_text(result.stdout or "", encoding="utf-8")
            run["returncode"] = result.returncode
            mp_dir = work_dir / "mp_structures"
            poscars = list(mp_dir.rglob("POSCAR")) if mp_dir.exists() else []
            run["poscars"] = len(poscars)
            if result.returncode == 0 and poscars:
                run["status"] = "structures_ready"
                _update_job_status(
                    job_id,
                    status="structures_ready",
                    safety_status="structures_fetched_no_execution",
                    db_path=db_path,
                )
            else:
                run["status"] = "structure_fetch_failed"
                _update_job_status(
                    job_id,
                    status="structure_fetch_failed",
                    safety_status="structure_fetch_failed_no_execution",
                    error_message=(result.stdout or "")[-500:],
                    db_path=db_path,
                )
        except subprocess.TimeoutExpired as exc:
            log_path.write_text(str(exc), encoding="utf-8")
            run["status"] = "structure_fetch_timeout"
            _update_job_status(
                job_id,
                status="structure_fetch_failed",
                safety_status="structure_fetch_timeout_no_execution",
                error_message=str(exc)[:500],
                db_path=db_path,
            )
        runs.append(run)

    ok = sum(1 for row in runs if row["status"] == "structures_ready")
    return {
        "status": "ok",
        "attempted": len(runs),
        "structures_ready": ok,
        "runs": runs,
        "safety_note": "Only Materials Project structures were fetched; no VASP, MD, SSH, or cloud job was launched.",
    }


def _workflow_report(stats: dict[str, Any]) -> str:
    lines = [
        "# Automatic Computation Workflow Report",
        "",
        f"- generated_at: {stats.get('generated_at')}",
        f"- safety_mode: {stats.get('safety_mode')}",
        f"- workflow_dir: `{stats.get('workflow_dir')}`",
        "",
        "## Stage Status",
        "",
    ]
    for name, payload in stats.get("stages", {}).items():
        status = payload.get("status") if isinstance(payload, dict) else "unknown"
        lines.append(f"- {name}: {status}")
    lines.extend(["", "## Safety Boundary", ""])
    lines.extend(
        [
            "- Default mode does not launch VASP, molecular dynamics, SSH, or cloud jobs.",
            "- JAX and FerroX runtime detection does not launch a phase-field simulation.",
            "- Materials Project fetching is optional and requires `MP_API_KEY` in the shell.",
            "- POTCAR and cloud credentials must stay outside the repository.",
            "- Computed results are imported only from explicit CSV files.",
        ]
    )
    if "next_steps" in stats:
        lines.extend(["", "## Next Steps", ""])
        lines.extend([f"- {item}" for item in stats["next_steps"]])
    return "\n".join(lines) + "\n"


def run_computation_workflow(
    *,
    candidates_path: Path | None = None,
    output_dir: Path | None = None,
    jobs_dir: Path | None = None,
    workflow_dir: Path | None = None,
    max_candidates: int = 20,
    max_tasks: int = 80,
    max_jobs: int = 12,
    task_family: str | None = "phase_stability",
    force_prepare: bool = False,
    fetch_mp_structures: bool = False,
    max_structure_fetch_jobs: int = 4,
    import_results_path: Path | None = None,
    import_job_id: str | None = None,
    write_cloud_template: bool = True,
    db_path: Path | None = None,
    runtime_status_path: Path | None = None,
) -> dict[str, Any]:
    """Run the safe automatic computation workflow.

    This orchestrates planning, package preparation, optional MP structure fetching,
    optional result import, and cloud template writing. It does not submit compute jobs.
    """

    target_workflow_dir = workflow_dir or DEFAULT_WORKFLOW_DIR
    target_workflow_dir.mkdir(parents=True, exist_ok=True)
    target_jobs_dir = jobs_dir or DEFAULT_JOBS_DIR
    stages: dict[str, Any] = {}

    # check_simulation_runtime binds status_path=DEFAULT_STATUS_PATH as a default
    # argument, so passing None here would override that default with None rather
    # than fall back to it. Only forward the override when one was given.
    runtime_kwargs: dict[str, Any] = {
        "run_jax_smoke": False,
        "run_ferrox_smoke": False,
    }
    if runtime_status_path is not None:
        runtime_kwargs["status_path"] = runtime_status_path
    stages["simulation_runtime"] = check_simulation_runtime(**runtime_kwargs)

    plan_stats = plan_computational_feedback_tasks(
        candidates_path=candidates_path,
        output_dir=output_dir,
        max_candidates=max_candidates,
        max_tasks=max_tasks,
        db_path=db_path,
    )
    stages["plan"] = plan_stats

    prepare_stats = prepare_computation_validation_jobs(
        tasks_path=Path(plan_stats["output_csv"]) if plan_stats.get("output_csv") else None,
        jobs_dir=target_jobs_dir,
        max_jobs=max_jobs,
        task_family=task_family,
        execution_mode="automatic_safe_prepare",
        force=force_prepare,
        db_path=db_path,
    )
    stages["prepare"] = prepare_stats

    if fetch_mp_structures:
        stages["fetch_mp_structures"] = fetch_mp_structures_for_prepared_jobs(
            max_jobs=max_structure_fetch_jobs,
            jobs_dir=target_jobs_dir,
            db_path=db_path,
        )
    else:
        stages["fetch_mp_structures"] = {
            "status": "skipped",
            "reason": "Pass --fetch-mp-structures to fetch Materials Project structures.",
        }

    if import_results_path:
        stages["import_results"] = import_computation_results(
            import_results_path,
            job_id=import_job_id,
            db_path=db_path,
        )
    else:
        stages["import_results"] = {
            "status": "skipped",
            "reason": "Pass --import-results <csv> after calculations complete.",
        }

    if write_cloud_template:
        stages["cloud_template"] = write_tencent_cloud_template()
    else:
        stages["cloud_template"] = {"status": "skipped"}

    with connect(db_path) as conn:
        job_count = conn.execute("SELECT COUNT(*) FROM computation_jobs").fetchone()[0]
        result_count = conn.execute("SELECT COUNT(*) FROM computation_results").fetchone()[0]

    stats = {
        "status": "ok",
        "generated_at": _utc_stamp(),
        "safety_mode": "safe_prepare_no_compute_submission",
        "workflow_dir": str(target_workflow_dir),
        "jobs_total": int(job_count),
        "results_total": int(result_count),
        "stages": stages,
        "next_steps": [
            "Set MP_API_KEY and rerun with --fetch-mp-structures to fetch MP HfO2 polymorph structures.",
            "Review job packages and copy POTCAR only on the licensed compute machine.",
            "Provide Tencent Cloud scheduler details before enabling a real submitter.",
            "Calibrate unit-consistent Landau coefficients with evidence/DFT before a FerroX science run.",
            "After VASP finishes, fill results CSV and rerun with --import-results.",
        ],
    }
    json_path = target_workflow_dir / "computation_workflow_state.json"
    report_path = target_workflow_dir / "computation_workflow_report.md"
    _write_json(json_path, stats)
    report_path.write_text(_workflow_report(stats), encoding="utf-8")
    stats["state_json"] = str(json_path)
    stats["report_path"] = str(report_path)
    record_pipeline_run("47_run_computation_workflow", "ok", stats, db_path=db_path)
    return stats
