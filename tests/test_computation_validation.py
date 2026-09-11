from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from backend.db.init_db import init_database
from backend.services.computation_validation import (
    import_computation_results,
    prepare_computation_validation_jobs,
)


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
                "required_inputs": json.dumps(["MP HfO2 polymorph structures"]),
                "expected_outputs": json.dumps(["relative_energy_meV_per_fu"]),
                "method_quality_gate": json.dumps(["same VASP settings across phases"]),
                "validation_checks": json.dumps(["converged static energies"]),
                "claim_boundary": "Smoke test only.",
                "kg_writeback": json.dumps(["ComputationalTask"]),
                "benchmark_writeback": json.dumps(["deltaE_o_m_meV_fu"]),
                "cloud_execution_hint": "Manual Tencent Cloud submission after review.",
                "safety_status": "planned_only_no_local_execution",
                "rationale": "Phase stability is the first physics gate.",
            }
        ]
    ).to_csv(path, index=False)


def test_prepare_computation_validation_jobs_writes_safe_package(tmp_path):
    db_path = tmp_path / "calc.sqlite3"
    init_database(db_path)
    tasks_path = tmp_path / "tasks.csv"
    jobs_dir = tmp_path / "jobs"
    _tasks_csv(tasks_path)

    stats = prepare_computation_validation_jobs(
        tasks_path=tasks_path,
        jobs_dir=jobs_dir,
        max_jobs=1,
        db_path=db_path,
    )
    job_index = pd.read_csv(jobs_dir / "computation_jobs.csv")
    work_dir = Path(job_index.iloc[0]["work_dir"])

    assert stats["prepared_jobs"] == 1
    assert (work_dir / "job_manifest.json").exists()
    assert (work_dir / "00_fetch_mp_structures.sh").exists()
    assert not list(work_dir.rglob("POTCAR"))
    assert not list(work_dir.rglob("POSCAR"))


def test_import_computation_results_updates_job_status(tmp_path):
    db_path = tmp_path / "calc.sqlite3"
    init_database(db_path)
    tasks_path = tmp_path / "tasks.csv"
    jobs_dir = tmp_path / "jobs"
    _tasks_csv(tasks_path)
    prepare_computation_validation_jobs(
        tasks_path=tasks_path,
        jobs_dir=jobs_dir,
        max_jobs=1,
        db_path=db_path,
    )
    job_id = pd.read_csv(jobs_dir / "computation_jobs.csv").iloc[0]["job_id"]
    results = tmp_path / "results.csv"
    pd.DataFrame(
        [
            {
                "label": "monoclinic",
                "job_id": job_id,
                "total_energy_eV": -100.0,
                "n_formula_units": 4,
                "relax_converged": "true",
                "static_converged": "true",
                "synthetic_fixture": "true",
            },
            {
                "label": "orthorhombic",
                "job_id": job_id,
                "total_energy_eV": -99.6,
                "n_formula_units": 4,
                "relax_converged": "true",
                "static_converged": "true",
                "synthetic_fixture": "true",
            },
        ]
    ).to_csv(results, index=False)

    stats = import_computation_results(results, db_path=db_path)

    assert stats["rows"] == 2
    assert stats["quality_counts"]["synthetic_fixture"] == 2
    assert Path(stats["normalized_path"]).parent == tmp_path
    normalized = pd.read_csv(stats["normalized_path"])
    assert set(normalized["quality_status"]) == {"synthetic_fixture"}


def test_csv_only_result_cannot_become_verified(tmp_path):
    db_path = tmp_path / "calc.sqlite3"
    init_database(db_path)
    results = tmp_path / "manually_filled.csv"
    pd.DataFrame(
        [
            {
                "label": "orthorhombic",
                "total_energy_eV": -121.0,
                "n_formula_units": 4,
                "relax_converged": "true",
                "static_converged": "true",
            }
        ]
    ).to_csv(results, index=False)

    stats = import_computation_results(results, db_path=db_path)

    assert stats["quality_counts"] == {"user_reported_unverified": 1}
    normalized = pd.read_csv(stats["normalized_path"])
    assert normalized.loc[0, "quality_status"] == "user_reported_unverified"
