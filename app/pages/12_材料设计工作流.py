from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

PROJECT_ROOT_LOCAL = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT_LOCAL) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_LOCAL))

from backend.core.config import PROJECT_ROOT
from backend.services.active_learning import recommend_active_learning_candidates
from backend.services.design_dataset import build_benchmark_tier_datasets, build_design_dataset
from backend.services.design_graph import build_design_graph
from backend.services.design_model import load_design_model_metrics, train_design_models, train_tiered_design_models
from backend.services.design_report import export_design_progress_report
from backend.services.llm_quota_guard import clear_llm_pause, read_llm_pause
from backend.services.progress_monitor import active_pipeline_processes, monitor_snapshot
from backend.services.workflow_runner import start_monitor_server, start_protected_full_pipeline


st.set_page_config(page_title="材料设计工作流", layout="wide")


def _run_background(command: list[str], prefix: str) -> dict[str, Any]:
    active = active_pipeline_processes()
    script_name = Path(command[1]).name if len(command) > 1 else ""
    if any(str(item.get("task", "")).endswith(script_name) for item in active):
        return {"started": False, "reason": "同类任务已经在后台运行"}
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_log = log_dir / f"{prefix}_{stamp}.out.log"
    err_log = log_dir / f"{prefix}_{stamp}.err.log"
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        stdout=out_log.open("w", encoding="utf-8"),
        stderr=err_log.open("w", encoding="utf-8"),
        creationflags=flags,
    )
    return {
        "started": True,
        "pid": process.pid,
        "stdout_log": str(out_log),
        "stderr_log": str(err_log),
    }


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def _metric(label: str, value: Any, help_text: str = "") -> None:
    st.metric(label, value if value not in (None, "") else "0", help=help_text or None)


def _start_pipeline_button(
    label: str,
    script: str,
    prefix: str,
    args: list[str] | None = None,
    primary: bool = False,
) -> None:
    if st.button(label, type="primary" if primary else "secondary", use_container_width=True):
        command = [sys.executable, script, *(args or [])]
        result = _run_background(command, prefix)
        if result.get("started"):
            st.success(f"已启动：PID {result['pid']}")
            st.caption(f"日志：{result['stdout_log']}")
        else:
            st.warning(result.get("reason", "没有启动"))


snapshot = monitor_snapshot()
counts = snapshot.get("counts", {})
runtime = snapshot.get("runtime", {})
tokens = snapshot.get("tokens", {})
pause = read_llm_pause()

st.title("材料设计工作流")
st.caption(
    "目标：从 PDF 证据出发，形成样品级事实、证据推理 RAG、设计图谱、benchmark 数据集、baseline 模型和主动学习候选。"
)

if pause:
    st.error("LLM 抽取已暂停。通常是额度、欠费、限流或 key 状态问题。已完成的数据不会丢。")
    with st.expander("暂停详情", expanded=False):
        st.write(pause.get("reason", ""))
        st.write(pause.get("resume_hint", "恢复额度或配置新 key 后，重新运行同一条 pipeline 即可断点继续。"))
    if st.button("我已恢复 API，清除暂停标记"):
        clear_llm_pause()
        st.success("已清除暂停标记。")

if snapshot.get("active"):
    st.info(
        f"后台正在运行：{runtime.get('active_step', '处理中')} | "
        f"总运行 {runtime.get('elapsed', '估算中')} | "
        f"当前步骤 {runtime.get('active_elapsed', '估算中')} | "
        f"预计剩余 {runtime.get('eta', '估算中')} | "
        f"速度 {runtime.get('rate_per_min', 0)} 条/分钟"
    )
else:
    st.success("当前没有检测到全量后台任务。可以查看结果，或启动下一步。")

top_cols = st.columns(8)
with top_cols[0]:
    _metric("PDF", counts.get("pdf_files", 0))
with top_cols[1]:
    _metric("chunk", counts.get("document_chunks", 0))
with top_cols[2]:
    _metric("审核事实", counts.get("reviewed_facts", 0))
with top_cols[3]:
    _metric("Benchmark", counts.get("benchmark_ok", 0))
with top_cols[4]:
    _metric("样品级关联", counts.get("sample_property_links", 0))
with top_cols[5]:
    _metric("文献卡片", counts.get("literature_cards", 0))
with top_cols[6]:
    _metric("AI 审核", counts.get("ai_fact_audits", 0))
with top_cols[7]:
    _metric("估算费用", f"{tokens.get('estimated_cost', 0)} {tokens.get('currency', 'CNY')}")

st.divider()

