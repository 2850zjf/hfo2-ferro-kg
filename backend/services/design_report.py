from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.design_model import load_design_model_metrics
from backend.services.pipeline_log import record_pipeline_run
from backend.services.progress_monitor import database_counts, token_usage


def _table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_暂无数据_"
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join(["---"] * len(columns)) + " |"
    body = []
    for row in rows:
        body.append("| " + " | ".join(str(row.get(col, "")).replace("\n", " ") for col in columns) + " |")
    return "\n".join([header, sep, *body])


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def _db_count(sql: str, db_path: Path | None = None) -> int:
    with connect(db_path) as conn:
        try:
            return int(conn.execute(sql).fetchone()[0])
        except Exception:
            return 0


def export_design_progress_report(
    output_path: Path | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    target = output_path or PROJECT_ROOT / "data" / "exports" / "hfo2_design_progress_report.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    counts = database_counts(db_path=db_path)
    usage = token_usage(db_path=db_path)
    dataset_path = PROJECT_ROOT / "data" / "design" / "hfo2_design_dataset.csv"
    candidates_path = PROJECT_ROOT / "data" / "design" / "active_learning_candidates.csv"
    tiers_path = PROJECT_ROOT / "data" / "design" / "benchmark_tiers.json"
    dataset = _load_csv(dataset_path)
    candidates = _load_csv(candidates_path)
    metrics = load_design_model_metrics()
    tier_stats = json.loads(tiers_path.read_text(encoding="utf-8")) if tiers_path.exists() else {}

    material_counts: list[dict[str, Any]] = []
    target_counts: list[dict[str, Any]] = []
    if not dataset.empty:
        if "material_family" in dataset:
            material_counts = (
                dataset["material_family"].fillna("unknown").astype(str).value_counts().head(12).reset_index().to_dict("records")
            )
            material_counts = [{"material_family": row["material_family"], "count": row["count"]} for row in material_counts]
        if "target_property" in dataset:
            target_counts = (
                dataset["target_property"].fillna("unknown").astype(str).value_counts().reset_index().to_dict("records")
            )
            target_counts = [{"target_property": row["target_property"], "count": row["count"]} for row in target_counts]

    metrics_rows: list[dict[str, Any]] = []
    if metrics:
        for item in metrics.get("targets", []):
            metrics_rows.append(
                {
                    "target_property": item.get("target_property"),
                    "status": item.get("status"),
                    "rows": item.get("rows"),
                    "MAE": round(float(item.get("mae", 0)), 4) if item.get("mae") is not None else "",
                    "RMSE": round(float(item.get("rmse", 0)), 4) if item.get("rmse") is not None else "",
                    "R2": round(float(item.get("r2", 0)), 4) if item.get("r2") is not None else "",
                }
            )

    candidate_rows: list[dict[str, Any]] = []
    if not candidates.empty:
        for _, row in candidates.head(12).iterrows():
            candidate_rows.append(
                {
                    "candidate_id": row.get("candidate_id", ""),
                    "target_property": row.get("target_property", ""),
                    "predicted_value": round(float(row.get("predicted_value", 0)), 3)
                    if pd.notna(row.get("predicted_value", None))
                    else "",
                    "uncertainty": round(float(row.get("uncertainty", 0)), 3)
                    if pd.notna(row.get("uncertainty", None))
                    else "",
                    "material": row.get("material_name", ""),
                    "anneal_C": row.get("annealing_temperature_c", ""),
                    "stack": row.get("electrode_stack", ""),
                }
            )

    audit_counts = {
        "usable_for_model": _db_count("SELECT COUNT(*) FROM ai_fact_audits WHERE ai_review_status='usable_for_model'", db_path),
        "usable_for_rag_only": _db_count("SELECT COUNT(*) FROM ai_fact_audits WHERE ai_review_status='usable_for_rag_only'", db_path),
        "needs_human_review": _db_count("SELECT COUNT(*) FROM ai_fact_audits WHERE ai_review_status='needs_human_review'", db_path),
        "reject": _db_count("SELECT COUNT(*) FROM ai_fact_audits WHERE ai_review_status='reject'", db_path),
    }
    context_counter = Counter()
    if not dataset.empty and "benchmark_tier" in dataset:
        context_counter.update(dataset["benchmark_tier"].dropna().astype(str))

    lines = [
        "# HfO2-FerroKG 进展报告",
        "",
        "## 当前定位",
        "本项目正在从文献抽取工具升级为证据推理、设计图谱、benchmark 数据集和主动学习一体化的 HfO2 基铁电材料设计工作台。",
        "",
        "## 数据规模",
        _table(
            [
                {"指标": "PDF 记录", "数量": counts.get("pdf_files", 0)},
                {"指标": "Document chunks", "数量": counts.get("document_chunks", 0)},
                {"指标": "审核事实", "数量": counts.get("reviewed_facts", 0)},
                {"指标": "Benchmark 抽取结果", "数量": counts.get("benchmark_ok", 0)},
                {"指标": "样品级关联", "数量": counts.get("sample_property_links", 0)},
                {"指标": "AI 二次审核", "数量": sum(audit_counts.values())},
            ],
            ["指标", "数量"],
        ),
        "",
        "## LLM 用量估计",
        _table(
            [
                {"项目": "实际 token", "值": usage.get("actual_total_tokens", 0)},
                {"项目": "估算总 token", "值": usage.get("estimated_total_tokens", 0)},
                {"项目": "估算费用", "值": f"{usage.get('estimated_cost', 0)} {usage.get('currency', 'CNY')}"},
            ],
            ["项目", "值"],
        ),
        "",
        "## Benchmark 分层",
        _table(
            [
                {"层级": name, "行数": info.get("rows", 0), "文件": info.get("path", "")}
                for name, info in (tier_stats.get("tiers") or {}).items()
            ],
            ["层级", "行数", "文件"],
        ),
        "",
        "## AI 二次审核状态",
        _table([{"状态": key, "数量": value} for key, value in audit_counts.items()], ["状态", "数量"]),
        "",
        "## 材料体系覆盖",
        _table(material_counts, ["material_family", "count"]),
        "",
        "## 目标性能覆盖",
        _table(target_counts, ["target_property", "count"]),
        "",
        "## Baseline 模型指标",
        _table(metrics_rows, ["target_property", "status", "rows", "MAE", "RMSE", "R2"]),
        "",
        "## 主动学习候选预览",
        _table(candidate_rows, ["candidate_id", "target_property", "predicted_value", "uncertainty", "material", "anneal_C", "stack"]),
        "",
        "## 下一步",
        "1. 完成样品级关联，并重建设计图谱和 benchmark 分层。",
        "2. 用 LLM 二次审核 partial/weak 事实，提升 strong_only 数据质量。",
        "3. RAG 回答优先使用样品级事实和设计图谱，并强制返回 fact_id、页码、证据句和样品条件。",
        "4. 用 strong_only 与 strong_partial 训练模型对比，主动学习候选只推荐高性能、高不确定性、可实验实现的组合。",
    ]
    target.write_text("\n".join(lines), encoding="utf-8")
    stats = {
        "output_path": str(target),
        "dataset_rows": int(len(dataset)),
        "candidate_rows": int(len(candidates)),
        "ai_audits": sum(audit_counts.values()),
    }
    record_pipeline_run("31_export_design_report", "ok", stats, db_path=db_path)
    return stats
