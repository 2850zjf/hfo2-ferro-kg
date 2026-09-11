from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pandas as pd

from backend.core.config import PROJECT_ROOT
from backend.services.design_dataset import build_design_dataset
from backend.services.design_model import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    _prepare_target_frame,
)
from backend.services.pipeline_log import record_pipeline_run


PRIMARY_TARGETS = [
    "remanent_polarization_Pr",
    "double_remanent_polarization_2Pr",
]

REJECT_AI_STATUSES = {"reject", "needs_human_review", "usable_for_rag_only"}


def _load_design_dataset(dataset_path: Path | None = None) -> tuple[pd.DataFrame, Path]:
    target = dataset_path or PROJECT_ROOT / "data" / "design" / "hfo2_design_dataset.csv"
    if not target.exists():
        build_design_dataset(output_path=target)
    if not target.exists():
        return pd.DataFrame(), target
    return pd.read_csv(target), target


def _truthy_series(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.lower().isin({"1", "true", "yes"})


def _paper_groups(frame: pd.DataFrame) -> pd.Series:
    if "paper_id" in frame:
        groups = frame["paper_id"].fillna("").astype(str)
    else:
        groups = pd.Series("", index=frame.index, dtype=str)
    if "pdf_id" in frame:
        fallback = frame["pdf_id"].fillna("").astype(str)
        groups = groups.where(groups.str.strip().ne(""), fallback)
    return groups.where(groups.str.strip().ne(""), frame.index.astype(str))


def filter_strong_relevance_rows(
    dataset_path: Path | None = None,
    output_path: Path | None = None,
    targets: list[str] | None = None,
    tiers: set[str] | None = None,
    require_sample_level: bool = True,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Create a non-destructive strong-relevance slice for modeling."""

    df, source_path = _load_design_dataset(dataset_path)
    selected_targets = targets or PRIMARY_TARGETS
    selected_tiers = tiers or {"strong_only"}
    out_path = output_path or PROJECT_ROOT / "data" / "design" / "hfo2_design_dataset_strong_relevant.csv"

    if df.empty:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out_path, index=False, encoding="utf-8-sig")
        stats = {
            "source_path": str(source_path),
            "output_path": str(out_path),
            "input_rows": 0,
            "kept_rows": 0,
            "removed_rows": 0,
            "targets": selected_targets,
            "tiers": sorted(selected_tiers),
            "require_sample_level": require_sample_level,
        }
        record_pipeline_run("33_filter_strong_relevance_rows", "ok", stats, db_path=db_path)
        return stats

    mask = pd.Series(True, index=df.index)
    if "target_property" in df:
        mask &= df["target_property"].astype(str).isin(selected_targets)
    if "benchmark_tier" in df:
        mask &= df["benchmark_tier"].astype(str).isin(selected_tiers)
    if "model_include" in df:
        mask &= pd.to_numeric(df["model_include"], errors="coerce").fillna(0).astype(int).eq(1)
    if "page_number" in df:
        mask &= df["page_number"].notna() & df["page_number"].astype(str).ne("")
    if "paper_id" in df:
        mask &= df["paper_id"].notna() & df["paper_id"].astype(str).ne("")
    if "evidence_text" in df:
        mask &= df["evidence_text"].fillna("").astype(str).str.strip().ne("")
    if "ai_review_status" in df:
        mask &= ~df["ai_review_status"].fillna("").astype(str).isin(REJECT_AI_STATUSES)
    if require_sample_level and "source" in df:
        mask &= df["source"].astype(str).eq("sample_property_links")

    kept = df[mask].copy()
    removed = df[~mask].copy()
    if "usable_for_model" in kept and "ai_review_status" in kept:
        audited = kept["ai_review_status"].fillna("").astype(str).ne("")
        usable = _truthy_series(kept["usable_for_model"])
        kept = kept[(~audited) | usable].copy()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    kept.to_csv(out_path, index=False, encoding="utf-8-sig")

    target_counts = (
        kept["target_property"].value_counts().to_dict() if "target_property" in kept else {}
    )
    removed_reasons = {
        "non_primary_or_non_selected_target": int(
            (~df.get("target_property", pd.Series("", index=df.index)).astype(str).isin(selected_targets)).sum()
        ),
        "not_selected_tier": int(
            (~df.get("benchmark_tier", pd.Series("", index=df.index)).astype(str).isin(selected_tiers)).sum()
        ),
        "not_model_included": int(
            (~pd.to_numeric(df.get("model_include", pd.Series(0, index=df.index)), errors="coerce").fillna(0).astype(int).eq(1)).sum()
        ),
        "not_sample_level": int(
            (df.get("source", pd.Series("", index=df.index)).astype(str).ne("sample_property_links")).sum()
        )
        if require_sample_level
        else 0,
        "weak_or_missing_evidence": int(len(removed)),
    }
    stats = {
        "source_path": str(source_path),
        "output_path": str(out_path),
        "input_rows": int(len(df)),
        "kept_rows": int(len(kept)),
        "removed_rows": int(len(df) - len(kept)),
        "target_counts": {str(k): int(v) for k, v in target_counts.items()},
        "removed_reasons": removed_reasons,
        "targets": selected_targets,
        "tiers": sorted(selected_tiers),
        "require_sample_level": require_sample_level,
        "screening_note": "Non-destructive filter using sample-level links, benchmark tiers, evidence traceability, normalized model_include, and existing AI audit fields.",
    }
    record_pipeline_run("33_filter_strong_relevance_rows", "ok", stats, db_path=db_path)
    return stats


def export_strong_relevance_extraction_queue(
    strong_dataset_path: Path | None = None,
    output_dir: Path | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Export paper/chunk queues for scoped full re-extraction."""

    dataset_path = strong_dataset_path or PROJECT_ROOT / "data" / "design" / "hfo2_design_dataset_strong_relevant.csv"
    if not dataset_path.exists():
        filter_strong_relevance_rows(output_path=dataset_path, db_path=db_path)
    df = pd.read_csv(dataset_path) if dataset_path.exists() else pd.DataFrame()
    out_dir = output_dir or PROJECT_ROOT / "data" / "extraction_queues"
    out_dir.mkdir(parents=True, exist_ok=True)
    paper_path = out_dir / "strong_relevant_papers.csv"
    chunk_path = out_dir / "strong_relevant_chunks.csv"
    summary_path = out_dir / "strong_relevant_extraction_queue.json"

    if df.empty:
        paper_ids: list[str] = []
        chunk_ids: list[str] = []
    else:
        paper_ids = sorted({str(value) for value in df.get("paper_id", []) if str(value) and str(value) != "nan"})
        chunk_ids = sorted({str(value) for value in df.get("chunk_id", []) if str(value) and str(value) != "nan"})

    paper_path.write_text("paper_id\n" + "\n".join(paper_ids) + ("\n" if paper_ids else ""), encoding="utf-8")
    chunk_path.write_text("chunk_id\n" + "\n".join(chunk_ids) + ("\n" if chunk_ids else ""), encoding="utf-8")
    stats = {
        "dataset_path": str(dataset_path),
        "paper_list_path": str(paper_path),
        "chunk_list_path": str(chunk_path),
        "papers": len(paper_ids),
        "chunks": len(chunk_ids),
        "note": "Use --paper-list for full high-value chunk re-extraction across selected strong-relevant papers, or --chunk-list for only currently linked chunks.",
    }
    summary_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    stats["summary_path"] = str(summary_path)
    record_pipeline_run("33_export_strong_relevance_extraction_queue", "ok", stats, db_path=db_path)
    return stats


def _make_preprocessor():
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder

    numeric_pipe = Pipeline(steps=[("imputer", SimpleImputer(strategy="median"))])
    categorical_pipe = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore", min_frequency=2)),
        ]
    )
    return ColumnTransformer(
        transformers=[
            ("num", numeric_pipe, NUMERIC_FEATURES),
            ("cat", categorical_pipe, CATEGORICAL_FEATURES),
        ]
    )