st.subheader("我们要解决的问题")
problem_cols = st.columns(4)
cards = [
    ("样品条件缺失", "只有材料名和 Pr/2Pr 不够。性能值必须绑定厚度、退火、电极、沉积方法、器件、相结构和状态。"),
    ("证据不可追溯", "每个结论都要能回到 DOI、论文标题、页码、chunk 和原文证据句。"),
    ("图谱还不是设计图谱", "设计图谱要区分可控变量、目标变量、约束变量、机制变量和证据。"),
    ("模型需要闭环", "baseline 只是起点，主动学习要推荐高性能、高不确定性、可实验实现的候选。"),
]
for col, (title, body) in zip(problem_cols, cards):
    with col:
        st.markdown(f"**{title}**")
        st.write(body)

st.subheader("完整工作流")
workflow = pd.DataFrame(
    [
        {"阶段": "1 文献解析", "目标": "PDF、表格、图注、正文 chunk", "产物": "parsed_pages / chunks / tables"},
        {"阶段": "2 LLM 文献卡片", "目标": "论文类型、材料体系、核心样品、关键图表", "产物": "llm_literature_cards"},
        {"阶段": "3 LLM chunk 语义分层", "目标": "方法、结果、机理、综述引用、理论计算", "产物": "llm_chunk_labels"},
        {"阶段": "4 全量事实抽取", "目标": "材料、样品、工艺、相、性能、机制、设计规则", "产物": "extraction_candidates / benchmark_extractions"},
        {"阶段": "5 样品级关联", "目标": "同一性能值绑定材料、厚度、退火、电极、相结构", "产物": "sample_property_links"},
        {"阶段": "6 LLM 二次审核", "目标": "排查 Pr/2Pr、单位、综述二手值、样品错配", "产物": "ai_fact_audits"},
        {"阶段": "7 设计图谱", "目标": "可控变量、目标变量、约束变量、机制变量、证据", "产物": "design_graph CSV + HTML"},
        {"阶段": "8 Benchmark 分层", "目标": "strong_only / strong_partial / all_traceable", "产物": "tiered benchmark CSV"},
        {"阶段": "9 模型与主动学习", "目标": "baseline 对比，并推荐候选工艺组合", "产物": "metrics / active_learning_candidates"},
    ]
)
st.dataframe(workflow, use_container_width=True, hide_index=True)

st.divider()

st.subheader("执行控制")
st.caption("后台任务会断点续跑；如果 token 或网络出问题，会先暂停，数据保留。")
row1 = st.columns(4)
with row1[0]:
    if st.button("启动实时监控", use_container_width=True):
        result = start_monitor_server()
        st.success(f"实时监控已启动：PID {result['pid']}")
        st.link_button("打开实时监控", result["url"], use_container_width=True)
with row1[1]:
    if st.button("启动完整流水线", type="primary", use_container_width=True):
        result = start_protected_full_pipeline(clear_pause=True)
        if result.get("started"):
            st.success(f"已启动完整流水线：PID {result['pid']}")
            st.link_button("打开实时监控", result["monitor_url"], use_container_width=True)
        else:
            st.warning(f"没有启动：{result.get('reason')}")
with row1[2]:
    _start_pipeline_button(
        "继续样品级关联",
        "pipelines/23_link_sample_facts.py",
        "sample_linking_page",
        ["--max-workers", "16", "--progress-every", "25", "--commit-every", "50", "--force-llm-when-paused"],
    )
with row1[3]:
    _start_pipeline_button(
        "AI 二次审核",
        "pipelines/28_ai_audit_sample_links.py",
        "ai_audits",
        ["--max-workers", "16", "--progress-every", "25", "--commit-every", "50", "--force-llm-when-paused"],
    )

row2 = st.columns(4)
with row2[0]:
    _start_pipeline_button(
        "生成 LLM 文献卡片",
        "pipelines/26_build_literature_cards.py",
        "literature_cards",
        ["--max-workers", "12", "--progress-every", "10", "--commit-every", "20", "--force-llm-when-paused"],
    )
with row2[1]:
    _start_pipeline_button(
        "LLM 标注 chunk",
        "pipelines/27_label_chunks_semantically.py",
        "chunk_labels",
        ["--max-workers", "16", "--progress-every", "25", "--commit-every", "50", "--force-llm-when-paused"],
    )
with row2[2]:
    if st.button("重建设计图谱", use_container_width=True):
        stats = build_design_graph()
        st.success(f"设计图谱：{stats['nodes']} 个节点，{stats['edges']} 条关系。")
with row2[3]:
    if st.button("构建分层 benchmark", use_container_width=True):
        stats = build_benchmark_tier_datasets()
        st.success("已生成 strong_only / strong_partial / all_traceable 三套数据。")
        st.json(stats)

