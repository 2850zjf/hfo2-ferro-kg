from __future__ import annotations

import json
import os
import platform
import shutil
import stat
import subprocess
import sys
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from backend.core.config import PROJECT_ROOT


SIMULATION_DIR = PROJECT_ROOT / "computations" / "simulation"
LOCK_PATH = SIMULATION_DIR / "ferrox.lock.json"
FERROX_TEMPLATE = SIMULATION_DIR / "templates" / "inputs_hzo_mfim"
JAX_STARTER = SIMULATION_DIR / "jax_landau_smoke.py"
DEFAULT_RUNTIME_DIR = PROJECT_ROOT / "data" / "computation" / "simulation_runtime"
DEFAULT_STATUS_PATH = DEFAULT_RUNTIME_DIR / "runtime_status.json"
DEFAULT_FERROX_SOURCE = PROJECT_ROOT / "data" / "computation" / "tools" / "FerroX"
DEFAULT_FERROX_BUILD = DEFAULT_FERROX_SOURCE / "build-macos-cpu"
DEFAULT_FERROX_EXAMPLE = DEFAULT_FERROX_SOURCE / "Exec" / "Examples" / "inputs_mfim_Noeb"


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _carry_forward_smoke(
    current: dict[str, Any],
    previous: dict[str, Any],
    *,
    identity_keys: tuple[str, ...],
) -> None:
    """Keep the last explicit smoke result during read-only environment probes."""
    if not current.get("installed") or previous.get("smoke_status") in {None, "not_run"}:
        return
    if any(current.get(key) != previous.get(key) for key in identity_keys):
        return
    for key in (
        "smoke_status",
        "last_smoke_at",
        "returncode",
        "backend",
        "devices",
        "gradient",
        "error",
        "smoke",
    ):
        if key in previous:
            current[key] = previous[key]


def _run_text(command: list[str], *, cwd: Path | None = None, timeout: int = 15) -> tuple[int, str]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        return result.returncode, result.stdout or ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 124, str(exc)


def _git_revision(source_dir: Path) -> str | None:
    if not (source_dir / ".git").exists():
        return None
    code, output = _run_text(["git", "-C", str(source_dir), "rev-parse", "HEAD"], timeout=5)
    return output.strip() if code == 0 and output.strip() else None


def find_ferrox_executable(
    source_dir: Path = DEFAULT_FERROX_SOURCE,
    build_dir: Path = DEFAULT_FERROX_BUILD,
) -> Path | None:
    configured = os.getenv("HFO2_FERROX_BIN")
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.append(build_dir / "bin" / "main3d.TPROF.ex")
    if source_dir.exists():
        candidates.extend(sorted(source_dir.glob("build*/bin/main3d*.ex")))
        candidates.extend(sorted((source_dir / "Exec").glob("main3d*.ex")))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    return None


