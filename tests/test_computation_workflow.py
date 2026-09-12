from __future__ import annotations

from pathlib import Path

import pandas as pd

from backend.db.init_db import init_database
from backend.services.computation_workflow import (
    fetch_mp_structures_for_prepared_jobs,
    run_computation_workflow,
)
from backend.services.computation_validation import prepare_computation_validation_jobs


def _candidates_csv(path: Path) -> None:
    pd.DataFrame(
        [
            {
                "candidate_id": "alc_0001",
                "active_learning_score": 0.92,
                "predicted_value": 48.0,
                "uncertainty": 6.5,
                "evidence_score": 0.8,
                "material_name": "Hf0.5Zr0.5O2",
                "formula": "Hf0.5Zr0.5O2",
                "dopant_elements": "Zr",
                "zr_fraction": 0.5,
                "film_thickness_nm": 7.0,
                "deposition_method": "ALD",
                "annealing_temperature_c": 550,
                "annealing_time_s": 30,
                "annealing_atmosphere": "N2",
                "electrode_stack": "TiN/HZO/TiN",
                "device_type": "FeCAP",
                "phase_name": "orthorhombic",
            }
        ]
    ).to_csv(path, index=False)


def _tasks_csv(path: Path) -> None:
    pd.DataFrame(
        [
            {
                "task_id": "calc_00001",
                "candidate_id": "alc_0001",
                "task_family": "phase_stability",
                "engine": "VASP_DFT",
                "objective": "Compare HfO2 polymorph stability.",
                "priority_score": 0.95,
                "material_name": "Hf0.5Zr0.5O2",
                "formula": "Hf0.5Zr0.5O2",
                "dopant_elements": "Zr",
                "zr_fraction": 0.5,
                "film_thickness_nm": 7.0,
                "deposition_method": "ALD",
                "annealing_temperature_c": 550,
                "annealing_time_s": 30,
                "annealing_atmosphere": "N2",
                "electrode_stack": "TiN/HZO/TiN",
                "device_type": "FeCAP",
                "phase_name": "orthorhombic",
                "structure_model": "bulk_or_strained_supercell",
                "calculation_scope": "composition_phase_energy",
                "required_inputs": '["MP HfO2 polymorph structures"]',
                "expected_outputs": '["relative_energy_meV_per_fu"]',
                "method_quality_gate": '["same VASP settings across phases"]',
                "validation_checks": '["converged static energies"]',
                "claim_boundary": "Smoke test only.",
                "kg_writeback": '["ComputationalTask"]',
                "benchmark_writeback": '["deltaE_o_m_meV_fu"]',
                "cloud_execution_hint": "Manual Tencent Cloud submission after review.",
                "safety_status": "planned_only_no_local_execution",
                "rationale": "Phase stability is the first physics gate.",
            }
        ]
    ).to_csv(path, index=False)


def test_run_computation_workflow_safe_mode_prepares_packages(tmp_path):
    db_path = tmp_path / "workflow.sqlite3"
    init_database(db_path)
    candidates_path = tmp_path / "candidates.csv"
    _candidates_csv(candidates_path)

    stats = run_computation_workflow(
        candidates_path=candidates_path,
        output_dir=tmp_path / "computation",
        jobs_dir=tmp_path / "jobs",
        workflow_dir=tmp_path / "workflow",
        max_candidates=1,
        max_tasks=4,
        max_jobs=1,
        write_cloud_template=False,
        db_path=db_path,
        runtime_status_path=tmp_path / "runtime_status.json",
    )

    assert stats["status"] == "ok"
    assert stats["safety_mode"] == "safe_prepare_no_compute_submission"
    assert stats["stages"]["fetch_mp_structures"]["status"] == "skipped"
    assert Path(stats["report_path"]).exists()


def test_fetch_mp_structures_requires_env_key(tmp_path, monkeypatch):
    monkeypatch.delenv("MP_API_KEY", raising=False)
    db_path = tmp_path / "workflow.sqlite3"
    init_database(db_path)
    tasks_path = tmp_path / "tasks.csv"
    _tasks_csv(tasks_path)
    prepare_computation_validation_jobs(
        tasks_path=tasks_path,
        jobs_dir=tmp_path / "jobs",
        max_jobs=1,
        db_path=db_path,
    )

    stats = fetch_mp_structures_for_prepared_jobs(
        max_jobs=1,
        jobs_dir=tmp_path / "jobs",
        db_path=db_path,
    )

    assert stats["status"] == "blocked_missing_mp_api_key"
    assert stats["attempted"] == 0
