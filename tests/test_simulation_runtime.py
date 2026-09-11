from __future__ import annotations

import json
import stat
from pathlib import Path

import pandas as pd

from backend.services.simulation_runtime import (
    check_simulation_runtime,
    prepare_ferrox_job_package,
    run_ferrox_smoke_test,
)


def test_runtime_check_writes_status_without_launching_compute(tmp_path):
    status_path = tmp_path / "runtime.json"
    payload = check_simulation_runtime(
        status_path=status_path,
        ferrox_source=tmp_path / "missing-source",
        ferrox_build=tmp_path / "missing-build",
    )

    assert status_path.exists()
    assert payload["safety"]["cloud_job_submitted"] is False
    assert payload["safety"]["paid_compute_started"] is False
    assert payload["ferrox"]["installed"] is False


def test_fake_ferrox_smoke_requires_plotfiles_and_finalize_marker(tmp_path):
    binary = tmp_path / "fake_ferrox"
    binary.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "mkdir -p plt00000000 plt00000001\n"
        "echo 'AMReX initialized'\n"
        "echo 'Advanced step 1'\n"
        "echo 'AMReX finalized'\n",
        encoding="utf-8",
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    inputs = tmp_path / "inputs"
    inputs.write_text("nsteps = 1\n", encoding="utf-8")

    result = run_ferrox_smoke_test(
        output_dir=tmp_path / "run",
        executable=binary,
        input_path=inputs,
    )

    assert result["status"] == "ok"
    assert result["steps"] == 1
    assert result["plotfiles"] == ["plt00000000", "plt00000001"]


def test_phase_field_package_is_prepared_without_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("HFO2_FERROX_BIN", str(tmp_path / "not-executable"))
    manifest = {
        "engine": "phase_field_or_compact_switching_model",
        "execution": {},
    }

    prepare_ferrox_job_package(tmp_path / "job", manifest)

    job = tmp_path / "job"
    assert (job / "inputs_hzo_mfim").exists()
    assert (job / "jax_landau_smoke.py").exists()
    assert (job / "03_run_ferrox_cloud.template.sh").exists()
    requirements = json.loads((job / "simulation_runtime_requirements.json").read_text())
    assert "required_before_science" in requirements
    assert manifest["engine"] == "FerroX_AMReX"
    assert not list(job.glob("plt*"))
    result_template = pd.read_csv(job / "results_template.csv")
    assert list(result_template.columns) == [
        "metric",
        "value",
        "unit",
        "quality_status",
        "source_file",
        "notes",
    ]


def test_read_only_probe_preserves_matching_smoke_results(tmp_path):
    build = tmp_path / "build"
    binary = build / "bin" / "main3d.TPROF.ex"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    status_path = tmp_path / "runtime.json"

    initial = check_simulation_runtime(
        run_jax_smoke=True,
        status_path=status_path,
        ferrox_source=tmp_path / "source",
        ferrox_build=build,
    )
    initial["ferrox"]["smoke_status"] = "ok"
    initial["ferrox"]["smoke"] = {"status": "ok", "steps": 1}
    status_path.write_text(json.dumps(initial), encoding="utf-8")

    refreshed = check_simulation_runtime(
        status_path=status_path,
        ferrox_source=tmp_path / "source",
        ferrox_build=build,
    )

    assert refreshed["jax"]["smoke_status"] == "ok"
    assert refreshed["jax"]["backend"] == initial["jax"]["backend"]
    assert refreshed["ferrox"]["smoke_status"] == "ok"
    assert refreshed["ferrox"]["smoke"]["steps"] == 1
