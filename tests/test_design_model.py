from __future__ import annotations

import pandas as pd

from backend.db.init_db import init_database
from backend.services.design_model import train_design_models


def test_train_design_models_trains_small_baseline(tmp_path):
    db_path = tmp_path / "model.sqlite3"
    init_database(db_path)
    dataset_path = tmp_path / "design.csv"
    rows = []
    for index in range(16):
        rows.append(
            {
                "record_id": f"row_{index}",
                "source": "reviewed_facts",
                "paper_id": f"paper_{index // 4}",
                "pdf_id": f"pdf_{index // 4}",
                "chunk_id": f"chunk_{index}",
                "page_number": 1,
                "title": "Synthetic HZO design data",
                "doi": "",
                "year": 2024 + index % 3,
                "paper_type": "experimental",
                "is_review": 0,
                "review_status": "approved",
                "material_name": "Hf0.5Zr0.5O2",
                "material_family": "HZO",
                "formula": "Hf0.5Zr0.5O2",
                "dopant_elements": "Zr",
                "dopant_concentration": "",
                "zr_fraction": 0.5,
                "film_thickness_nm": 8 + index,
                "deposition_method": "ALD",
                "annealing_temperature_c": 450 + index * 5,
                "annealing_time_s": 30,
                "annealing_atmosphere": "N2",
                "top_electrode": "TiN",
                "bottom_electrode": "TiN",
                "electrode_stack": "TiN/HZO/TiN",
                "substrate": "Si",
                "device_type": "capacitor",
                "phase_name": "orthorhombic",
                "space_group": "Pca21",
                "wake_up_or_endurance_state": "after wake-up",
                "target_property": "double_remanent_polarization_2Pr",
                "target_value": 20 + index * 1.5,
                "target_unit": "uC/cm2",
                "condition": "",
                "evidence_text": "Synthetic evidence.",
                "quality_flags": "",
            }
        )
    pd.DataFrame(rows).to_csv(dataset_path, index=False)

    stats = train_design_models(
        dataset_path=dataset_path,
        output_dir=tmp_path / "models",
        targets=["double_remanent_polarization_2Pr"],
        min_rows=8,
        random_state=7,
        db_path=db_path,
    )

    target_stats = stats["targets"][0]
    assert stats["trained_models"] == 1
    assert target_stats["status"] == "trained"
    assert target_stats["rows"] == 16
    assert (tmp_path / "models" / "double_remanent_polarization_2Pr.pkl").exists()
    assert (tmp_path / "models" / "design_model_metrics.json").exists()
