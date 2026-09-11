from __future__ import annotations

import json
import math
import shutil
import stat
import uuid
from pathlib import Path
from typing import Any

import pandas as pd

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.computation_planner import plan_computational_feedback_tasks
from backend.services.pipeline_log import record_pipeline_run


DEFAULT_TASKS_PATH = PROJECT_ROOT / "data" / "computation" / "computational_feedback_tasks.csv"
DEFAULT_JOBS_DIR = PROJECT_ROOT / "data" / "computation" / "validation_jobs"
MP_STARTER_DIR = PROJECT_ROOT / "computations" / "mp_hfo2_phase_smoke_test"

JOB_EXPORT_COLUMNS = [
    "job_id",
    "task_id",
    "candidate_id",
    "task_family",
    "engine",
    "priority_score",
    "status",
    "execution_mode",
    "safety_status",
    "work_dir",
    "objective",
]


def _json_loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return default


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "nan"):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def _slug(value: str) -> str:
    allowed = []
    for char in value.lower():
        if char.isalnum():
            allowed.append(char)
        elif char in {"-", "_", ".", " "}:
            allowed.append("_")
    text = "".join(allowed).strip("_")
    while "__" in text:
        text = text.replace("__", "_")
    return text or "job"


def _job_id(row: pd.Series) -> str:
    seed = "|".join(
        [
            str(row.get("task_id", "")),
            str(row.get("candidate_id", "")),
            str(row.get("task_family", "")),
            str(row.get("engine", "")),
        ]
    )
    return f"cjob_{uuid.uuid5(uuid.NAMESPACE_URL, seed).hex[:16]}"


def _copy_if_exists(source: Path, target: Path) -> None:
    if source.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP)


def _task_manifest(row: pd.Series, job_id: str, work_dir: Path, execution_mode: str) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "task_id": str(row.get("task_id", "")),
        "candidate_id": str(row.get("candidate_id", "")),
        "task_family": str(row.get("task_family", "")),
        "engine": str(row.get("engine", "")),
        "objective": str(row.get("objective", "")),
        "priority_score": _safe_float(row.get("priority_score")),
        "material": {
            "material_name": str(row.get("material_name", "")),
            "formula": str(row.get("formula", "")),
            "dopant_elements": str(row.get("dopant_elements", "")),
            "zr_fraction": _safe_float(row.get("zr_fraction")),
            "phase_name": str(row.get("phase_name", "")),
        },
        "process_context": {
            "film_thickness_nm": _safe_float(row.get("film_thickness_nm")),
            "deposition_method": str(row.get("deposition_method", "")),
            "annealing_temperature_c": _safe_float(row.get("annealing_temperature_c")),
            "annealing_time_s": _safe_float(row.get("annealing_time_s")),
            "annealing_atmosphere": str(row.get("annealing_atmosphere", "")),
            "electrode_stack": str(row.get("electrode_stack", "")),
            "device_type": str(row.get("device_type", "")),
        },
        "calculation": {
            "structure_model": str(row.get("structure_model", "")),
            "calculation_scope": str(row.get("calculation_scope", "")),
            "required_inputs": _json_loads(row.get("required_inputs"), []),
            "expected_outputs": _json_loads(row.get("expected_outputs"), []),
            "method_quality_gate": _json_loads(row.get("method_quality_gate"), []),
            "validation_checks": _json_loads(row.get("validation_checks"), []),
            "claim_boundary": str(row.get("claim_boundary", "")),
            "kg_writeback": _json_loads(row.get("kg_writeback"), []),
            "benchmark_writeback": _json_loads(row.get("benchmark_writeback"), []),
        },
        "execution": {
            "execution_mode": execution_mode,
            "work_dir": str(work_dir),
            "safety_status": "prepared_no_execution",
            "structure_source_policy": "Use Materials Project, cited publication structures, ICSD-derived structures, or explicitly documented SQS/enumeration. Do not silently hand-build structures.",
            "secret_policy": "No API key, SSH key, POTCAR, paid database file, or cloud token is stored in this job package.",
        },
    }


