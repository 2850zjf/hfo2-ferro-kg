from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree

import numpy as np


DEFAULT_FIELDS = [
    "Px", "Py", "Pz", "Phi", "PoissonRHS", "Ex", "Ey", "Ez", "holes",
    "electrons", "charge", "epsilon", "mask", "tphase", "alpha", "beta",
    "theta", "PhiDiff",
]


def polarization_uc_cm2(value_c_m2: float | np.ndarray) -> float | np.ndarray:
    return np.asarray(value_c_m2) * 100.0


def electric_field_mv_cm(value_v_m: float | np.ndarray) -> float | np.ndarray:
    return np.asarray(value_v_m) / 1.0e8


def fe_masked_mean(field: np.ndarray, material_mask: np.ndarray) -> float:
    selected = np.asarray(field)[np.isclose(material_mask, 0.0)]
    if selected.size == 0:
        raise ValueError("no_hzo_cells_in_mask")
    return float(np.mean(selected))


def _crossings(x: np.ndarray, y: np.ndarray) -> list[float]:
    values: list[float] = []
    for index in range(len(x) - 1):
        y0, y1 = y[index], y[index + 1]
        if y0 == 0:
            values.append(float(x[index]))
        elif y0 * y1 < 0:
            fraction = -y0 / (y1 - y0)
            values.append(float(x[index] + fraction * (x[index + 1] - x[index])))
    return values


def extract_loop_metrics(
    electric_field_mv_cm_values: Iterable[float],
    polarization_uc_cm2_values: Iterable[float],
) -> dict[str, Any]:
    field = np.asarray(list(electric_field_mv_cm_values), dtype=float)
    polarization = np.asarray(list(polarization_uc_cm2_values), dtype=float)
    if field.size != polarization.size or field.size < 6 or not np.all(np.isfinite(field + polarization)):
        return {"status": "insufficient_loop"}
    remanent = _crossings(polarization, field)
    coercive = _crossings(field, polarization)
    if len(remanent) < 2 or len(coercive) < 2 or np.ptp(field) <= 0:
        return {"status": "insufficient_loop"}
    pr_negative, pr_positive = min(remanent), max(remanent)
    ec_negative, ec_positive = min(coercive), max(coercive)
    return {
        "status": "ok",
        "pr_uc_cm2": (abs(pr_negative) + abs(pr_positive)) / 2.0,
        "two_pr_uc_cm2": pr_positive - pr_negative,
        "ec_mv_cm": (abs(ec_negative) + abs(ec_positive)) / 2.0,
        "ec_negative_mv_cm": ec_negative,
        "ec_positive_mv_cm": ec_positive,
        "saturation_polarization_uc_cm2": float(np.max(np.abs(polarization))),
        "loop_area_uc_mv_cm2": float(abs(np.trapezoid(polarization, field))),
    }


