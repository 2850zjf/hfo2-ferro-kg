from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.config import PROJECT_ROOT
from backend.services.active_learning import recommend_active_learning_candidates
from backend.services.design_dataset import build_design_dataset
from backend.services.design_graph import build_design_graph
from backend.services.design_model import load_design_model_metrics, train_design_models
from backend.services.llm_quota_guard import clear_llm_pause, read_llm_pause
from backend.services.progress_monitor import active_pipeline_processes, monitor_snapshot
from backend.services.workflow_runner import start_monitor_server, start_protected_full_pipeline


st.set_page_config(page_title="材料设计工作流", layout="wide")


def _run_background(command: list[str], prefix: str) -> dict[str, Any]:
    active = active_pipeline_processes()
    if any(str(item.get("task", "")).endswith(command[1]) for item in active if len(command) > 1):
        return {"started": False, "reason": "同类任务已经在运行"}
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


def _metric_row(label: str, value: Any, help_text: str = "") -> None:
    st.metric(label, value if value not in (None, "") else "0", help=help_text or None)


snapshot = monitor_snapshot()
counts = snapshot.get("counts", {})
runtime = snapshot.get("runtime", {})
tokens = snapshot.get("tokens", {})
pause = read_llm_pause()

st.title("材料设计工作流")
st.caption("从文献事实出发，构建样品级数据、设计图谱、可训练 benchmark、baseline 模型和主动学习候选。")

if pause:
    st.error("LLM 抽取已自动暂停。通常是额度、欠费、限流或 key 状态问题。已完成的数据不会丢。")
    with st.expander("暂停详情", expanded=False):
        st.write(pause.get("reason", ""))
        st.write(pause.get("resume_hint", "恢复额度或配置新 key 后继续同一条 pipeline。"))
    if st.button("我已恢复 API，清除暂停标记"):
        clear_llm_pause()
        st.success("已清除暂停标记。")

if snapshot.get("active"):
    st.info(
        f"后台正在运行：{runtime.get('active_step', '处理中')}；"
        f"总运行 {runtime.get('elapsed', '估算中')}；"
        f"当前步骤 {runtime.get('active_elapsed', '估算中')}；"
        f"剩余 {runtime.get('eta', '估算中')}；"
        f"速度 {runtime.get('rate_per_min', 0)} 条/分钟。"
    )
else:
    st.success("当前没有检测到全量后台任务。可以查看结果，或启动下一步。")

top_cols = st.columns(6)
with top_cols[0]:
    _metric_row("PDF", counts.get("pdf_files", 0))
with top_cols[1]:
    _metric_row("chunk", counts.get("document_chunks", 0))
with top_cols[2]:
    _metric_row("审核事实", counts.get("reviewed_facts", 0))
with top_cols[3]:
    _metric_row("Benchmark 抽取", counts.get("benchmark_extractions", 0))
with top_cols[4]:
    _metric_row("样品级关联", counts.get("sample_property_links", 0))
with top_cols[5]:
    _metric_row("累计 token", tokens.get("total_tokens", 0))

cost_cols = st.columns(4)
with cost_cols[0]:
    _metric_row("输入 token", tokens.get("prompt_tokens", 0))
with cost_cols[1]:
    _metric_row("输出 token", tokens.get("completion_tokens", 0))
with cost_cols[2]:
    _metric_row("估算费用", tokens.get("total_cost_cny", "0.00"))
with cost_cols[3]:
    _metric_row("LLM 状态", "暂停" if pause else "可用")

st.divider()

st.subheader("我们正在解决什么问题")
problem_cols = st.columns(4)
cards = [
    ("样品条件缺失", "只知道材料名和 Pr/2Pr 不够，必须绑定厚度、退火、电极、相结构和器件。"),
    ("证据不可追溯", "每个结论都要能回到 DOI、页码、chunk 和证据句。"),
    ("图谱不等于设计", "设计图谱要区分可控变量、目标变量、约束变量和机制变量。"),
    ("模型需要闭环", "baseline 只是起点，下一步要用主动学习推荐最值得做的实验。"),
]
for col, (title, body) in zip(problem_cols, cards):
    with col:
        st.markdown(f"**{title}**")
        st.write(body)