def _model_specs(random_state: int) -> dict[str, Any]:
    from sklearn.ensemble import ExtraTreesRegressor, GradientBoostingRegressor, RandomForestRegressor
    from sklearn.linear_model import ElasticNet, Ridge
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.svm import SVR

    preprocessor = _make_preprocessor
    return {
        "RandomForest": lambda: Pipeline(
            [
                ("features", preprocessor()),
                (
                    "model",
                    RandomForestRegressor(
                        n_estimators=250,
                        min_samples_leaf=2,
                        random_state=random_state,
                        n_jobs=-1,
                    ),
                ),
            ]
        ),
        "ExtraTrees": lambda: Pipeline(
            [
                ("features", preprocessor()),
                (
                    "model",
                    ExtraTreesRegressor(
                        n_estimators=250,
                        min_samples_leaf=2,
                        random_state=random_state,
                        n_jobs=-1,
                    ),
                ),
            ]
        ),
        "GradientBoosting": lambda: Pipeline(
            [
                ("features", preprocessor()),
                (
                    "model",
                    GradientBoostingRegressor(
                        n_estimators=180,
                        learning_rate=0.05,
                        max_depth=3,
                        random_state=random_state,
                    ),
                ),
            ]
        ),
        "Ridge": lambda: Pipeline(
            [
                ("features", preprocessor()),
                ("scale", StandardScaler(with_mean=False)),
                ("model", Ridge(alpha=1.0, random_state=random_state)),
            ]
        ),
        "ElasticNet": lambda: Pipeline(
            [
                ("features", preprocessor()),
                ("scale", StandardScaler(with_mean=False)),
                ("model", ElasticNet(alpha=0.05, l1_ratio=0.2, random_state=random_state, max_iter=5000)),
            ]
        ),
        "SVR_rbf": lambda: Pipeline(
            [
                ("features", preprocessor()),
                ("scale", StandardScaler(with_mean=False)),
                ("model", SVR(C=10.0, epsilon=1.0, gamma="scale")),
            ]
        ),
    }


