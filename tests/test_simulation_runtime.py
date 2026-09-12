from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pandas as pd

from backend.services import simulation_runtime
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


def _fake_jax_status(version: str):
    """Stand-in for simulation_runtime._jax_status.

    The explicit smoke (run_smoke=True) succeeds; a read-only probe
    (run_smoke=False) starts from "not_run". So an "ok" in a read-only result can
    only have arrived via _carry_forward_smoke, which is what these tests are
    actually about.

    This cannot be achieved through the status file alone: _carry_forward_smoke
    returns early unless the *current* probe reports installed=True, and
    _jax_status recomputes that from the live environment on every call. Without
    this stand-in the test silently depends on the FerroX stack (jax + jaxlib)
    being installed, and fails with 'not_run' == 'ok' when it is not.
    """

    def fake(run_smoke: bool) -> dict:
        payload = {
            "installed": True,
            "version": version,
            "jaxlib_version": version,
            "python": sys.executable,
            "smoke_status": "not_run",
        }
        if run_smoke:
            payload.update(
                {
                    "smoke_status": "ok",
                    "returncode": 0,
                    "backend": "cpu",
                    "devices": ["cpu:0"],
                    "gradient": [-1.0, 0.0, 1.0],
                }
            )
        return payload

    return fake


def _fake_ferrox_build(tmp_path: Path) -> Path:
    build = tmp_path / "build"
    binary = build / "bin" / "main3d.TPROF.ex"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return build


def test_read_only_probe_preserves_matching_smoke_results(tmp_path, monkeypatch):
    build = _fake_ferrox_build(tmp_path)
    status_path = tmp_path / "runtime.json"
    monkeypatch.setattr(simulation_runtime, "_jax_status", _fake_jax_status("9.9.9-test"))

    initial = check_simulation_runtime(
        run_jax_smoke=True,
        status_path=status_path,
        ferrox_source=tmp_path / "source",
        ferrox_build=build,
    )
    assert initial["jax"]["smoke_status"] == "ok"
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


def test_read_only_probe_discards_smoke_when_identity_changes(tmp_path, monkeypatch):
    """Guard against the test above passing vacuously.

    Carry-forward is keyed on (version, jaxlib_version, python). If the runtime
    identity changes, a stale smoke result must NOT be presented as current -
    otherwise an old "ok" would vouch for an environment that no longer exists.
    """
    build = _fake_ferrox_build(tmp_path)
    status_path = tmp_path / "runtime.json"
    monkeypatch.setattr(simulation_runtime, "_jax_status", _fake_jax_status("9.9.9-test"))

    initial = check_simulation_runtime(
        run_jax_smoke=True,
        status_path=status_path,
        ferrox_source=tmp_path / "source",
        ferrox_build=build,
    )
    assert initial["jax"]["smoke_status"] == "ok"

    monkeypatch.setattr(simulation_runtime, "_jax_status", _fake_jax_status("8.8.8-other"))
    refreshed = check_simulation_runtime(
        status_path=status_path,
        ferrox_source=tmp_path / "source",
        ferrox_build=build,
    )

    assert refreshed["jax"]["smoke_status"] == "not_run"
    assert "backend" not in refreshed["jax"]