def _write_phase_stability_package(work_dir: Path, manifest: dict[str, Any]) -> None:
    template_dir = work_dir / "templates"
    template_dir.mkdir(parents=True, exist_ok=True)
    _copy_if_exists(MP_STARTER_DIR / "phase_targets.json", work_dir / "phase_targets.json")
    _copy_if_exists(MP_STARTER_DIR / "results_template.csv", work_dir / "results_template.csv")
    for name in ["INCAR.relax", "INCAR.static", "KPOINTS"]:
        _copy_if_exists(MP_STARTER_DIR / "templates" / name, template_dir / name)

    formula = "HfO2"
    structure_policy = {
        "primary_source": "Materials Project",
        "first_smoke_test_formula": formula,
        "why_hfo2_first": (
            "The first automated validation gate is HfO2 polymorph phase stability. "
            "HZO/dopant/interface models should be added after this smoke test is reproducible."
        ),
        "do_not_commit": ["POTCAR", "MP_API_KEY", "SSH keys", "cloud tokens"],
    }
    (work_dir / "structure_source_policy.json").write_text(
        json.dumps(structure_policy, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_executable(
        work_dir / "00_fetch_mp_structures.sh",
        f"""#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${{MP_API_KEY:-}}" ]]; then
  echo "MP_API_KEY is not set. Export it in the shell; do not write it into files."
  exit 2
fi

python "{MP_STARTER_DIR / 'fetch_mp_structures.py'}" \\
  --targets "{work_dir / 'phase_targets.json'}" \\
  --output-dir "{work_dir / 'mp_structures'}" \\
  --formula "{formula}" \\
  --max-energy-above-hull 0.35
""",
    )
    _write_executable(
        work_dir / "01_run_vasp_placeholder.sh",
        """#!/usr/bin/env bash
set -euo pipefail

echo "This placeholder intentionally does not run VASP."
echo "Copy POTCAR on the licensed compute machine, then submit each mp_structures/<phase>/ folder with your scheduler."
echo "After completion, fill results_template.csv and import it with pipelines/45_import_computation_results.py."
""",
    )
    manifest["execution"]["prepared_files"] = [
        "phase_targets.json",
        "results_template.csv",
        "templates/INCAR.relax",
        "templates/INCAR.static",
        "templates/KPOINTS",
        "00_fetch_mp_structures.sh",
        "01_run_vasp_placeholder.sh",
    ]


def _write_protocol_package(work_dir: Path, manifest: dict[str, Any]) -> None:
    required = manifest["calculation"].get("required_inputs", [])
    expected = manifest["calculation"].get("expected_outputs", [])
    gates = manifest["calculation"].get("method_quality_gate", [])
    lines = [
        f"# {manifest['task_family']} computation protocol",
        "",
        "This package is a protocol and status artifact. It does not run calculations locally.",
        "",
        "## Objective",
        "",
        manifest.get("objective", ""),
        "",
        "## Required Inputs",
        "",
        *[f"- {item}" for item in required],
        "",
        "## Expected Outputs",
        "",
        *[f"- {item}" for item in expected],
        "",
        "## Quality Gate",
        "",
        *[f"- {item}" for item in gates],
        "",
        "## Claim Boundary",
        "",
        manifest["calculation"].get("claim_boundary", ""),
    ]
    (work_dir / "protocol.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (work_dir / "results_template.csv").write_text(
        "metric,value,unit,quality_status,notes\n",
        encoding="utf-8",
    )
    _write_executable(
        work_dir / "01_run_placeholder.sh",
        """#!/usr/bin/env bash
set -euo pipefail
echo "Protocol-only task. Prepare validated input structures and scheduler scripts before running any compute job."
""",
    )
    manifest["execution"]["prepared_files"] = [
        "protocol.md",
        "results_template.csv",
        "01_run_placeholder.sh",
    ]


def _write_readme(work_dir: Path, manifest: dict[str, Any]) -> None:
    lines = [
        f"# Computation Validation Job {manifest['job_id']}",
        "",
        f"- task: {manifest['task_id']}",
        f"- family: {manifest['task_family']}",
        f"- engine: {manifest['engine']}",
        f"- material: {manifest['material'].get('material_name')}",
        f"- formula: {manifest['material'].get('formula')}",
        f"- priority: {manifest.get('priority_score')}",
        "",
        "## Safety",
        "",
        "- No calculation has been launched by this package.",
        "- No POTCAR, API key, SSH key, or cloud token is included.",
        "- Generated structures and results should remain under `data/computation/` unless explicitly curated.",
        "",
        "## Next Step",
        "",
        "Review `job_manifest.json`, fetch or attach source structures, run on the licensed compute system, then import results.",
    ]
    (work_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_job_package(row: pd.Series, jobs_dir: Path, execution_mode: str) -> tuple[str, Path, dict[str, Any]]:
    job_id = _job_id(row)
    folder_name = f"{job_id}__{_slug(str(row.get('task_family', 'task')))}"
    work_dir = jobs_dir / folder_name
    work_dir.mkdir(parents=True, exist_ok=True)
    manifest = _task_manifest(row, job_id, work_dir, execution_mode)
    if str(row.get("task_family", "")) == "phase_stability":
        _write_phase_stability_package(work_dir, manifest)
    else:
        _write_protocol_package(work_dir, manifest)
    (work_dir / "job_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_readme(work_dir, manifest)
    return job_id, work_dir, manifest


def _load_tasks(tasks_path: Path | None) -> tuple[pd.DataFrame, Path]:
    path = tasks_path or DEFAULT_TASKS_PATH
    if not path.exists():
        plan_computational_feedback_tasks(output_dir=path.parent)
    if not path.exists():
        return pd.DataFrame(), path
    return pd.read_csv(path), path


def prepare_computation_validation_jobs(
    tasks_path: Path | None = None,
    jobs_dir: Path | None = None,
    max_jobs: int = 12,
    task_family: str | None = None,
    execution_mode: str = "prepare_only",
    force: bool = False,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Create safe computation job packages and register them in SQLite.

    This function never launches VASP, MD, phase-field, SSH, or cloud jobs.
    """

    tasks, source_path = _load_tasks(tasks_path)
    target_dir = jobs_dir or DEFAULT_JOBS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    export_csv = target_dir / "computation_jobs.csv"
    export_json = target_dir / "computation_jobs.json"

    if tasks.empty:
        pd.DataFrame(columns=JOB_EXPORT_COLUMNS).to_csv(export_csv, index=False, encoding="utf-8-sig")
        export_json.write_text("[]\n", encoding="utf-8")
        stats = {
            "status": "no_tasks",
            "tasks_path": str(source_path),
            "jobs": 0,
            "jobs_dir": str(target_dir),
            "safety_note": "No computation was launched.",
        }
        record_pipeline_run("44_prepare_computation_validation_jobs", "skipped", stats, db_path=db_path)
        return stats

    if task_family:
        tasks = tasks[tasks["task_family"].astype(str) == task_family].copy()
    tasks = tasks.sort_values("priority_score", ascending=False).head(max_jobs)

    prepared: list[dict[str, Any]] = []
    skipped = 0
    with connect(db_path) as conn:
        for _, row in tasks.iterrows():
            job_id = _job_id(row)
            existing = conn.execute(
                "SELECT job_id FROM computation_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if existing and not force:
                skipped += 1
                continue
            job_id, work_dir, manifest = _write_job_package(row, target_dir, execution_mode)
            cloud_payload = {
                "provider": "tencent_cloud_or_manual_hpc",
                "mode": "not_submitted",
                "entrypoint": str(work_dir / "README.md"),
                "requires_user_confirmation": True,
            }
            conn.execute(
                """
                INSERT INTO computation_jobs (
                    job_id, task_id, candidate_id, task_family, engine, objective,
                    priority_score, status, execution_mode, safety_status, work_dir,
                    input_manifest_json, cloud_payload_json, result_json, error_message,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(job_id) DO UPDATE SET
                    status = excluded.status,
                    execution_mode = excluded.execution_mode,
                    safety_status = excluded.safety_status,
                    work_dir = excluded.work_dir,
                    input_manifest_json = excluded.input_manifest_json,
                    cloud_payload_json = excluded.cloud_payload_json,
                    error_message = excluded.error_message,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    job_id,
                    manifest["task_id"],
                    manifest["candidate_id"],
                    manifest["task_family"],
                    manifest["engine"],
                    manifest["objective"],
                    manifest["priority_score"],
                    "prepared",
                    execution_mode,
                    "prepared_no_execution",
                    str(work_dir),
                    json.dumps(manifest, ensure_ascii=False),
                    json.dumps(cloud_payload, ensure_ascii=False),
                    None,
                    None,
                ),
            )
            prepared.append(
                {
                    "job_id": job_id,
                    "task_id": manifest["task_id"],
                    "candidate_id": manifest["candidate_id"],
                    "task_family": manifest["task_family"],
                    "engine": manifest["engine"],
                    "priority_score": manifest["priority_score"],
                    "status": "prepared",
                    "execution_mode": execution_mode,
                    "safety_status": "prepared_no_execution",
                    "work_dir": str(work_dir),
                    "objective": manifest["objective"],
                }
            )
        conn.commit()

    jobs = list_computation_jobs(limit=10000, db_path=db_path)
    pd.DataFrame(jobs, columns=JOB_EXPORT_COLUMNS).to_csv(export_csv, index=False, encoding="utf-8-sig")
    export_json.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path = target_dir / "computation_validation_jobs.md"
    _write_jobs_report(report_path, jobs)
    stats = {
        "status": "ok",
        "tasks_path": str(source_path),
        "prepared_jobs": len(prepared),
        "skipped_existing": skipped,
        "jobs_total": len(jobs),
        "jobs_dir": str(target_dir),
        "jobs_csv": str(export_csv),
        "jobs_json": str(export_json),
        "report_path": str(report_path),
        "safety_note": "Job packages were prepared only; no local or cloud calculation was launched.",
    }
    record_pipeline_run("44_prepare_computation_validation_jobs", "ok", stats, db_path=db_path)
    return stats


def _write_jobs_report(path: Path, jobs: list[dict[str, Any]]) -> None:
    counts = pd.Series([row.get("task_family", "") for row in jobs]).value_counts().to_dict() if jobs else {}
    lines = [
        "# Computation Validation Jobs",
        "",
        "These jobs are prepared packages. They do not include POTCAR or secrets and do not launch calculations.",
        "",
        "## Counts",
        "",
    ]
    if counts:
        lines.extend([f"- {family}: {count}" for family, count in counts.items()])
    else:
        lines.append("- No jobs prepared.")
    lines.extend(["", "## Jobs", ""])
    for row in jobs[:30]:
        lines.append(
            f"- {row.get('job_id')} | {row.get('task_family')} | {row.get('status')} | {row.get('work_dir')}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def list_computation_jobs(limit: int = 100, db_path: Path | None = None) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT job_id, task_id, candidate_id, task_family, engine, priority_score,
                   status, execution_mode, safety_status, work_dir, objective
            FROM computation_jobs
            ORDER BY created_at DESC, priority_score DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def computation_job_summary(db_path: Path | None = None) -> dict[str, Any]:
    with connect(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM computation_jobs").fetchone()[0]
        by_status = {
            row["status"]: row["count"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS count FROM computation_jobs GROUP BY status"
            ).fetchall()
        }
        by_family = {
            row["task_family"]: row["count"]
            for row in conn.execute(
                "SELECT task_family, COUNT(*) AS count FROM computation_jobs GROUP BY task_family"
            ).fetchall()
        }
        results = conn.execute("SELECT COUNT(*) FROM computation_results").fetchone()[0]
    return {
        "jobs": int(total),
        "results": int(results),
        "by_status": {str(k): int(v) for k, v in by_status.items()},
        "by_family": {str(k): int(v) for k, v in by_family.items()},
    }


def _truthy(value: Any) -> bool:
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "ok", "converged"}


def _quality_status(row: pd.Series) -> str:
    """Classify CSV-only results without pretending they are raw-output verified.

    This importer accepts a user-filled table.  A table can describe convergence,
    but it cannot prove it: the same fields can be typed by hand or produced by a
    test fixture.  Raw-output verification therefore belongs to the independent
    VASP audit path, never to this function.
    """

    if _truthy(row.get("synthetic_fixture")):
        return "synthetic_fixture"
    energy = _safe_float(row.get("energy_eV_per_fu"))
    if energy is None:
        return "needs_review"
    if "relax_converged" in row and not _truthy(row.get("relax_converged")):
        return "needs_review"
    if "static_converged" in row and not _truthy(row.get("static_converged")):
        return "needs_review"
    return "user_reported_unverified"


def _normalize_phase_results(results: pd.DataFrame) -> pd.DataFrame:
    data = results.copy()
    if "energy_eV_per_fu" not in data or data["energy_eV_per_fu"].isna().all():
        if {"total_energy_eV", "n_formula_units"} <= set(data.columns):
            total = pd.to_numeric(data["total_energy_eV"], errors="coerce")
            n_fu = pd.to_numeric(data["n_formula_units"], errors="coerce")
            data["energy_eV_per_fu"] = total / n_fu
    data["energy_eV_per_fu"] = pd.to_numeric(data.get("energy_eV_per_fu"), errors="coerce")
    if "relative_energy_meV_per_fu" not in data or data["relative_energy_meV_per_fu"].isna().all():
        min_energy = data["energy_eV_per_fu"].min()
        data["relative_energy_meV_per_fu"] = (data["energy_eV_per_fu"] - min_energy) * 1000.0
    data["quality_status"] = [_quality_status(row) for _, row in data.iterrows()]
    return data


def import_computation_results(
    results_path: Path,
    job_id: str | None = None,
    db_path: Path | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Import unverified descriptors from a user-filled CSV.

    The function deliberately never emits ``quality_status=verified``.  Scientific
    verification requires parsing immutable raw VASP outputs and their hashes via
    the dedicated audit workflow.
    """

    results = pd.read_csv(results_path)
    if results.empty:
        stats = {"status": "empty", "results_path": str(results_path), "rows": 0}
        record_pipeline_run("45_import_computation_results", "skipped", stats, db_path=db_path)
        return stats

    if job_id is None and "job_id" in results.columns:
        ids = [str(value) for value in results["job_id"].dropna().unique()]
        if len(ids) == 1:
            job_id = ids[0]
    if job_id is None:
        job_id = "manual_import"

    normalized = _normalize_phase_results(results)
    # Keep diagnostics next to the caller's input by default.  This prevents tests
    # and one-off imports from polluting the repository-wide validation directory.
    report_dir = output_dir or results_path.parent
    report_dir.mkdir(parents=True, exist_ok=True)
    normalized_path = report_dir / f"{job_id}_normalized_results.csv"
    normalized.to_csv(normalized_path, index=False, encoding="utf-8-sig")

    inserted = 0
    quality_counts = normalized["quality_status"].value_counts().to_dict()
    with connect(db_path) as conn:
        job = conn.execute("SELECT task_id FROM computation_jobs WHERE job_id = ?", (job_id,)).fetchone()
        task_id = job["task_id"] if job else None
        for index, row in normalized.iterrows():
            payload = row.to_dict()
            result_id = f"cresult_{uuid.uuid5(uuid.NAMESPACE_URL, job_id + str(index) + json.dumps(payload, sort_keys=True, default=str)).hex[:16]}"
            conn.execute(
                """
                INSERT INTO computation_results (
                    result_id, job_id, task_id, result_type, payload_json,
                    quality_status, source_path
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(result_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    quality_status = excluded.quality_status,
                    source_path = excluded.source_path
                """,
                (
                    result_id,
                    job_id,
                    task_id,
                    "phase_energy" if "energy_eV_per_fu" in normalized.columns else "descriptor",
                    json.dumps(payload, ensure_ascii=False, default=str),
                    str(row["quality_status"]),
                    str(results_path),
                ),
            )
            inserted += 1
        # CSV-only imports are never allowed to mark a computation job verified or
        # complete, even when the user-provided convergence booleans are true.
        status = "needs_review"
        conn.execute(
            """
            UPDATE computation_jobs
            SET status = ?, result_json = ?, completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE job_id = ?
            """,
            (
                status,
                json.dumps(
                    {
                        "source_path": str(results_path),
                        "normalized_path": str(normalized_path),
                        "quality_counts": {str(k): int(v) for k, v in quality_counts.items()},
                    },
                    ensure_ascii=False,
                ),
                job_id,
            ),
        )
        conn.commit()

    report_path = report_dir / f"{job_id}_import_report.md"
    lines = [
        f"# Computation Result Import: {job_id}",
        "",
        f"- source: {results_path}",
        f"- normalized: {normalized_path}",
        f"- rows: {inserted}",
        "- verification: CSV-only; no row is accepted as raw-output verified",
        "",
        "## Quality Counts",
        "",
    ]
    lines.extend([f"- {key}: {value}" for key, value in quality_counts.items()])
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    stats = {
        "status": "ok",
        "job_id": job_id,
        "rows": inserted,
        "quality_counts": {str(k): int(v) for k, v in quality_counts.items()},
        "normalized_path": str(normalized_path),
        "report_path": str(report_path),
    }
    record_pipeline_run("45_import_computation_results", "ok", stats, db_path=db_path)
    return stats