def compare_regression_models(
    dataset_path: Path,
    output_dir: Path | None = None,
    targets: list[str] | None = None,
    min_rows: int = 30,
    test_size: float = 0.25,
    random_state: int = 42,
    db_path: Path | None = None,
) -> dict[str, Any]:
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
    from sklearn.model_selection import GroupShuffleSplit, train_test_split

    df = pd.read_csv(dataset_path) if dataset_path.exists() else pd.DataFrame()
    selected_targets = targets or PRIMARY_TARGETS
    out_dir = output_dir or PROJECT_ROOT / "models" / "model_comparison"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    specs = _model_specs(random_state=random_state)

    for target_property in selected_targets:
        subset = _prepare_target_frame(df, target_property)
        row_count = len(subset)
        if row_count < min_rows:
            rows.append(
                {
                    "target_property": target_property,
                    "model": "all",
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
        groups = _paper_groups(subset)
        if groups.nunique() >= 2:
            splitter = GroupShuffleSplit(n_splits=1, test_size=test_size, random_state=random_state)
            train_index, test_index = next(splitter.split(X, y, groups))
            X_train, X_test = X.iloc[train_index], X.iloc[test_index]
            y_train, y_test = y.iloc[train_index], y.iloc[test_index]
            train_groups = groups.iloc[train_index]
            test_groups = groups.iloc[test_index]
            split_name = "paper_group_holdout"
        else:
            X_train, X_test, y_train, y_test = train_test_split(
                X,
                y,
                test_size=holdout,
                random_state=random_state,
            )
            train_groups = groups.loc[X_train.index]
            test_groups = groups.loc[X_test.index]
            split_name = "row_holdout_fallback"
        baseline_pred = [float(y_train.mean())] * len(y_test)
        baseline_mae = float(mean_absolute_error(y_test, baseline_pred))
        for model_name, factory in specs.items():
            pipeline = factory()
            pipeline.fit(X_train, y_train)
            predictions = pipeline.predict(X_test)
            absolute_error = (pd.Series(predictions, index=y_test.index) - y_test).abs()
            relative_error = absolute_error / y_test.abs().clip(lower=1e-9)
            mae = float(mean_absolute_error(y_test, predictions))
            rmse = float(mean_squared_error(y_test, predictions) ** 0.5)
            r2 = float(r2_score(y_test, predictions)) if len(y_test) > 1 else None
            rows.append(
                {
                    "target_property": target_property,
                    "model": model_name,
                    "status": "trained",
                    "rows": row_count,
                    "train_rows": int(len(X_train)),
                    "validation_rows": int(len(X_test)),
                    "split": split_name,
                    "train_groups": int(train_groups.nunique()),
                    "validation_groups": int(test_groups.nunique()),
                    "group_overlap": int(len(set(train_groups) & set(test_groups))),
                    "mae": mae,
                    "rmse": rmse,
                    "r2": r2,
                    "baseline_mae": baseline_mae,
                    "improvement_vs_baseline": baseline_mae - mae,
                    "within_5_uC_cm2_accuracy": float((absolute_error <= 5.0).mean()),
                    "within_10_uC_cm2_accuracy": float((absolute_error <= 10.0).mean()),
                    "within_10pct_accuracy": float((relative_error <= 0.10).mean()),
                }
            )

    metrics_csv = out_dir / "strong_relevant_model_comparison.csv"
    fieldnames = [
        "target_property",
        "model",
        "status",
        "rows",
        "train_rows",
        "validation_rows",
        "split",
        "train_groups",
        "validation_groups",
        "group_overlap",
        "mae",
        "rmse",
        "r2",
        "baseline_mae",
        "improvement_vs_baseline",
        "within_5_uC_cm2_accuracy",
        "within_10_uC_cm2_accuracy",
        "within_10pct_accuracy",
        "min_rows",
    ]
    with metrics_csv.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})

    trained_rows = [row for row in rows if row.get("status") == "trained"]
    best_by_target: dict[str, dict[str, Any]] = {}
    for target_property in selected_targets:
        candidates = [row for row in trained_rows if row["target_property"] == target_property]
        if candidates:
            best_by_target[target_property] = min(candidates, key=lambda item: item["mae"])

    report_md = out_dir / "strong_relevant_model_comparison.md"
    report_md.write_text(
        _render_model_report(dataset_path, rows, best_by_target),
        encoding="utf-8",
    )
    payload = {
        "dataset_path": str(dataset_path),
        "output_dir": str(out_dir),
        "metrics_csv": str(metrics_csv),
        "report_md": str(report_md),
        "targets": selected_targets,
        "models": sorted(specs.keys()),
        "results": rows,
        "best_by_target": best_by_target,
    }
    metrics_json = out_dir / "strong_relevant_model_comparison.json"
    metrics_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["metrics_json"] = str(metrics_json)
    record_pipeline_run("33_compare_regression_models", "ok", payload, db_path=db_path)
    return payload