def _jax_status(run_smoke: bool) -> dict[str, Any]:
    jax_version = _package_version("jax")
    jaxlib_version = _package_version("jaxlib")
    payload: dict[str, Any] = {
        "installed": bool(jax_version and jaxlib_version),
        "version": jax_version,
        "jaxlib_version": jaxlib_version,
        "python": sys.executable,
        "smoke_status": "not_run",
    }
    if not run_smoke or not payload["installed"]:
        return payload

    code = """
import json
import jax
import jax.numpy as jnp
jax.config.update('jax_enable_x64', True)
def energy(p):
    return -p**2 + p**4 + 0.25*p**6
grad = jax.jit(jax.vmap(jax.grad(energy)))
p = jnp.array([-0.5, 0.0, 0.5])
g = grad(p)
print(json.dumps({
    'backend': jax.default_backend(),
    'devices': [str(device) for device in jax.devices()],
    'gradient': [float(value) for value in g],
}))
"""
    returncode, output = _run_text([sys.executable, "-c", code], timeout=30)
    payload["returncode"] = returncode
    if returncode == 0:
        try:
            smoke = json.loads(output.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            payload.update({"smoke_status": "failed", "error": output[-1000:]})
        else:
            payload.update(smoke)
            payload["smoke_status"] = "ok"
    else:
        payload.update({"smoke_status": "failed", "error": output[-1000:]})
    return payload


def _ferrox_status(
    source_dir: Path,
    build_dir: Path,
) -> dict[str, Any]:
    lock = _read_json(LOCK_PATH, {})
    executable = find_ferrox_executable(source_dir, build_dir)
    revision = _git_revision(source_dir)
    pinned = str(lock.get("commit") or "") or None
    return {
        "installed": executable is not None,
        "source_dir": str(source_dir),
        "build_dir": str(build_dir),
        "executable": str(executable) if executable else None,
        "source_commit": revision,
        "pinned_commit": pinned,
        "commit_matches_lock": bool(revision and pinned and revision == pinned),
        "ferrox_version": lock.get("ferrox_version"),
        "amrex_commit": lock.get("amrex_commit"),
        "build_configuration": lock.get("local_build", {}),
        "smoke_status": "not_run",
    }


def run_ferrox_smoke_test(
    *,
    output_dir: Path | None = None,
    executable: Path | None = None,
    input_path: Path | None = None,
    timeout_s: int = 60,
) -> dict[str, Any]:
    binary = executable or find_ferrox_executable()
    inputs = input_path or (DEFAULT_FERROX_EXAMPLE if DEFAULT_FERROX_EXAMPLE.exists() else FERROX_TEMPLATE)
    if not binary or not binary.exists():
        return {"status": "blocked_missing_ferrox_binary", "returncode": None}
    if not inputs.exists():
        return {"status": "blocked_missing_input", "returncode": None, "input": str(inputs)}

    target = output_dir or (
        DEFAULT_RUNTIME_DIR / "ferrox_smoke" / datetime.now(UTC).strftime("run_%Y%m%dT%H%M%S%fZ")
    )
    target.mkdir(parents=True, exist_ok=True)
    log_path = target / "ferrox_smoke.log"
    command = [
        str(binary),
        str(inputs),
        "nsteps=1",
        "plot_int=1",
        "domain.n_cell=8 8 8",
        "domain.max_grid_size=8 8 8",
    ]
    returncode, output = _run_text(command, cwd=target, timeout=timeout_s)
    log_path.write_text(output, encoding="utf-8")
    plotfiles = sorted(path.name for path in target.glob("plt*") if path.is_dir())
    finalized = "AMReX" in output and "finalized" in output
    status = "ok" if returncode == 0 and len(plotfiles) >= 2 and finalized else "failed"
    return {
        "status": status,
        "returncode": returncode,
        "binary": str(binary),
        "input": str(inputs),
        "output_dir": str(target),
        "log_path": str(log_path),
        "mesh": [8, 8, 8],
        "steps": 1,
        "plotfiles": plotfiles,
        "amrex_finalized": finalized,
        "claim_boundary": "Runtime smoke test only; no calibrated HZO result is claimed.",
    }


def check_simulation_runtime(
    *,
    run_jax_smoke: bool = False,
    run_ferrox_smoke: bool = False,
    status_path: Path = DEFAULT_STATUS_PATH,
    ferrox_source: Path = DEFAULT_FERROX_SOURCE,
    ferrox_build: Path = DEFAULT_FERROX_BUILD,
    ferrox_smoke_output: Path | None = None,
) -> dict[str, Any]:
    previous = _read_json(status_path, {})
    if not isinstance(previous, dict):
        previous = {}
    checked_at = _utc_stamp()
    ferrox = _ferrox_status(ferrox_source, ferrox_build)
    jax = _jax_status(run_jax_smoke)
    if run_jax_smoke and jax.get("smoke_status") != "not_run":
        jax["last_smoke_at"] = checked_at
    elif not run_jax_smoke:
        _carry_forward_smoke(
            jax,
            previous.get("jax", {}),
            identity_keys=("version", "jaxlib_version", "python"),
        )
    if run_ferrox_smoke:
        ferrox_smoke = run_ferrox_smoke_test(
            output_dir=ferrox_smoke_output,
            executable=Path(ferrox["executable"]) if ferrox.get("executable") else None,
            input_path=(
                ferrox_source / "Exec" / "Examples" / "inputs_mfim_Noeb"
                if (ferrox_source / "Exec" / "Examples" / "inputs_mfim_Noeb").exists()
                else FERROX_TEMPLATE
            ),
        )
        ferrox["smoke_status"] = ferrox_smoke["status"]
        ferrox["smoke"] = ferrox_smoke
        ferrox["last_smoke_at"] = checked_at
    else:
        _carry_forward_smoke(
            ferrox,
            previous.get("ferrox", {}),
            identity_keys=("source_commit", "executable"),
        )

    payload = {
        "status": "ready" if ferrox.get("installed") and _package_version("jax") else "partial",
        "generated_at": checked_at,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "jax": jax,
        "ferrox": ferrox,
        "workflow": {
            "sequence": [
                "evidence_and_DFT_descriptors",
                "jax_landau_calibration",
                "ferrox_phase_field_simulation",
                "quality_gate",
                "KG_and_benchmark_writeback",
            ],
            "local_policy": "CPU smoke tests only",
            "cloud_policy": "Large MPI/GPU runs require explicit user-confirmed Tencent Cloud submission",
        },
        "safety": {
            "cloud_job_submitted": False,
            "paid_compute_started": False,
            "secrets_written": False,
            "scientific_result_claimed": False,
        },
    }
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    payload["status_path"] = str(status_path)
    return payload


def load_simulation_runtime_status(status_path: Path = DEFAULT_STATUS_PATH) -> dict[str, Any]:
    payload = _read_json(status_path, None)
    if not isinstance(payload, dict):
        return check_simulation_runtime(status_path=status_path)
    executable = find_ferrox_executable()
    ferrox = payload.setdefault("ferrox", {})
    ferrox["installed"] = executable is not None
    ferrox["executable"] = str(executable) if executable else None
    jax = payload.setdefault("jax", {})
    jax["installed"] = bool(_package_version("jax") and _package_version("jaxlib"))
    jax["version"] = _package_version("jax")
    jax["jaxlib_version"] = _package_version("jaxlib")
    payload["status_path"] = str(status_path)
    return payload


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)


