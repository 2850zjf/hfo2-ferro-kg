from __future__ import annotations

import json
import subprocess

import pytest
from pydantic import ValidationError

from backend.schemas.ferrox_experiment_schema import FerroXExperimentSpec
from backend.services.ferrox_runner import (
    FerroXConfig, build_command, create_grid_convergence_specs, create_replicate_specs,
    create_screening_specs, launch_run, load_curve_csv, prepare_run, render_inputs,
    stop_run, tile_assignments, windows_to_wsl_path,
)


def test_command_is_argument_list_and_paths_are_converted(tmp_path):
    config = FerroXConfig()
    def fake_runner(command, **kwargs):
        assert command[:5] == ["wsl.exe", "-d", "Ubuntu-24.04", "--", "wslpath"]
        return subprocess.CompletedProcess(command, 0, "/mnt/d/run/inputs.hzo\n", "")
    converted = windows_to_wsl_path(tmp_path / "inputs.hzo", config, fake_runner)
    assert build_command(config, converted, 4, 4) == [
        "wsl.exe", "-d", "Ubuntu-24.04", "--", "env", "OMP_NUM_THREADS=4",
        "mpirun", "-n", "4", "/opt/ferrox/bin/ferrox", "/mnt/d/run/inputs.hzo",
    ]


def test_prepare_run_is_isolated_and_preserves_provenance(tmp_path):
    run = prepare_run("trial_001", FerroXExperimentSpec(), tmp_path)
    text = open(run["input_path"], encoding="utf-8").read().lower()
    assert "simulation output is not experimental evidence" in text
    state = json.loads(open(run["state_path"], encoding="utf-8").read())
    assert len(state["parameter_sha256"]) == 64
    assert state["kg_writeback"] is False
    assert (tmp_path / "trial_001" / "experiment.json").is_file()
    with pytest.raises(FileExistsError):
        prepare_run("trial_001", FerroXExperimentSpec(), tmp_path)
    with pytest.raises(ValueError, match="invalid_run_id"):
        prepare_run("../escape", FerroXExperimentSpec(), tmp_path)


def test_input_voltage_waveform_grid_and_schema_guards():
    text = render_inputs(FerroXExperimentSpec())
    assert "domain.n_cell = 32 32 20" in text
    assert "voltage_sweep = 1" in text
    assert "Phi_Bc_hi = -3" in text
    assert "num_Vapp_max = 49" in text
    with pytest.raises(ValidationError, match="quantitative calibration"):
        FerroXExperimentSpec(quantitative_calibration=True)
    with pytest.raises(ValidationError, match="divisible"):
        FerroXExperimentSpec(thickness_nm=10.2)


def test_seeded_microstructure_and_screening_matrix():
    spec = FerroXExperimentSpec.model_validate({
        "preset": "phase_orientation_screen",
        "microstructure": {"tetragonal_fraction": 0.25, "orientation_spread_deg": 30, "random_seed": 7},
    })
    first = tile_assignments(spec)
    assert first == tile_assignments(spec)
    assert sum(bool(tile["tetragonal"]) for tile in first) == 5
    assert len(create_screening_specs(FerroXExperimentSpec())) == 12
    assert [item.microstructure.random_seed for item in create_replicate_specs(spec)] == [1, 2, 3]
    convergence = create_grid_convergence_specs(spec)
    assert [item.grid.cell_size_nm for item in convergence] == [1.0, 0.5]
    assert [item.nx for item in convergence] == [16, 32]


def test_launch_failure_is_recorded(tmp_path, monkeypatch):
    run = prepare_run("failure", FerroXExperimentSpec(), tmp_path)
    monkeypatch.setattr("backend.services.ferrox_runner.windows_to_wsl_path", lambda *a, **k: "/mnt/d/in")
    def fail(*args, **kwargs):
        raise OSError("no process")
    with pytest.raises(OSError):
        launch_run(run, popen=fail)
    assert json.loads(open(run["state_path"], encoding="utf-8").read())["status"] == "failed_to_start"


def test_curve_parser(tmp_path):
    curve = tmp_path / "curve.csv"
    curve.write_text("step,polarization_uc_cm2,electric_field_mv_cm\n1,20,3\n", encoding="utf-8")
    assert load_curve_csv(curve) == [{"step": 1.0, "polarization_uc_cm2": 20.0, "electric_field_mv_cm": 3.0}]


def test_stop_updates_state_without_shell_command(tmp_path):
    run = prepare_run("stop", FerroXExperimentSpec(), tmp_path)
    state = json.loads(open(run["state_path"], encoding="utf-8").read())
    state.update({"status": "running", "pid": 321})
    open(run["state_path"], "w", encoding="utf-8").write(json.dumps(state))
    calls = []
    def fake_runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")
    assert stop_run(run["run_dir"], runner=fake_runner)
    assert calls == [["taskkill.exe", "/PID", "321", "/T"]]
    assert json.loads(open(run["state_path"], encoding="utf-8").read())["status"] == "stopped"
