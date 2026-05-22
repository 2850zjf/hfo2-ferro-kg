from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from backend.core.config import PROJECT_ROOT
from backend.services.design_dataset import build_design_dataset
from backend.services.pipeline_log import record_pipeline_run


NUMERIC_FEATURES = [
    "year",
    "is_review",
    "zr_fraction",
    "film_thickness_nm",
    "annealing_temperature_c",
    "annealing_time_s",
]

CATEGORICAL_FEATURES = [
    "material_name",
    "material_family",
    "formula",
    "dopant_elements",
    "dopant_concentration",
    "deposition_method",
    "annealing_atmosphere",
    "top_electrode",
    "bottom_electrode",
    "electrode_stack",
    "substrate",
    "device_type",
    "phase_name",
    "space_group",
    "wake_up_or_endurance_state",
    "source",
    "review_status",
]

DEFAULT_TARGETS = [
    "remanent_polarization_Pr",
    "double_remanent_polarization_2Pr",
    "coercive_field_Ec",
    "endurance_cycles",
    "retention_time",
    "memory_window",
    "leakage_current_density",
]


def _safe_model_name(target_property: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in target_property)


def _load_or_build_dataset(dataset_path: Path | None = None) -> tuple[pd.DataFrame, Path]:
    target = dataset_path or PROJECT_ROOT / "data" / "design" / "hfo2_design_dataset.csv"
    if not target.exists():
        build_design_dataset(output_path=target)
    if not target.exists():
        return pd.DataFrame(), target
    return pd.read_csv(target), target


def _prepare_target_frame(df: pd.DataFrame, target_property: str) -> pd.DataFrame:
    if df.empty or "target_property" not in df:
        return pd.DataFrame()
    subset = df[df["target_property"].astype(str) == target_property].copy()
    if "model_include" in subset:
        include = pd.to_numeric(subset["model_include"], errors="coerce").fillna(0).astype(int)
        subset = subset[include == 1].copy()
    value_column = "model_target_value" if "model_target_value" in subset else "target_value"
    if value_column not in subset:
        return pd.DataFrame()
    subset["target_value"] = pd.to_numeric(subset[value_column], errors="coerce")
    for column in NUMERIC_FEATURES:
        if column not in subset:
            subset[column] = None
        subset[column] = pd.to_numeric(subset[column], errors="coerce")
    for column in CATEGORICAL_FEATURES:
        if column not in subset:
            subset[column] = ""
        subset[column] = subset[column].fillna("").astype(str)
    return subset.dropna(subset=["target_value"])


def _make_pipeline(random_state: int) -> Pipeline:
    numeric_pipe = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
        ]
    )
    categorical_pipe = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore", min_frequency=2)),
        ]
    )
    preprocessor = ColumnTransformer(
        transformers=[
            ("num", numeric_pipe, NUMERIC_FEATURES),
            ("cat", categorical_pipe, CATEGORICAL_FEATURES),
        ]
    )
    model = RandomForestRegressor(
        n_estimators=250,
        min_samples_leaf=2,
        random_state=random_state,
        n_jobs=-1,
    )
    return Pipeline(steps=[("features", preprocessor), ("model", model)])


def train_design_models(
    dataset_path: Path | None = None,
    output_dir: Path | None = None,
    targets: list[str] | None = None,
    min_rows: int = 12,
    test_size: float = 0.25,
    random_state: int = 42,
    db_path: Path | None = None,
) -> dict[str, Any]:
    df, source_path = _load_or_build_dataset(dataset_path)
    out_dir = output_dir or PROJECT_ROOT / "models" / "design_models"
    out_dir.mkdir(parents=True, exist_ok=True)
    selected_targets = targets or DEFAULT_TARGETS
    metrics: list[dict[str, Any]] = []

    for target_property in selected_targets:
        subset = _prepare_target_frame(df, target_property)
        row_count = len(subset)
        if row_count < min_rows:
            metrics.append(
                {
                    "target_property": target_property,
                    "status": "skipped_not_enough_rows",
                    "rows": row_count,
                    "min_rows": min_rows,
                }
            )
            continue

        X = subset[NUMERIC_FEATURES + CATEGORICAL_FEATURES]
        y = subset["target_value"]
        holdout = max(1, int(round(row_count * test_size)))
        holdout = min(holdout, row_count - 2)
        if holdout < 1:
            metrics.append(
                {
                    "target_property": target_property,
                    "status": "skipped_not_enough_holdout",
                    "rows": row_count,
                    "min_rows": min_rows,
                }
            )
            continue

        X_train, X_test, y_train, y_test = train_test_split(
            X,
            y,
            test_size=holdout,
            random_state=random_state,
        )
        pipeline = _make_pipeline(random_state=random_state)
        pipeline.fit(X_train, y_train)
        predictions = pipeline.predict(X_test)
        baseline_predictions = [float(y_train.mean())] * len(y_test)
        mae = float(mean_absolute_error(y_test, predictions))
        baseline_mae = float(mean_absolute_error(y_test, baseline_predictions))
        mse = float(mean_squared_error(y_test, predictions))
        r2 = float(r2_score(y_test, predictions)) if len(y_test) > 1 else None
        model_path = out_dir / f"{_safe_model_name(target_property)}.pkl"
        with model_path.open("wb") as fh:
            pickle.dump(
                {
                    "target_property": target_property,
                    "features": NUMERIC_FEATURES + CATEGORICAL_FEATURES,
                    "pipeline": pipeline,
                    "dataset_path": str(source_path),
                },
                fh,
            )
        metrics.append(
            {
                "target_property": target_property,
                "status": "trained",
                "rows": row_count,
                "train_rows": len(X_train),
                "test_rows": len(X_test),
                "mae": mae,
                "rmse": mse**0.5,
                "r2": r2,
                "baseline_mae": baseline_mae,
                "improvement_vs_baseline": baseline_mae - mae,
                "model_path": str(model_path),
            }
        )

    metrics_path = out_dir / "design_model_metrics.json"
    payload = {
        "dataset_path": str(source_path),
        "models_dir": str(out_dir),
        "targets": metrics,
        "trained_models": sum(1 for item in metrics if item["status"] == "trained"),
        "skipped_models": sum(1 for item in metrics if item["status"] != "trained"),
    }
    metrics_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["metrics_path"] = str(metrics_path)
    record_pipeline_run("22_train_design_models", "ok", payload, db_path=db_path)
    return payload


def load_design_model_metrics(metrics_path: Path | None = None) -> dict[str, Any] | None:
    target = metrics_path or PROJECT_ROOT / "models" / "design_models" / "design_model_metrics.json"
    if not target.exists():
        return None
    return json.loads(target.read_text(encoding="utf-8"))
