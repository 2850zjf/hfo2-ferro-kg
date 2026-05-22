from __future__ import annotations

import json

import pandas as pd

from backend.db.init_db import init_database
from backend.db.session import connect
from backend.services.design_dataset import build_design_dataset


def test_build_design_dataset_combines_reviewed_and_benchmark_rows(tmp_path):
    db_path = tmp_path / "design.sqlite3"
    output_path = tmp_path / "design.csv"
    init_database(db_path)

    reviewed_payload = {
        "material": {
            "canonical_name": "Hf0.5Zr0.5O2",
            "material_family": "HZO",
            "formula": "Hf0.5Zr0.5O2",
            "dopant_elements": ["Zr"],
            "zr_fraction": 0.5,
        },
        "sample": {
            "film_thickness_nm": 10,
            "deposition_method": "ALD",
            "annealing_temperature_c": 500,
            "annealing_time_s": 30,
            "annealing_atmosphere": "N2",
            "top_electrode": "TiN",
            "bottom_electrode": "TiN",
            "device_stack": "TiN/HZO/TiN",
            "substrate": "Si",
        },
        "property": {
            "property_name": "double_remanent_polarization_2Pr",
            "value": 40,
            "unit": "uC/cm2",
            "normalized_value": 40,
            "normalized_unit": "uC/cm2",
            "device_type": "capacitor",
            "evidence_text": "A 2Pr value of 40 uC/cm2 was reported.",
        },
        "phases": [{"phase_name": "orthorhombic", "space_group": "Pca21"}],
    }
    benchmark_payload = {
        "benchmark_records": [
            {
                "input_variables": {
                    "material_name": "La:HfO2",
                    "material_family": "La:HfO2",
                    "film_thickness_nm": 12,
                    "deposition_method": "sputtering",
                    "annealing_temperature_c": 600,
                    "electrode_stack": "Pt/La:HfO2/Pt",
                    "phase": "orthorhombic",
                },
                "output_targets": {"Ec": {"value": 2.1, "unit": "MV/cm"}},
                "conditions": {"temperature": "room temperature"},
                "evidence_text": "The coercive field was 2.1 MV/cm.",
            }
        ]
    }

    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO papers (paper_id, title, doi, year, paper_type) VALUES (?, ?, ?, ?, ?)",
            ("paper_1", "HZO benchmark paper", "10.1000/test", 2026, "experimental"),
        )
        conn.execute(
            """
            INSERT INTO reviewed_facts (
                fact_id, paper_id, pdf_id, chunk_id, page_number, fact_type,
                payload_json, review_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "fact_1",
                "paper_1",
                "pdf_1",
                "chunk_1",
                2,
                "ferroelectric_property",
                json.dumps(reviewed_payload),
                "approved",
            ),
        )
        conn.execute(
            """
            INSERT INTO benchmark_extractions (
                extraction_id, paper_id, pdf_id, chunk_id, page_number,
                source_type, payload_json, model_name, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "bench_1",
                "paper_1",
                "pdf_1",
                "chunk_2",
                3,
                "text",
                json.dumps(benchmark_payload),
                "qwen3.7-max",
                "ok",
            ),
        )
        conn.commit()

    stats = build_design_dataset(output_path=output_path, db_path=db_path)
    df = pd.read_csv(output_path)

    assert stats["rows"] == 2
    assert set(df["source"]) == {"reviewed_facts", "benchmark_extractions"}
    assert set(df["target_property"]) == {
        "double_remanent_polarization_2Pr",
        "coercive_field_Ec",
    }
    assert "evidence_text" in df.columns
