from __future__ import annotations

import pandas as pd

from backend.db.init_db import init_database
from backend.services.model_comparison import (
    compare_regression_models,
    cross_validate_regression_models,
    filter_strong_relevance_rows,
)


def _row(index: int, *, target: str, tier: str = "strong_only", source: str = "sample_property_links"):
    return {
        "record_id": f"row_{index}",
        "source": source,
        "paper_id": f"paper_{index // 3}",
        "pdf_id": f"pdf_{index // 3}",
        "chunk_id": f"chunk_{index}",
        "page_number": 1,
        "title": "Synthetic HZO data",
        "doi": "",
        "year": 2024 + index % 2,
        "paper_type": "experimental",
        "is_review": 0,
        "review_status": "strong",
        "ai_review_status": "usable_for_model",
        "usable_for_model": 1,
        "benchmark_tier": tier,
        "material_name": "Hf0.5Zr0.5O2",
        "material_family": "HZO",
        "formula": "Hf0.5Zr0.5O2",
        "dopant_elements": "Zr",
        "dopant_concentration": "",
        "zr_fraction": 0.5,
        "film_thickness_nm": 8 + index,
        "deposition_method": "ALD",
        "annealing_temperature_c": 450 + index * 3,
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
        "target_property": target,
        "target_value": 20 + index,
        "target_unit": "uC/cm2",
        "model_target_value": 20 + index,
        "model_target_unit": "uC/cm2",
        "model_include": 1,
        "model_exclusion_reason": "",
        "condition": "",
        "evidence_text": "Synthetic evidence.",
        "risk_flags": "",
        "quality_flags": "",
    }


def test_filter_and_compare_strong_relevant_rows(tmp_path):
    db_path = tmp_path / "comparison.sqlite3"
    init_database(db_path)
    dataset_path = tmp_path / "design.csv"
    filtered_path = tmp_path / "strong.csv"
    rows = [
        _row(index, target="remanent_polarization_Pr")
        for index in range(18)
    ] + [
        _row(index + 20, target="double_remanent_polarization_2Pr")
        for index in range(18)
    ]
    rows.append(_row(99, target="coercive_field_Ec"))
    rows.append(_row(100, target="remanent_polarization_Pr", tier="all_traceable"))
    rows.append(_row(101, target="remanent_polarization_Pr", source="reviewed_facts"))
    pd.DataFrame(rows).to_csv(dataset_path, index=False)

    filter_stats = filter_strong_relevance_rows(
        dataset_path=dataset_path,
        output_path=filtered_path,
        db_path=db_path,
    )
    filtered = pd.read_csv(filtered_path)

    assert filter_stats["kept_rows"] == 36
    assert set(filtered["target_property"]) == {
        "remanent_polarization_Pr",
        "double_remanent_polarization_2Pr",
    }
    assert set(filtered["source"]) == {"sample_property_links"}

    model_stats = compare_regression_models(
        dataset_path=filtered_path,
        output_dir=tmp_path / "models",
        min_rows=12,
        db_path=db_path,
    )

    trained = [
        row
        for row in model_stats["results"]
        if row["target_property"] == "remanent_polarization_Pr" and row["status"] == "trained"
    ]
    assert len(trained) >= 4
    assert (tmp_path / "models" / "strong_relevant_model_comparison.csv").exists()
    assert (tmp_path / "models" / "strong_relevant_model_comparison.md").exists()

    cv_stats = cross_validate_regression_models(
        dataset_path=filtered_path,
        output_dir=tmp_path / "cv_models",
        min_rows=12,
        folds=3,
        db_path=db_path,
    )
    trained_cv = [
        row
        for row in cv_stats["summary"]
        if row["target_property"] == "remanent_polarization_Pr" and row["status"] == "trained"
    ]
    assert len(trained_cv) >= 4
    assert (tmp_path / "cv_models" / "cross_validation_summary.csv").exists()
    assert (tmp_path / "cv_models" / "cross_validation_report.md").exists()