st.subheader("完整工作流")
workflow = pd.DataFrame(
    [
        {"阶段": "1 文献解析", "目标": "PDF、表格、图注、正文 chunk", "产物": "parsed_pages / chunks / tables"},
        {"阶段": "2 LLM 全量抽取", "目标": "材料、样品、工艺、相、性能、机制、设计规则", "产物": "extraction_candidates / benchmark_extractions"},
        {"阶段": "3 AI 预审核", "目标": "检查 Pr/2Pr、单位、页码、证据句、领域一致性", "产物": "preapproved / needs_human_review / rejected"},
        {"阶段": "4 样品级关联", "目标": "同一性能值绑定材料、厚度、退火、电极、相结构", "产物": "sample_property_links"},
        {"阶段": "5 设计图谱", "目标": "标出可控变量、目标变量、约束变量、机制变量", "产物": "design_graph CSV + HTML"},
        {"阶段": "6 Benchmark", "目标": "形成可训练的样品-工艺-性能表格", "产物": "hfo2_design_dataset.csv"},
        {"阶段": "7 Baseline 模型", "目标": "先判断数据能否支持性能预测", "产物": "RandomForest 模型和指标"},
        {"阶段": "8 主动学习", "目标": "推荐高性能、高不确定性、可实验实现的候选", "产物": "active_learning_candidates.csv"},
    ]
)
st.dataframe(workflow, use_container_width=True, hide_index=True)

st.divider()

st.subheader("执行控制")
actions = st.columns(5)
with actions[0]:
    if st.button("启动/确保监控页", use_container_width=True):
        result = start_monitor_server()
        st.success(f"监控页已启动：PID {result['pid']}")
        st.link_button("打开实时监控", result["url"])
with actions[1]:
    if st.button("启动全量流水线", type="primary", use_container_width=True):
        result = start_protected_full_pipeline(clear_pause=True)
        if result.get("started"):
            st.success(f"已启动：PID {result['pid']}")
            st.link_button("打开实时监控", result["monitor_url"])
        else:
            st.warning(f"没有启动：{result.get('reason')}")
with actions[2]:
    if st.button("仅跑样品级关联", use_container_width=True):
        result = _run_background(
            [
                sys.executable,
                "pipelines/23_link_sample_facts.py",
                "--max-workers",
                "32",
                "--progress-every",
                "25",
                "--commit-every",
                "100",
                "--force-llm-when-paused",
            ],
            "sample_linking_page",
        )
        if result.get("started"):
            st.success(f"样品级关联已启动：PID {result['pid']}")
        else:
            st.warning(result.get("reason"))
with actions[3]:
    if st.button("构建设计图谱", use_container_width=True):
        stats = build_design_graph()
        st.success(f"设计图谱：{stats['nodes']} 个节点，{stats['edges']} 条关系。")
with actions[4]:
    if st.button("构建数据集+模型+推荐", use_container_width=True):
        dataset_stats = build_design_dataset()
        model_stats = train_design_models(min_rows=12)
        rec_stats = recommend_active_learning_candidates()
        st.success(
            f"数据集 {dataset_stats['rows']} 行；训练 {model_stats['trained_models']} 个模型；"
            f"推荐 {rec_stats.get('candidates', 0)} 个候选。"
        )

st.divider()

dataset_path = PROJECT_ROOT / "data" / "design" / "hfo2_design_dataset.csv"
candidates_path = PROJECT_ROOT / "data" / "design" / "active_learning_candidates.csv"
design_graph_html = PROJECT_ROOT / "data" / "exports" / "hfo2_design_graph.html"
metrics = load_design_model_metrics()

st.subheader("当前产物")
artifact_cols = st.columns(3)
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
    st.dataframe(df.head(300), use_container_width=True, hide_index=True)
else:
    st.info("还没有设计数据集。可以点击上面的“构建数据集+模型+推荐”。")

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
    st.info("还没有主动学习候选。训练模型后点击“构建数据集+模型+推荐”。")
