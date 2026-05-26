from __future__ import annotations

import json
import math
import pickle
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backend.core.config import PROJECT_ROOT
from backend.services.design_dataset import build_design_dataset
from backend.services.design_model import CATEGORICAL_FEATURES, NUMERIC_FEATURES
from backend.services.pipeline_log import record_pipeline_run


DEFAULT_TARGET = "double_remanent_polarization_2Pr"
OUTPUT_COLUMNS = [
    "candidate_id",
    "target_property",
    "predicted_value",
    "prediction_unit",
    "uncertainty",
    "feasibility_score",
    "evidence_score",
    "active_learning_score",
    "material_name",
    "material_family",
    "formula",
    "dopant_elements",
    "zr_fraction",
    "film_thickness_nm",
    "deposition_method",
    "annealing_temperature_c",
    "annealing_time_s",
    "annealing_atmosphere",
    "top_electrode",
    "bottom_electrode",
    "electrode_stack",
    "substrate",
    "device_type",
    "phase_name",
    "space_group",
    "nearest_evidence_json",
    "reason",
]


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "nan"):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def _load_dataset(dataset_path: Path | None = None) -> tuple[pd.DataFrame, Path]:
    target = dataset_path or PROJECT_ROOT / "data" / "design" / "hfo2_design_dataset.csv"
    if not target.exists():
        build_design_dataset(output_path=target)
    if not target.exists():
        return pd.DataFrame(), target
    return pd.read_csv(target), target


def _load_model(target_property: str, models_dir: Path | None = None) -> dict[str, Any] | None:
    directory = models_dir or PROJECT_ROOT / "models" / "design_models"
    path = directory / f"{target_property}.pkl"
    if not path.exists():
        return None
    with path.open("rb") as fh:
        payload = pickle.load(fh)
    payload["model_path"] = str(path)
    return payload


def _top_values(df: pd.DataFrame, column: str, limit: int = 5) -> list[Any]:
    if column not in df:
        return [""]
    series = df[column].fillna("").astype(str).str.strip()
    series = series[series != ""]
    if series.empty:
        return [""]
    return series.value_counts().head(limit).index.tolist()


def _quantile_values(df: pd.DataFrame, column: str, fallback: list[float], max_values: int = 4) -> list[float | str]:
    if column not in df:
        return fallback
    numeric = pd.to_numeric(df[column], errors="coerce").dropna()
    numeric = numeric[(numeric > 0) & np.isfinite(numeric)]
    if numeric.empty:
        return fallback
    values = []
    for q in np.linspace(0.15, 0.85, max_values):
        value = float(numeric.quantile(q))
        if column in {"film_thickness_nm", "annealing_temperature_c", "annealing_time_s"}:
            value = round(value, 2)
        values.append(value)
    unique: list[float] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return unique or fallback


def _candidate_seed_rows(df: pd.DataFrame, target_property: str) -> pd.DataFrame:
    if df.empty or "target_property" not in df:
        return pd.DataFrame()
    subset = df[df["target_property"].astype(str) == target_property].copy()
    if "model_include" in subset:
        include = pd.to_numeric(subset["model_include"], errors="coerce").fillna(0).astype(int)
        subset = subset[include == 1].copy()
    return subset