def cross_validate_regression_models(
    dataset_path: Path,
    output_dir: Path | None = None,
    targets: list[str] | None = None,
    min_rows: int = 30,
    folds: int = 5,
    random_state: int = 42,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Run K-fold stability checks on the same strong-relevance modeling slice.

    The publication-facing score should still be the held-out validation split
    from compare_regression_models. Cross-validation here estimates metric
    stability and flags models whose apparent performance depends on a lucky
    split.
    """
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
    from sklearn.model_selection import GroupKFold, KFold

    df = pd.read_csv(dataset_path) if dataset_path.exists() else pd.DataFrame()
    selected_targets = targets or PRIMARY_TARGETS
    out_dir = output_dir or PROJECT_ROOT / "models" / "model_cross_validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = _model_specs(random_state=random_state)
    fold_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

    for target_property in selected_targets:
        subset = _prepare_target_frame(df, target_property)
        row_count = len(subset)
        if row_count < min_rows or row_count < 3:
            summary_rows.append(
                {
                    "target_property": target_property,
                    "model": "all",
                    "status": "skipped_not_enough_rows",
                    "rows": row_count,
                    "min_rows": min_rows,
                    "folds": 0,
                }
            )
            continue

        n_splits = min(max(2, folds), row_count)
        X = subset[NUMERIC_FEATURES + CATEGORICAL_FEATURES]
        y = subset["target_value"]
        groups = _paper_groups(subset)
        use_group_split = groups.nunique() >= n_splits
        splitter = (
            GroupKFold(n_splits=n_splits)
            if use_group_split
            else KFold(n_splits=n_splits, shuffle=True, random_state=random_state)
        )

        for model_name, factory in specs.items():
            per_fold: list[dict[str, Any]] = []
            split_iter = splitter.split(X, y, groups) if use_group_split else splitter.split(X)
            for fold_index, (train_index, test_index) in enumerate(split_iter, start=1):
                X_train, X_test = X.iloc[train_index], X.iloc[test_index]
                y_train, y_test = y.iloc[train_index], y.iloc[test_index]
                pipeline = factory()
                pipeline.fit(X_train, y_train)
                predictions = pipeline.predict(X_test)
                absolute_error = (pd.Series(predictions, index=y_test.index) - y_test).abs()
                relative_error = absolute_error / y_test.abs().clip(lower=1e-9)
                baseline_pred = [float(y_train.mean())] * len(y_test)
                baseline_mae = float(mean_absolute_error(y_test, baseline_pred))
                mae = float(mean_absolute_error(y_test, predictions))
                rmse = float(mean_squared_error(y_test, predictions) ** 0.5)
                r2 = float(r2_score(y_test, predictions)) if len(y_test) > 1 else None
                row = {
                    "target_property": target_property,
                    "model": model_name,
                    "status": "trained",
                    "rows": row_count,
                    "fold": fold_index,
                    "folds": n_splits,
                    "train_rows": int(len(X_train)),
                    "validation_rows": int(len(X_test)),
                    "split": "paper_group_kfold" if use_group_split else "row_kfold_fallback",
                    "train_groups": int(groups.iloc[train_index].nunique()),
                    "validation_groups": int(groups.iloc[test_index].nunique()),
                    "group_overlap": int(
                        len(set(groups.iloc[train_index]) & set(groups.iloc[test_index]))
                    ),
                    "mae": mae,
                    "rmse": rmse,
                    "r2": r2,
                    "baseline_mae": baseline_mae,
                    "improvement_vs_baseline": baseline_mae - mae,
                    "within_5_uC_cm2_accuracy": float((absolute_error <= 5.0).mean()),
                    "within_10_uC_cm2_accuracy": float((absolute_error <= 10.0).mean()),
                    "within_10pct_accuracy": float((relative_error <= 0.10).mean()),
                }
                fold_rows.append(row)
                per_fold.append(row)

            summary = _summarize_cv_rows(target_property, model_name, per_fold)
            summary_rows.append(summary)

    fold_csv = out_dir / "cross_validation_folds.csv"
    summary_csv = out_dir / "cross_validation_summary.csv"
    fieldnames = [
        "target_property",
        "model",
        "status",
        "rows",
        "fold",
        "folds",
        "train_rows",
        "validation_rows",
        "split",
        "train_groups",
        "validation_groups",
        "group_overlap",
        "mae",
        "rmse",
        "r2",
        "baseline_mae",
        "improvement_vs_baseline",
        "within_5_uC_cm2_accuracy",
        "within_10_uC_cm2_accuracy",
        "within_10pct_accuracy",
        "min_rows",
    ]
    with fold_csv.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in fold_rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})

    summary_fields = [
        "target_property",
        "model",
        "status",
        "rows",
        "folds",
        "split",
        "group_overlap_max",
        "mae_mean",
        "mae_std",
        "rmse_mean",
        "rmse_std",
        "r2_mean",
        "r2_std",
        "baseline_mae_mean",
        "improvement_vs_baseline_mean",
        "within_5_uC_cm2_accuracy_mean",
        "within_10_uC_cm2_accuracy_mean",
        "within_10pct_accuracy_mean",
        "min_rows",
    ]
    with summary_csv.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=summary_fields)
        writer.writeheader()
        for row in summary_rows:
            writer.writerow({field: row.get(field, "") for field in summary_fields})

    summary_json = out_dir / "cross_validation_summary.json"
    report_md = out_dir / "cross_validation_report.md"
    payload = {
        "dataset_path": str(dataset_path),
        "output_dir": str(out_dir),
        "folds_requested": folds,
        "random_state": random_state,
        "targets": selected_targets,
        "models": sorted(specs.keys()),
        "fold_metrics_csv": str(fold_csv),
        "summary_csv": str(summary_csv),
        "summary": summary_rows,
    }
    summary_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    report_md.write_text(_render_cross_validation_report(dataset_path, summary_rows), encoding="utf-8")
    payload["summary_json"] = str(summary_json)
    payload["report_md"] = str(report_md)
    record_pipeline_run("33_cross_validate_regression_models", "ok", payload, db_path=db_path)
    return payload


def _summarize_cv_rows(target_property: str, model_name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "target_property": target_property,
            "model": model_name,
            "status": "skipped",
            "rows": 0,
            "folds": 0,
        }

    frame = pd.DataFrame(rows)

    def mean(column: str) -> float | None:
        values = pd.to_numeric(frame.get(column), errors="coerce").dropna()
        return float(values.mean()) if not values.empty else None

    def std(column: str) -> float | None:
        values = pd.to_numeric(frame.get(column), errors="coerce").dropna()
        return float(values.std(ddof=0)) if not values.empty else None

    return {
        "target_property": target_property,
        "model": model_name,
        "status": "trained",
        "rows": int(frame["rows"].iloc[0]),
        "folds": int(frame["folds"].iloc[0]),
        "split": str(frame["split"].iloc[0]),
        "group_overlap_max": int(pd.to_numeric(frame["group_overlap"], errors="coerce").fillna(0).max()),
        "mae_mean": mean("mae"),
        "mae_std": std("mae"),
        "rmse_mean": mean("rmse"),
        "rmse_std": std("rmse"),
        "r2_mean": mean("r2"),
        "r2_std": std("r2"),
        "baseline_mae_mean": mean("baseline_mae"),
        "improvement_vs_baseline_mean": mean("improvement_vs_baseline"),
        "within_5_uC_cm2_accuracy_mean": mean("within_5_uC_cm2_accuracy"),
        "within_10_uC_cm2_accuracy_mean": mean("within_10_uC_cm2_accuracy"),
        "within_10pct_accuracy_mean": mean("within_10pct_accuracy"),
    }


def _render_cross_validation_report(dataset_path: Path, rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Cross-Validation Stability Report",
        "",
        f"- Dataset: `{dataset_path}`",
        "- Purpose: estimate out-of-paper stability with paper-grouped folds whenever paper identifiers are available.",
        "- Accuracy-style values are tolerance hit rates on each validation fold.",
        "",
        "| Target | Model | Status | Rows | Folds | MAE mean | MAE std | RMSE mean | R2 mean | Within 10 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        if row.get("status") != "trained":
            lines.append(
                f"| {row.get('target_property')} | {row.get('model')} | {row.get('status')} | {row.get('rows')} | {row.get('folds', 0)} |  |  |  |  |  |"
            )
            continue
        lines.append(
            "| {target} | {model} | trained | {rows} | {folds} | {mae:.3f} | {mae_std:.3f} | {rmse:.3f} | {r2:.3f} | {within10:.2%} |".format(
                target=row["target_property"],
                model=row["model"],
                rows=int(row["rows"]),
                folds=int(row["folds"]),
                mae=float(row["mae_mean"]) if row.get("mae_mean") is not None else float("nan"),
                mae_std=float(row["mae_std"]) if row.get("mae_std") is not None else float("nan"),
                rmse=float(row["rmse_mean"]) if row.get("rmse_mean") is not None else float("nan"),
                r2=float(row["r2_mean"]) if row.get("r2_mean") is not None else float("nan"),
                within10=float(row["within_10_uC_cm2_accuracy_mean"])
                if row.get("within_10_uC_cm2_accuracy_mean") is not None
                else 0.0,
            )
        )
    lines.append("")
    return "\n".join(lines)


def _render_model_report(
    dataset_path: Path,
    rows: list[dict[str, Any]],
    best_by_target: dict[str, dict[str, Any]],
) -> str:
    lines = [
        "# Strong-Relevant HfO2/HZO Model Comparison",
        "",
        f"- Dataset: `{dataset_path}`",
        "- Screening: strong sample-level rows with evidence traceability, valid normalized targets, and non-rejected AI audit status.",
        "- Accuracy is reported as regression tolerance hit rate, not classification accuracy.",
        "",
        "## Best Models",
        "",
        "| Target | Best model | Rows | MAE | RMSE | R2 | Within 5 | Within 10 | Within 10% |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for target, row in best_by_target.items():
        lines.append(
            "| {target} | {model} | {rows} | {mae:.3f} | {rmse:.3f} | {r2:.3f} | {w5:.2%} | {w10:.2%} | {w10p:.2%} |".format(
                target=target,
                model=row["model"],
                rows=int(row["rows"]),
                mae=float(row["mae"]),
                rmse=float(row["rmse"]),
                r2=float(row["r2"]) if row.get("r2") is not None else float("nan"),
                w5=float(row["within_5_uC_cm2_accuracy"]),
                w10=float(row["within_10_uC_cm2_accuracy"]),
                w10p=float(row["within_10pct_accuracy"]),
            )
        )
    lines.extend(
        [
            "",
            "## Full Results",
            "",
            "| Target | Model | Status | Rows | MAE | RMSE | R2 | Baseline MAE | Improvement | Within 5 | Within 10 | Within 10% |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in rows:
        if row.get("status") != "trained":
            lines.append(
                f"| {row.get('target_property')} | {row.get('model')} | {row.get('status')} | {row.get('rows')} |  |  |  |  |  |  |  |  |"
            )
            continue
        lines.append(
            "| {target} | {model} | trained | {rows} | {mae:.3f} | {rmse:.3f} | {r2:.3f} | {baseline:.3f} | {improve:.3f} | {w5:.2%} | {w10:.2%} | {w10p:.2%} |".format(
                target=row["target_property"],
                model=row["model"],
                rows=int(row["rows"]),
                mae=float(row["mae"]),
                rmse=float(row["rmse"]),
                r2=float(row["r2"]) if row.get("r2") is not None else float("nan"),
                baseline=float(row["baseline_mae"]),
                improve=float(row["improvement_vs_baseline"]),
                w5=float(row["within_5_uC_cm2_accuracy"]),
                w10=float(row["within_10_uC_cm2_accuracy"]),
                w10p=float(row["within_10pct_accuracy"]),
            )
        )
    lines.append("")
    return "\n".join(lines)