def prepare_ferrox_job_package(work_dir: Path, manifest: dict[str, Any]) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    input_target = work_dir / "inputs_hzo_mfim"
    shutil.copyfile(FERROX_TEMPLATE, input_target)
    shutil.copyfile(JAX_STARTER, work_dir / "jax_landau_smoke.py")

    runtime = load_simulation_runtime_status()
    default_binary = runtime.get("ferrox", {}).get("executable") or ""
    _write_executable(
        work_dir / "01_run_jax_landau_smoke.sh",
        """#!/usr/bin/env bash
set -euo pipefail
PYTHON="${HFO2_SIM_PYTHON:-python3}"
"${PYTHON}" jax_landau_smoke.py --output jax_landau_smoke.json
""",
    )
    _write_executable(
        work_dir / "02_run_ferrox_local_smoke.sh",
        f"""#!/usr/bin/env bash
set -euo pipefail
FERROX_BIN="${{HFO2_FERROX_BIN:-{default_binary}}}"
if [[ -z "${{FERROX_BIN}}" || ! -x "${{FERROX_BIN}}" ]]; then
  echo "Set HFO2_FERROX_BIN to a validated FerroX executable." >&2
  exit 2
fi
"${{FERROX_BIN}}" inputs_hzo_mfim \
  nsteps=1 plot_int=1 \
  domain.n_cell="8 8 8" domain.max_grid_size="8 8 8"
""",
    )
    _write_executable(
        work_dir / "03_run_ferrox_cloud.template.sh",
        """#!/usr/bin/env bash
set -euo pipefail
: "${HFO2_FERROX_BIN:?Set HFO2_FERROX_BIN on the cloud compute image}"
NPROCS="${HFO2_FERROX_NPROCS:-1}"
if [[ "${NPROCS}" -gt 1 ]]; then
  mpirun -n "${NPROCS}" "${HFO2_FERROX_BIN}" inputs_hzo_mfim
else
  "${HFO2_FERROX_BIN}" inputs_hzo_mfim
fi
""",
    )
    (work_dir / "results_template.csv").write_text(
        "metric,value,unit,quality_status,source_file,notes\n"
        "remanent_polarization_trend,,relative,pending,,\n"
        "coercive_field_trend,,relative,pending,,\n"
        "domain_fraction_proxy,,fraction,pending,,\n"
        "switching_time_proxy,,s,pending,,\n",
        encoding="utf-8",
    )
    requirements = {
        "runtime": runtime,
        "input_provenance": "Official FerroX MFIM example adapted as an uncalibrated starter.",
        "required_before_science": [
            "cited and unit-consistent Landau coefficients",
            "mesh and time-step convergence",
            "electrical and polarization boundary-condition sensitivity",
            "mapping from FerroX output to experimental observable",
        ],
    }
    (work_dir / "simulation_runtime_requirements.json").write_text(
        json.dumps(requirements, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest["engine"] = "FerroX_AMReX"
    manifest["execution"]["runtime"] = {
        "jax": runtime.get("jax", {}),
        "ferrox": runtime.get("ferrox", {}),
    }
    manifest["execution"]["prepared_files"] = [
        "inputs_hzo_mfim",
        "jax_landau_smoke.py",
        "01_run_jax_landau_smoke.sh",
        "02_run_ferrox_local_smoke.sh",
        "03_run_ferrox_cloud.template.sh",
        "results_template.csv",
        "simulation_runtime_requirements.json",
    ]