row3 = st.columns(4)
with row3[0]:
    if st.button("构建数据集", use_container_width=True):
        stats = build_design_dataset()
        st.success(f"设计数据集：{stats['rows']} 行，其中可建模 {stats['model_rows']} 行。")
with row3[1]:
    if st.button("训练普通 baseline", use_container_width=True):
        stats = train_design_models(min_rows=12)
        st.success(f"训练完成：{stats['trained_models']} 个模型。")
with row3[2]:
    if st.button("训练分层模型", use_container_width=True):
        stats = train_tiered_design_models(min_rows=12)
        st.success("分层模型训练完成。")
        st.json({"metrics_path": stats.get("metrics_path")})
with row3[3]:
    if st.button("生成主动学习候选", use_container_width=True):
        stats = recommend_active_learning_candidates()
        st.success(f"已生成 {stats.get('candidates', 0)} 个候选。")

if st.button("导出进展报告", use_container_width=True):
    stats = export_design_progress_report()
    st.success(f"报告已导出：{stats['output_path']}")

st.divider()

dataset_path = PROJECT_ROOT / "data" / "design" / "hfo2_design_dataset.csv"
candidates_path = PROJECT_ROOT / "data" / "design" / "active_learning_candidates.csv"
design_graph_html = PROJECT_ROOT / "data" / "exports" / "hfo2_design_graph.html"
report_path = PROJECT_ROOT / "data" / "exports" / "hfo2_design_progress_report.md"
metrics = load_design_model_metrics()

st.subheader("当前产物")
artifact_cols = st.columns(4)
with artifact_cols[0]:
    if dataset_path.exists():
        st.download_button("下载设计数据集 CSV", dataset_path.read_bytes(), dataset_path.name, "text/csv", use_container_width=True)
    else:
        st.button("设计数据集未生成", disabled=True, use_container_width=True)
with artifact_cols[1]:
    if candidates_path.exists():
        st.download_button("下载主动学习候选 CSV", candidates_path.read_bytes(), candidates_path.name, "text/csv", use_container_width=True)
    else:
        st.button("主动学习候选未生成", disabled=True, use_container_width=True)
with artifact_cols[2]:
    if design_graph_html.exists():
        st.link_button("打开设计图谱 HTML", design_graph_html.resolve().as_uri(), use_container_width=True)
    else:
        st.button("设计图谱 HTML 未生成", disabled=True, use_container_width=True)
with artifact_cols[3]:
    if report_path.exists():
        st.download_button("下载进展报告", report_path.read_bytes(), report_path.name, "text/markdown", use_container_width=True)
    else:
        st.button("进展报告未生成", disabled=True, use_container_width=True)

df = _load_csv(dataset_path)
if not df.empty:
    st.subheader("设计数据集概览")
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("总行数", len(df))
    m2.metric("可建模行", int(pd.to_numeric(df.get("model_include", 0), errors="coerce").fillna(0).sum()))
    m3.metric("目标类型", df["target_property"].nunique() if "target_property" in df else 0)
    m4.metric("论文数", df["paper_id"].nunique() if "paper_id" in df else 0)
    m5.metric("材料体系", df["material_family"].nunique() if "material_family" in df else 0)

    if "target_property" in df:
        counts_df = df["target_property"].value_counts().reset_index()
        counts_df.columns = ["target_property", "count"]
        st.bar_chart(counts_df, x="target_property", y="count")
    if "benchmark_tier" in df:
        tier_df = df["benchmark_tier"].fillna("未分层").astype(str).value_counts().reset_index()
        tier_df.columns = ["benchmark_tier", "count"]
        st.bar_chart(tier_df, x="benchmark_tier", y="count")
    st.dataframe(df.head(300), use_container_width=True, hide_index=True)
else:
    st.info("还没有设计数据集。可以点击上面的“构建数据集”或“启动完整流水线”。")

if metrics:
    st.subheader("Baseline 模型指标")
    metrics_df = pd.DataFrame(metrics.get("targets", []))
    st.dataframe(metrics_df, use_container_width=True, hide_index=True)
    trained = metrics_df[metrics_df.get("status", "") == "trained"] if not metrics_df.empty else pd.DataFrame()
    if not trained.empty and "mae" in trained:
        st.bar_chart(trained, x="target_property", y="mae")

candidates = _load_csv(candidates_path)
if not candidates.empty:
    st.subheader("主动学习候选")
    st.caption("排序综合考虑预测性能、模型不确定性、实验可实现性和相似文献证据。")
    st.dataframe(candidates, use_container_width=True, hide_index=True)
else:
    st.info("还没有主动学习候选。训练模型后点击“生成主动学习候选”。")
