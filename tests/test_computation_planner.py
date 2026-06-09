from __future__ import annotations

import pandas as pd

from backend.db.init_db import init_database
from backend.services.computation_planner import plan_computational_feedback_tasks


def test_plan_computational_feedback_tasks_is_planning_only(tmp_path):
    db_path = tmp_path / "calc.sqlite3"
    init_database(db_path)
    candidates_path = tmp_path / "candidates.csv"
    output_dir = tmp_path / "computation"
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
    ).to_csv(candidates_path, index=False)

    stats = plan_computational_feedback_tasks(
        candidates_path=candidates_path,
        output_dir=output_dir,
        max_candidates=1,
        db_path=db_path,
    )
    tasks = pd.read_csv(output_dir / "computational_feedback_tasks.csv")

    assert stats["tasks"] >= 3
    assert "phase_stability" in set(tasks["task_family"])
    assert "planned_only_no_local_execution" in set(tasks["safety_status"])
    assert (output_dir / "computational_feedback_plan.md").exists()