def summarize_replicates(metrics_rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(metrics_rows)
    if not rows:
        return {"status": "no_runs"}
    keys = ("pr_uc_cm2", "two_pr_uc_cm2", "ec_mv_cm", "loop_area_uc_mv_cm2", "domain_wall_density")
    summary: dict[str, Any] = {"status": "ok", "run_count": len(rows)}
    for key in keys:
        values = np.asarray([row[key] for row in rows if row.get(key) is not None], dtype=float)
        if values.size:
            summary[f"{key}_mean"] = float(np.mean(values))
            summary[f"{key}_std"] = float(np.std(values, ddof=1)) if values.size > 1 else 0.0
    return summary


def compare_grid_convergence(coarse: dict[str, Any], fine: dict[str, Any], tolerance_percent: float = 5.0) -> dict[str, Any]:
    differences: dict[str, float] = {}
    for key in ("pr_uc_cm2", "ec_mv_cm"):
        coarse_value, fine_value = coarse.get(key), fine.get(key)
        if coarse_value is None or fine_value in {None, 0}:
            return {"status": "insufficient_metrics", "converged": False}
        differences[key] = abs(float(coarse_value) - float(fine_value)) / abs(float(fine_value)) * 100.0
    return {
        "status": "ok",
        "tolerance_percent": tolerance_percent,
        "differences_percent": differences,
        "converged": all(value <= tolerance_percent for value in differences.values()),
    }


def domain_metrics(pz: np.ndarray, material_mask: np.ndarray) -> dict[str, float]:
    active = np.isclose(material_mask, 0.0)
    values = np.asarray(pz)[active]
    if values.size == 0:
        raise ValueError("no_hzo_cells_in_mask")
    signs = np.sign(np.asarray(pz))
    active_pairs = 0
    walls = 0
    for axis in range(signs.ndim):
        left = [slice(None)] * signs.ndim
        right = [slice(None)] * signs.ndim
        left[axis] = slice(None, -1)
        right[axis] = slice(1, None)
        pair_mask = active[tuple(left)] & active[tuple(right)]
        active_pairs += int(pair_mask.sum())
        walls += int(((signs[tuple(left)] * signs[tuple(right)] < 0) & pair_mask).sum())
    return {
        "positive_domain_fraction": float(np.mean(values > 0)),
        "negative_domain_fraction": float(np.mean(values < 0)),
        "domain_wall_density": float(walls / active_pairs) if active_pairs else 0.0,
    }


def switching_time_map(pz_series: np.ndarray, times: Iterable[float]) -> np.ndarray:
    series = np.asarray(pz_series)
    time_values = np.asarray(list(times), dtype=float)
    if series.shape[0] != time_values.size:
        raise ValueError("time_axis_mismatch")
    initial = np.sign(series[0])
    switched = np.sign(series) != initial
    first = np.argmax(switched, axis=0)
    result = np.full(series.shape[1:], np.nan)
    ever = np.any(switched, axis=0)
    result[ever] = time_values[first[ever]]
    return result


def read_plotfile(plotfile: str | Path) -> dict[str, Any]:
    root = Path(plotfile)
    lines = (root / "Header").read_text(encoding="utf-8").splitlines()
    count = int(lines[1])
    names = lines[2:2 + count]
    dim_index = 2 + count
    bounds = [int(value) for value in re.findall(r"-?\d+", lines[dim_index + 6])]
    nx, ny, nz = bounds[3] + 1, bounds[4] + 1, bounds[5] + 1
    lo = tuple(float(v) for v in lines[dim_index + 3].split())
    hi = tuple(float(v) for v in lines[dim_index + 4].split())
    data_path = root / "Level_0" / "Cell_D_00000"
    with data_path.open("rb") as stream:
        stream.readline()
        raw = np.frombuffer(stream.read(), dtype="<f8")
    expected = count * nx * ny * nz
    if raw.size != expected:
        raise ValueError(f"unsupported_multifab_layout:{raw.size}:{expected}")
    values = raw.reshape(count, nz, ny, nx)
    return {
        "fields": dict(zip(names, values)),
        "origin": lo,
        "spacing": tuple((hi[i] - lo[i]) / (nx, ny, nz)[i] for i in range(3)),
        "time": float(lines[dim_index + 2]),
    }


def write_vti(path: str | Path, fields: dict[str, np.ndarray], origin=(0.0, 0.0, 0.0), spacing=(1.0, 1.0, 1.0)) -> Path:
    target = Path(path)
    first = np.asarray(next(iter(fields.values())))
    if first.ndim != 3 or any(np.asarray(value).shape != first.shape for value in fields.values()):
        raise ValueError("vti_fields_must_share_3d_shape")
    nz, ny, nx = first.shape
    vtk = ElementTree.Element("VTKFile", type="ImageData", version="1.0", byte_order="LittleEndian")
    image = ElementTree.SubElement(vtk, "ImageData", WholeExtent=f"0 {nx} 0 {ny} 0 {nz}", Origin=" ".join(map(str, origin)), Spacing=" ".join(map(str, spacing)))
    piece = ElementTree.SubElement(image, "Piece", Extent=f"0 {nx} 0 {ny} 0 {nz}")
    cell_data = ElementTree.SubElement(piece, "CellData", Scalars="Pz" if "Pz" in fields else next(iter(fields)))
    for name, value in fields.items():
        array = ElementTree.SubElement(cell_data, "DataArray", type="Float64", Name=name, format="ascii")
        array.text = " ".join(f"{number:.12g}" for number in np.asarray(value).ravel())
    ElementTree.SubElement(piece, "PointData")
    target.parent.mkdir(parents=True, exist_ok=True)
    ElementTree.ElementTree(vtk).write(target, encoding="utf-8", xml_declaration=True)
    return target


def process_field_series(records: list[dict[str, Any]], output_dir: str | Path) -> dict[str, Any]:
    if not records:
        raise ValueError("no_plotfiles")
    rows: list[dict[str, float]] = []
    for index, record in enumerate(records):
        fields = record["fields"]
        mask = np.asarray(fields["mask"])
        pz = fe_masked_mean(np.asarray(fields["Pz"]), mask)
        ez = fe_masked_mean(np.asarray(fields["Ez"]), mask)
        rows.append({
            "step": float(record.get("step", index)),
            "time": float(record.get("time", index)),
            "voltage_v": float(record.get("voltage_v", np.nan)),
            "polarization_uc_cm2": float(polarization_uc_cm2(pz)),
            "electric_field_mv_cm": float(electric_field_mv_cm(ez)),
        })
    metrics = extract_loop_metrics(
        [row["electric_field_mv_cm"] for row in rows],
        [row["polarization_uc_cm2"] for row in rows],
    )
    metrics.update(domain_metrics(np.asarray(records[-1]["fields"]["Pz"]), np.asarray(records[-1]["fields"]["mask"])))
    final_mask = np.isclose(np.asarray(records[-1]["fields"]["mask"]), 0.0)
    final_ez = np.asarray(electric_field_mv_cm(records[-1]["fields"]["Ez"]))[final_mask]
    metrics.update(
        {
            "internal_ez_mean_mv_cm": float(np.mean(final_ez)),
            "internal_ez_std_mv_cm": float(np.std(final_ez)),
            "internal_ez_p05_mv_cm": float(np.percentile(final_ez, 5)),
            "internal_ez_p95_mv_cm": float(np.percentile(final_ez, 95)),
        }
    )
    if len(records) >= 2:
        switching = switching_time_map(
            np.stack([np.asarray(record["fields"]["Pz"]) for record in records]),
            [float(record.get("time", index)) for index, record in enumerate(records)],
        )
        switched_hzo = switching[final_mask & np.isfinite(switching)]
        metrics["switched_cell_fraction"] = float(np.mean(np.isfinite(switching[final_mask])))
        metrics["median_local_switching_time_seconds"] = (
            float(np.median(switched_hzo)) if switched_hzo.size else None
        )
    metrics.update({"scientific_status": "simulation_only_not_experimental_fact", "kg_writeback": False})
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "curve.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (output / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    if len(records) >= 2:
        np.save(output / "switching_time_seconds.npy", switching)
        write_vti(
            output / "switching_time.vti",
            {"switching_time_seconds": np.nan_to_num(switching, nan=-1.0)},
            records[-1].get("origin", (0.0, 0.0, 0.0)),
            records[-1].get("spacing", (1.0, 1.0, 1.0)),
        )
    return metrics


def process_run_outputs(run_dir: str | Path) -> dict[str, Any]:
    root = Path(run_dir).resolve()
    state_path = root / "run.json"
    if not state_path.is_file():
        raise ValueError("run_json_not_found")
    plotfiles = sorted(path for path in root.glob("plt*") if path.is_dir())
    if not plotfiles:
        raise ValueError("no_plotfiles")
    records: list[dict[str, Any]] = []
    vti_dir = root / "vti"
    for plotfile in plotfiles:
        record = read_plotfile(plotfile)
        suffix = re.search(r"(\d+)$", plotfile.name)
        record["step"] = int(suffix.group(1)) if suffix else len(records)
        thickness_m = record["spacing"][2] * np.asarray(record["fields"]["Ez"]).shape[0]
        mean_ez = fe_masked_mean(record["fields"]["Ez"], record["fields"]["mask"])
        record["voltage_v"] = mean_ez * thickness_m
        write_vti(vti_dir / f"{plotfile.name}.vti", record["fields"], record["origin"], record["spacing"])
        records.append(record)
    metrics = process_field_series(records, root)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(
        {
            "postprocess_status": "complete",
            "plotfile_count": len(plotfiles),
            "vti_count": len(plotfiles),
            "metrics_status": metrics["status"],
        }
    )
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return {
        "plotfile_count": len(plotfiles),
        "curve_path": str(root / "curve.csv"),
        "metrics_path": str(root / "metrics.json"),
        "vti_dir": str(vti_dir),
        "metrics": metrics,
    }
