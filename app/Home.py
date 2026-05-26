from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.config import get_settings
from backend.db.init_db import init_database, list_tables
from backend.services.pipeline_log import recent_pipeline_runs
from backend.services.progress_monitor import monitor_snapshot


st.set_page_config(page_title="HfO2-FerroKG", page_icon="", layout="wide")

settings = get_settings()
snapshot = monitor_snapshot()
counts = snapshot.get("counts", {})
runtime = snapshot.get("runtime", {})
tokens = snapshot.get("tokens", {})

st.title("HfO2-FerroKG")
st.caption("氧化铪基铁电材料知识图谱与材料设计工作台")

status = st.container()
with status:
    if snapshot.get("active"):
        st.info(
            f"后台正在运行：{runtime.get('active_step', '处理中')}；"
            f"总运行 {runtime.get('elapsed', '估算中')}；"
            f"剩余 {runtime.get('eta', '估算中')}。"
        )
    else:
        st.success("当前没有检测到后台全量任务。")
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.page_link("pages/11_任务实时监控.py", label="进入任务实时监控")
    with col_b:
        st.link_button("打开独立监控页", "http://127.0.0.1:8502/", use_container_width=True)
    with col_c:
        st.page_link("pages/12_材料设计工作流.py", label="进入材料设计工作流")

metric_cols = st.columns(6)
metric_cols[0].metric("PDF", counts.get("pdf_files", 0))
metric_cols[1].metric("解析页", counts.get("parsed_pages", 0))
metric_cols[2].metric("chunk", counts.get("document_chunks", 0))
metric_cols[3].metric("审核事实", counts.get("reviewed_facts", 0))
metric_cols[4].metric("Benchmark", counts.get("benchmark_extractions", 0))
metric_cols[5].metric("样品级关联", counts.get("sample_property_links", 0))

token_cols = st.columns(4)
token_cols[0].metric("输入 token", tokens.get("prompt_tokens", 0))
token_cols[1].metric("输出 token", tokens.get("completion_tokens", 0))
token_cols[2].metric("累计 token", tokens.get("total_tokens", 0))
token_cols[3].metric("估算费用", tokens.get("total_cost_cny", "0.00"))

left, right = st.columns([2, 1])

with left:
    st.subheader("第一阶段目标")
    st.write(
        "基于本地 PDF 文献，解析 HfO2 基铁电材料的组成、工艺、相结构、铁电性能和证据句，"
        "经人工或 AI 预审核后构建知识图谱、RAG 问答、benchmark 数据集和材料设计模型。"
    )

    st.subheader("当前工作流")
    st.markdown(
        """
        1. PDF 文献入库与解析
        2. 表格、图注、正文 chunk 和视觉资产抽取
        3. HfO2 专用信息抽取与 AI 预审核
        4. 样品级事实关联：材料、厚度、退火、电极、相结构、性能
        5. 知识图谱与设计图谱构建
        6. 证据推理式 RAG 问答
        7. Benchmark 数据集、baseline 模型和主动学习推荐
        """
    )

with right:
    st.subheader("本地状态")
    st.write(f"项目目录：`{settings.project_root}`")
    st.write(f"PDF 目录：`{settings.pdf_root}`")
    st.write(f"数据库：`{settings.db_path}`")

    if st.button("初始化 SQLite 数据库"):
        db_path = init_database()
        st.success(f"数据库已初始化：{db_path}")

    if settings.db_path.exists():
        with st.expander("数据库表", expanded=False):
            st.code("\n".join(list_tables(settings.db_path)))
    else:
        st.info("数据库尚未初始化。")

st.subheader("最近流水线运行")
try:
    runs = recent_pipeline_runs(limit=10)
    if runs:
        st.dataframe(runs, use_container_width=True)
    else:
        st.write("暂无运行记录。")
except Exception:
    st.write("数据库初始化后会显示运行记录。")

st.divider()
st.info("请把本地 PDF 放入 data/raw_pdfs/。该目录已被 .gitignore 排除，不会提交到 Git。")
