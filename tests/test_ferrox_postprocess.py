from __future__ import annotations

from xml.etree import ElementTree

import numpy as np

from backend.services.ferrox_benchmark import select_benchmark
from backend.services.ferrox_postprocess import (
    domain_metrics,
    electric_field_mv_cm,
    compare_grid_convergence,
    extract_loop_metrics,
    fe_masked_mean,
    polarization_uc_cm2,
    process_field_series,
    switching_time_map,
    summarize_replicates,
    write_vti,
)


def test_units_mask_and_domain_metrics():
    mask = np.array([[[0.0, 0.0], [1.0, 0.0]]])
    pz = np.array([[[0.2, -0.1], [99.0, 0.3]]])
    assert fe_masked_mean(pz, mask) == np.mean([0.2, -0.1, 0.3])
    assert float(polarization_uc_cm2(0.2)) == 20.0
    assert float(electric_field_mv_cm(3e8)) == 3.0
    metrics = domain_metrics(pz, mask)
    assert metrics["positive_domain_fraction"] == 2 / 3
    assert metrics["negative_domain_fraction"] == 1 / 3


def test_loop_metrics_and_insufficient_data():
    field = [-3, -2, -1, 0, 1, 2, 3, 2, 1, 0, -1, -2, -3]
    polarization = [-30, -28, -20, -10, 0, 15, 30, 28, 20, 10, 0, -15, -30]
    metrics = extract_loop_metrics(field, polarization)
    assert metrics["status"] == "ok"
    assert metrics["pr_uc_cm2"] == 10
    assert metrics["two_pr_uc_cm2"] == 20
    assert metrics["ec_mv_cm"] == 1
    assert extract_loop_metrics([0, 1], [1, 2])["status"] == "insufficient_loop"


def test_switching_map_vti_and_series_outputs(tmp_path):
    series = np.array([[[1.0, -1.0]], [[-1.0, -1.0]], [[-1.0, 1.0]]])
    switched = switching_time_map(series, [0.0, 1.0, 2.0])
    assert switched.tolist() == [[1.0, 2.0]]
    fields = {
        "Pz": np.ones((1, 1, 2)),
        "mask": np.zeros((1, 1, 2)),
        "Ez": np.ones((1, 1, 2)) * 1e8,
    }
    vti = write_vti(tmp_path / "fields.vti", fields)
    names = {node.attrib["Name"] for node in ElementTree.parse(vti).findall(".//DataArray")}
    assert names == {"Pz", "mask", "Ez"}
    records = [
        {
            "fields": {**fields, "Pz": fields["Pz"] * p, "Ez": fields["Ez"] * e},
            "voltage_v": e,
        }
        for e, p in zip(
            [-3, -2, -1, 0, 1, 2, 3, 2, 1, 0, -1, -2, -3],
            [-0.3, -0.28, -0.2, -0.1, 0, 0.15, 0.3, 0.28, 0.2, 0.1, 0, -0.15, -0.3],
        )
    ]
    metrics = process_field_series(records, tmp_path)
    assert metrics["status"] == "ok"
    assert metrics["kg_writeback"] is False
    assert "internal_ez_std_mv_cm" in metrics
    assert "median_local_switching_time_seconds" in metrics
    assert (tmp_path / "curve.csv").is_file()
    assert (tmp_path / "metrics.json").is_file()
    assert (tmp_path / "switching_time.vti").is_file()


def test_benchmark_selection_requires_complete_primary_evidence():
    incomplete = {"paper_id": "bad", "paper_type": "review"}
    complete = {
        "doi": "10.1/example",
        "paper_id": "p1",
        "pdf_id": "pdf1",
        "page_number": 4,
        "evidence_text": "Complete measured loop evidence",
        "thickness_nm": 9.5,
        "voltage_or_frequency": "3 V, 1 kHz",
        "pr_or_2pr": "2Pr=40 uC/cm2",
        "ec": "1.2 MV/cm",
        "paper_type": "experimental",
        "material": "Hf0.5Zr0.5O2",
        "stack": "TiN/HZO/TiN",
        "full_pe_loop": True,
        "thickness_condition_count": 2,
    }
    result = select_benchmark([incomplete, complete])
    assert result["status"] == "benchmark_selected"
    assert result["selected"]["paper_id"] == "p1"
    assert select_benchmark([incomplete])["quantitative_calibration_allowed"] is False


def test_replicate_summary_and_grid_convergence_gate():
    rows = [
        {"pr_uc_cm2": 20.0, "two_pr_uc_cm2": 40.0, "ec_mv_cm": 1.0},
        {"pr_uc_cm2": 22.0, "two_pr_uc_cm2": 44.0, "ec_mv_cm": 1.1},
        {"pr_uc_cm2": 21.0, "two_pr_uc_cm2": 42.0, "ec_mv_cm": 1.05},
    ]
    summary = summarize_replicates(rows)
    assert summary["run_count"] == 3
    assert summary["pr_uc_cm2_mean"] == 21.0
    assert summary["pr_uc_cm2_std"] == 1.0
    assert compare_grid_convergence(rows[0], {"pr_uc_cm2": 20.5, "ec_mv_cm": 1.02})["converged"]
    assert not compare_grid_convergence(rows[0], {"pr_uc_cm2": 25.0, "ec_mv_cm": 1.3})["converged"]
