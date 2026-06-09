from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.config import get_settings
from backend.db.init_db import init_database, list_tables
from backend.services.pipeline_log import recent_pipeline_runs
from backend.services.progress_monitor import monitor_snapshot


st.set_page_config(page_title="HfO2-FerroKG", page_icon="", layout="wide")


def _fmt(value: Any) -> str:
    if value in (None, ""):
        return "0"
    if isinstance(value, float):
        return f"{value:,.3g}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _metric_card(label: str, value: Any, note: str = "") -> None:
    st.markdown(
        f"""
        <div class="kg-metric">
            <div class="kg-metric-label">{label}</div>
            <div class="kg-metric-value">{_fmt(value)}</div>
            <div class="kg-metric-note">{note}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _artifact_state(path: Path) -> str:
    return "已生成" if path.exists() else "未生成"


def _run_rows(limit: int = 8) -> list[dict[str, Any]]:
    rows = []
    for item in recent_pipeline_runs(limit=limit):
        stats = item.get("stats") or {}
        rows.append(
            {
                "时间": item.get("created_at", ""),
                "步骤": item.get("step_name", ""),
                "状态": item.get("status", ""),
                "主要结果": ", ".join(
                    f"{key}={value}"
                    for key, value in stats.items()
                    if key
                    in {
                        "rows",
                        "model_rows",
                        "strong_only_rows",
                        "strong_partial_rows",
                        "all_traceable_rows",
                        "candidates",
                        "ai_audits",
                        "verdict",
                        "output_path",
                    }
                )[:180],
            }
        )
    return rows


st.markdown(
    """
    <style>
    :root {
        --kg-ink: #17202a;
        --kg-muted: #53616f;
        --kg-border: #d9e1e7;
        --kg-blue: #1f6feb;
        --kg-green: #26734d;
        --kg-amber: #9a6700;
        --kg-red: #b42318;
        --kg-panel: #f7f9fb;
    }
    .block-container {
        padding-top: 1.2rem;
        max-width: 1320px;
    }
    .kg-hero {
        border: 1px solid var(--kg-border);
        background: linear-gradient(180deg, #ffffff 0%, #f7fafc 100%);
        padding: 22px 24px;
        border-radius: 8px;
        margin-bottom: 18px;
    }
    .kg-title {
        color: var(--kg-ink);
        font-size: 36px;
        line-height: 1.12;
        font-weight: 760;
        margin: 0 0 8px 0;
        letter-spacing: 0;
    }
    .kg-subtitle {
        color: var(--kg-muted);
        font-size: 16px;
        line-height: 1.55;
        max-width: 980px;
        margin: 0;
    }
    .kg-chip-row {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-top: 16px;
    }
    .kg-chip {
        border: 1px solid var(--kg-border);
        background: #ffffff;
        color: #263747;
        border-radius: 999px;
        padding: 5px 10px;
        font-size: 13px;
        white-space: nowrap;
    }
    .kg-metric {
        border: 1px solid var(--kg-border);
        background: #ffffff;
        border-radius: 8px;
        min-height: 102px;
        padding: 14px 14px 12px;
    }
    .kg-metric-label {
        color: var(--kg-muted);
        font-size: 13px;
        margin-bottom: 9px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .kg-metric-value {
        color: var(--kg-ink);
        font-size: 26px;
        font-weight: 740;
        line-height: 1.1;
        letter-spacing: 0;
    }
    .kg-metric-note {
        color: var(--kg-muted);
        font-size: 12px;
        line-height: 1.35;
        margin-top: 8px;
        min-height: 16px;
    }
    .kg-band {
        border: 1px solid var(--kg-border);
        background: var(--kg-panel);
        border-radius: 8px;
        padding: 14px 16px;
        margin: 4px 0 14px;
    }
    .kg-status-ok { color: var(--kg-green); font-weight: 700; }
    .kg-status-warn { color: var(--kg-amber); font-weight: 700; }
    .kg-status-run { color: var(--kg-blue); font-weight: 700; }
    .kg-small {
        color: var(--kg-muted);
        font-size: 13px;
        line-height: 1.45;
    }
    div[data-testid="stMetricValue"] {
        font-size: 24px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


settings = get_settings()
snapshot = monitor_snapshot()
counts = snapshot.get("counts", {})
runtime = snapshot.get("runtime", {})
tokens = snapshot.get("tokens", {})

dataset_path = settings.project_root / "data" / "design" / "hfo2_design_dataset.csv"
strong_path = settings.project_root / "data" / "design" / "hfo2_design_dataset_strong_only.csv"
graph_path = settings.project_root / "data" / "exports" / "hfo2_design_graph.html"
report_path = settings.project_root / "data" / "exports" / "hfo2_design_progress_report.md"
gold_path = settings.project_root / "data" / "evaluation" / "hfo2_paper_gold_set_template.csv"

active = bool(snapshot.get("active"))
status_class = "kg-status-run" if active else "kg-status-ok"
status_text = (
    f"运行中：{runtime.get('active_step', '处理中')}，预计剩余 {runtime.get('eta', '估算中')}"
    if active
    else "空闲：当前没有检测到后台全量任务"
)

st.markdown(
    f"""
    <section class="kg-hero">
        <h1 class="kg-title">HfO2-FerroKG</h1>
        <p class="kg-subtitle">
            HfO2/HZO 铁电材料论文原型工作台：从本地 PDF 证据出发，构建样品级事实关联、
            分层 benchmark、设计图谱、Pr/2Pr 预测模型验证、计算反馈任务和证据推理式 RAG。
        </p>
        <div class="kg-chip-row">
            <span class="kg-chip">本体约束抽取</span>
            <span class="kg-chip">样品-性能关联</span>
            <span class="kg-chip">分层数据集</span>
            <span class="kg-chip">证据约束问答</span>
            <span class="kg-chip">Pr / 2Pr 主线</span>
        </div>
    </section>
    """,
    unsafe_allow_html=True,
)

status_cols = st.columns([1.6, 1, 1, 1])
with status_cols[0]:
    st.markdown(
        f"""
        <div class="kg-band">
            <div class="{status_class}">{status_text}</div>
            <div class="kg-small">项目目录：{settings.project_root}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with status_cols[1]:
    st.page_link("pages/12_材料设计工作流.py", label="材料设计工作流", use_container_width=True)
with status_cols[2]:
    st.page_link("pages/5_知识图谱浏览.py", label="知识图谱浏览", use_container_width=True)
with status_cols[3]:
    st.page_link("pages/6_RAG_问答.py", label="RAG 问答", use_container_width=True)

metric_cols = st.columns(6)
with metric_cols[0]:
    _metric_card("PDF 记录", counts.get("pdf_files", 0), f"已解析 {counts.get('parsed_pdfs', 0)}")
with metric_cols[1]:
    _metric_card("解析页", counts.get("parsed_pages", 0), f"表格 {counts.get('pdf_tables', 0)}")
with metric_cols[2]:
    _metric_card("文本切片", counts.get("document_chunks", 0), f"高价值 {counts.get('high_value_chunks', 0)}")
with metric_cols[3]:
    _metric_card("复核事实", counts.get("reviewed_facts", 0), "机器预审/待复核")
with metric_cols[4]:
    _metric_card("样品级关联", counts.get("sample_property_links", 0), f"strong {counts.get('sample_links_strong', 0)}")
with metric_cols[5]:
    _metric_card("AI 审核", counts.get("ai_fact_audits", 0), f"可建模 {counts.get('ai_usable_for_model', 0)}")

st.divider()

left, middle, right = st.columns([1.2, 1.1, 0.9])

with left:
    st.subheader("论文原型主线")
    st.markdown(
        """
        | 模块 | 当前主数据 |
        | --- | --- |
        | 样品级事实 | `sample_property_links` |
        | 上游事实 | `reviewed_facts` / `benchmark_extractions` |
        | 正式结果优先级 | strong rows + AI usable + 人工标注 |
        | 第一性能目标 | `remanent_polarization_Pr` / `double_remanent_polarization_2Pr` |
        """
    )
    st.page_link("pages/13_人工标注.py", label="进入人工标注", use_container_width=True)
    st.page_link("pages/10_事实一致性复核.py", label="事实一致性复核", use_container_width=True)

with middle:
    st.subheader("数据集与模型")
    tier_cols = st.columns(3)
    with tier_cols[0]:
        st.metric("强相关", _fmt(992), help="对应 strong_only，论文主结果优先使用")
    with tier_cols[1]:
        st.metric("强/部分相关", _fmt(4731), help="对应 strong_partial，用于探索分析和补充结果")
    with tier_cols[2]:
        st.metric("可追溯", _fmt(12168), help="对应 all_traceable，用于 RAG 线索和人工复核")
    st.markdown(
        """
        ```bash
        python3 pipelines/32_evaluate_paper_prototype.py --sample-size 30
        ```
        """
    )
    if gold_path.exists():
        st.caption(f"Gold set 模板：{gold_path}")
    else:
        st.caption("Gold set 模板尚未生成。")

with right:
    st.subheader("本地产物")
    artifact_rows = [
        {"产物": "Design dataset", "状态": _artifact_state(dataset_path)},
        {"产物": "strong_only CSV", "状态": _artifact_state(strong_path)},
        {"产物": "设计图谱 HTML", "状态": _artifact_state(graph_path)},
        {"产物": "进展报告", "状态": _artifact_state(report_path)},
        {"产物": "Gold set 模板", "状态": _artifact_state(gold_path)},
    ]
    st.dataframe(artifact_rows, use_container_width=True, hide_index=True)
    if report_path.exists():
        st.download_button(
            "下载进展报告",
            report_path.read_bytes(),
            file_name=report_path.name,
            mime="text/markdown",
            use_container_width=True,
        )

st.subheader("工作流")
workflow_cols = st.columns(5)
workflow_items = [
    ("1", "PDF 解析", "文本、表格、图注、chunk"),
    ("2", "结构化抽取", "材料、工艺、相、性能"),
    ("3", "样品级关联", "性能值绑定样品条件"),
    ("4", "分层 benchmark", "strong / partial / traceable"),
    ("5", "计算反馈", "VASP、相场、MD 任务规划"),
]
for col, (step, title, body) in zip(workflow_cols, workflow_items):
    with col:
        st.markdown(
            f"""
            <div class="kg-metric">
                <div class="kg-metric-label">Step {step}</div>
                <div class="kg-metric-value" style="font-size:19px">{title}</div>
                <div class="kg-metric-note">{body}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

st.divider()

ops_left, ops_right = st.columns([1, 1])
with ops_left:
    st.subheader("运行与配置")
    run_cols = st.columns(3)
    with run_cols[0]:
        st.page_link("pages/11_任务实时监控.py", label="任务实时监控", use_container_width=True)
    with run_cols[1]:
        st.link_button("独立监控页", "http://127.0.0.1:8502/", use_container_width=True)
    with run_cols[2]:
        if st.button("初始化 SQLite", use_container_width=True):
            db_path = init_database()
            st.success(f"数据库已初始化：{db_path}")

    st.markdown(
        f"""
        <div class="kg-band">
            <div class="kg-small">PDF 目录：{settings.pdf_root}</div>
            <div class="kg-small">数据库：{settings.db_path}</div>
            <div class="kg-small">LLM：{settings.llm_provider} / {settings.llm_model}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if settings.db_path.exists():
        with st.expander("数据库表", expanded=False):
            st.code("\n".join(list_tables(settings.db_path)))
    else:
        st.info("数据库尚未初始化。")

with ops_right:
    st.subheader("Token 与费用")
    token_cols = st.columns(3)
    token_cols[0].metric("输入 token", _fmt(tokens.get("actual_prompt_tokens", 0)))
    token_cols[1].metric("输出 token", _fmt(tokens.get("actual_completion_tokens", 0)))
    token_cols[2].metric(
        "估算费用",
        f"{tokens.get('estimated_live_cost', tokens.get('estimated_cost', 0))} {tokens.get('currency', 'CNY')}",
    )
    st.caption("真实 API key 只从环境变量或 .env 读取，不在网页、日志或报告中显示。")

st.subheader("最近流水线运行")
try:
    rows = _run_rows(limit=10)
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.write("暂无运行记录。")
except Exception:
    st.write("数据库初始化后会显示运行记录。")