def _build_candidate_grid(df: pd.DataFrame, max_candidates: int) -> pd.DataFrame:
    choices = {
        "material_name": _top_values(df, "material_name", 6),
        "material_family": _top_values(df, "material_family", 4),
        "formula": _top_values(df, "formula", 5),
        "dopant_elements": _top_values(df, "dopant_elements", 5),
        "deposition_method": _top_values(df, "deposition_method", 4),
        "annealing_atmosphere": _top_values(df, "annealing_atmosphere", 4),
        "top_electrode": _top_values(df, "top_electrode", 4),
        "bottom_electrode": _top_values(df, "bottom_electrode", 4),
        "electrode_stack": _top_values(df, "electrode_stack", 6),
        "substrate": _top_values(df, "substrate", 4),
        "device_type": _top_values(df, "device_type", 4),
        "phase_name": _top_values(df, "phase_name", 4),
        "space_group": _top_values(df, "space_group", 4),
        "wake_up_or_endurance_state": _top_values(df, "wake_up_or_endurance_state", 3),
    }
    numeric_choices = {
        "zr_fraction": _quantile_values(df, "zr_fraction", [0.5], 3),
        "film_thickness_nm": _quantile_values(df, "film_thickness_nm", [8.0, 10.0, 12.0], 4),
        "annealing_temperature_c": _quantile_values(df, "annealing_temperature_c", [400.0, 500.0, 600.0], 4),
        "annealing_time_s": _quantile_values(df, "annealing_time_s", [30.0, 60.0], 3),
        "year": _quantile_values(df, "year", [2024.0], 2),
        "is_review": [0],
    }

    material_tuples = list(
        zip(
            choices["material_name"],
            (choices["material_family"] * 10)[: len(choices["material_name"])],
            (choices["formula"] * 10)[: len(choices["material_name"])],
            (choices["dopant_elements"] * 10)[: len(choices["material_name"])],
        )
    )
    if not material_tuples:
        material_tuples = [("", "", "", "")]

    rows: list[dict[str, Any]] = []
    dimensions = [
        material_tuples,
        numeric_choices["zr_fraction"],
        numeric_choices["film_thickness_nm"],
        choices["deposition_method"],
        numeric_choices["annealing_temperature_c"],
        numeric_choices["annealing_time_s"],
        choices["annealing_atmosphere"],
        choices["electrode_stack"],
        choices["device_type"],
        choices["phase_name"],
    ]
    for index, combo in enumerate(product(*dimensions)):
        if index >= max_candidates:
            break
        material, zr_fraction, thickness, deposition, anneal_temp, anneal_time, atmosphere, stack, device, phase = combo
        material_name, material_family, formula, dopants = material
        top, bottom = "", ""
        if isinstance(stack, str) and "/" in stack:
            parts = [part.strip() for part in stack.split("/") if part.strip()]
            if parts:
                top = parts[0]
                bottom = parts[-1]
        rows.append(
            {
                "year": numeric_choices["year"][0] if numeric_choices["year"] else "",
                "is_review": 0,
                "zr_fraction": zr_fraction,
                "film_thickness_nm": thickness,
                "annealing_temperature_c": anneal_temp,
                "annealing_time_s": anneal_time,
                "material_name": material_name,
                "material_family": material_family,
                "formula": formula,
                "dopant_elements": dopants,
                "dopant_concentration": "",
                "deposition_method": deposition,
                "annealing_atmosphere": atmosphere,
                "top_electrode": top,
                "bottom_electrode": bottom,
                "electrode_stack": stack,
                "substrate": choices["substrate"][0] if choices["substrate"] else "",
                "device_type": device,
                "phase_name": phase,
                "space_group": choices["space_group"][0] if choices["space_group"] else "",
                "wake_up_or_endurance_state": choices["wake_up_or_endurance_state"][0]
                if choices["wake_up_or_endurance_state"]
                else "",
                "source": "active_learning_candidate",
                "review_status": "candidate",
            }
        )
    return pd.DataFrame(rows)


def _predict_with_uncertainty(model_payload: dict[str, Any], X: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    pipeline = model_payload["pipeline"]
    predictions = np.asarray(pipeline.predict(X), dtype=float)
    try:
        transformed = pipeline.named_steps["features"].transform(X)
        forest = pipeline.named_steps["model"]
        tree_predictions = np.asarray([tree.predict(transformed) for tree in forest.estimators_], dtype=float)
        uncertainty = tree_predictions.std(axis=0)
    except Exception:
        uncertainty = np.zeros(len(predictions), dtype=float)
    return predictions, uncertainty


def _minmax(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce").fillna(0)
    low = float(numeric.min())
    high = float(numeric.max())
    if math.isclose(low, high):
        return pd.Series([0.5] * len(numeric), index=numeric.index)
    return (numeric - low) / (high - low)


def _row_similarity(candidate: pd.Series, row: pd.Series) -> float:
    weights = {
        "material_name": 3.0,
        "material_family": 2.0,
        "formula": 2.0,
        "dopant_elements": 1.5,
        "electrode_stack": 2.0,
        "deposition_method": 1.5,
        "phase_name": 1.5,
        "device_type": 1.0,
    }
    score = 0.0
    total = sum(weights.values())
    for column, weight in weights.items():
        left = str(candidate.get(column) or "").strip().lower()
        right = str(row.get(column) or "").strip().lower()
        if left and right and left == right:
            score += weight
    for column, scale in [
        ("film_thickness_nm", 30.0),
        ("annealing_temperature_c", 250.0),
        ("annealing_time_s", 600.0),
    ]:
        left = _safe_float(candidate.get(column))
        right = _safe_float(row.get(column))
        if left is None or right is None:
            continue
        score += max(0.0, 1.0 - abs(left - right) / scale)
        total += 1.0
    return score / total if total else 0.0


def _nearest_evidence(candidate: pd.Series, evidence_df: pd.DataFrame, limit: int = 3) -> tuple[float, list[dict[str, Any]]]:
    if evidence_df.empty:
        return 0.0, []
    scores = evidence_df.apply(lambda row: _row_similarity(candidate, row), axis=1)
    top = evidence_df.assign(_similarity=scores).sort_values("_similarity", ascending=False).head(limit)
    evidence = []
    for _, row in top.iterrows():
        evidence.append(
            {
                "similarity": round(float(row.get("_similarity") or 0), 3),
                "title": row.get("title", ""),
                "doi": row.get("doi", ""),
                "page_number": row.get("page_number", ""),
                "target_property": row.get("target_property", ""),
                "target_value": row.get("target_value", ""),
                "target_unit": row.get("target_unit", ""),
                "evidence_text": str(row.get("evidence_text", ""))[:360],
            }
        )
    return float(top["_similarity"].max() if not top.empty else 0.0), evidence


def _feasibility(candidate: pd.Series, evidence_df: pd.DataFrame) -> float:
    required = [
        "material_name",
        "film_thickness_nm",
        "deposition_method",
        "annealing_temperature_c",
        "electrode_stack",
        "device_type",
    ]
    present = sum(1 for column in required if str(candidate.get(column) or "").strip())
    score = present / len(required)
    thickness = _safe_float(candidate.get("film_thickness_nm"))
    if thickness is not None and not (1 <= thickness <= 100):
        score -= 0.2
    anneal_temp = _safe_float(candidate.get("annealing_temperature_c"))
    if anneal_temp is not None and not (250 <= anneal_temp <= 900):
        score -= 0.2
    if evidence_df.empty:
        score -= 0.2
    return max(0.0, min(1.0, score))


def recommend_active_learning_candidates(
    target_property: str = DEFAULT_TARGET,
    dataset_path: Path | None = None,
    models_dir: Path | None = None,
    output_dir: Path | None = None,
    max_candidates: int = 1200,
    top_n: int = 80,
    db_path: Path | None = None,
) -> dict[str, Any]:
    df, source_path = _load_dataset(dataset_path)
    out_dir = output_dir or PROJECT_ROOT / "data" / "design"
    out_dir.mkdir(parents=True, exist_ok=True)
    if df.empty:
        stats = {"status": "no_dataset", "dataset_path": str(source_path), "candidates": 0}
        record_pipeline_run("25_recommend_active_learning", "skipped", stats, db_path=db_path)
        return stats

    evidence_df = _candidate_seed_rows(df, target_property)
    if evidence_df.empty:
        evidence_df = df.copy()

    model_payload = _load_model(target_property, models_dir=models_dir)
    candidates = _build_candidate_grid(evidence_df if not evidence_df.empty else df, max_candidates=max_candidates)
    if candidates.empty:
        stats = {"status": "no_candidate_grid", "dataset_path": str(source_path), "candidates": 0}
        record_pipeline_run("25_recommend_active_learning", "skipped", stats, db_path=db_path)
        return stats

    for column in NUMERIC_FEATURES:
        if column not in candidates:
            candidates[column] = np.nan
        candidates[column] = pd.to_numeric(candidates[column], errors="coerce")
    for column in CATEGORICAL_FEATURES:
        if column not in candidates:
            candidates[column] = ""
        candidates[column] = candidates[column].fillna("").astype(str)

    model_status = "missing_model"
    if model_payload is not None:
        X = candidates[NUMERIC_FEATURES + CATEGORICAL_FEATURES]
        predictions, uncertainty = _predict_with_uncertainty(model_payload, X)
        candidates["predicted_value"] = predictions
        candidates["uncertainty"] = uncertainty
        model_status = "model"
    else:
        value_col = "model_target_value" if "model_target_value" in evidence_df else "target_value"
        base = pd.to_numeric(evidence_df.get(value_col, pd.Series(dtype=float)), errors="coerce").dropna()
        candidates["predicted_value"] = float(base.quantile(0.75)) if not base.empty else 0.0
        candidates["uncertainty"] = 0.0

    prediction_unit = ""
    if "model_target_unit" in evidence_df:
        units = evidence_df["model_target_unit"].dropna().astype(str)
        units = units[units != ""]
        prediction_unit = units.mode().iloc[0] if not units.empty else ""
    if not prediction_unit and "target_unit" in evidence_df:
        units = evidence_df["target_unit"].dropna().astype(str)
        units = units[units != ""]
        prediction_unit = units.mode().iloc[0] if not units.empty else ""

    evidence_scores = []
    nearest_payloads = []
    feasibility_scores = []
    for _, row in candidates.iterrows():
        evidence_score, nearest = _nearest_evidence(row, evidence_df, limit=3)
        evidence_scores.append(evidence_score)
        nearest_payloads.append(nearest)
        feasibility_scores.append(_feasibility(row, evidence_df))
    candidates["evidence_score"] = evidence_scores
    candidates["nearest_evidence_json"] = [
        json.dumps(item, ensure_ascii=False) for item in nearest_payloads
    ]
    candidates["feasibility_score"] = feasibility_scores

    candidates["pred_norm"] = _minmax(candidates["predicted_value"])
    candidates["uncertainty_norm"] = _minmax(candidates["uncertainty"])
    candidates["active_learning_score"] = (
        0.48 * candidates["pred_norm"]
        + 0.27 * candidates["uncertainty_norm"]
        + 0.15 * candidates["feasibility_score"]
        + 0.10 * candidates["evidence_score"]
    )
    candidates["target_property"] = target_property
    candidates["prediction_unit"] = prediction_unit
    candidates["reason"] = candidates.apply(
        lambda row: (
            f"high predicted {target_property}, uncertainty={row['uncertainty']:.3g}, "
            f"feasibility={row['feasibility_score']:.2f}, evidence_support={row['evidence_score']:.2f}"
        ),
        axis=1,
    )
    candidates = candidates.sort_values("active_learning_score", ascending=False).head(top_n).copy()
    candidates.insert(0, "candidate_id", [f"alc_{index + 1:04d}" for index in range(len(candidates))])

    csv_path = out_dir / "active_learning_candidates.csv"
    json_path = out_dir / "active_learning_candidates.json"
    candidates[OUTPUT_COLUMNS].to_csv(csv_path, index=False, encoding="utf-8-sig")
    json_path.write_text(
        json.dumps(candidates[OUTPUT_COLUMNS].to_dict(orient="records"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    stats = {
        "status": "ok",
        "model_status": model_status,
        "target_property": target_property,
        "dataset_path": str(source_path),
        "candidate_grid": int(max_candidates),
        "candidates": int(len(candidates)),
        "output_csv": str(csv_path),
        "output_json": str(json_path),
        "best_score": float(candidates["active_learning_score"].max()) if not candidates.empty else None,
        "best_predicted_value": float(candidates["predicted_value"].max()) if not candidates.empty else None,
        "model_path": model_payload.get("model_path") if model_payload else "",
    }
    record_pipeline_run("25_recommend_active_learning", "ok", stats, db_path=db_path)
    return stats
