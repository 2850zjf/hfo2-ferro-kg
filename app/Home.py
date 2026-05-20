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


st.set_page_config(
    page_title="HfO2-FerroKG",
    page_icon="",
    layout="wide",
)

settings = get_settings()

st.title("HfO2-FerroKG")
st.caption("氧化铪基铁电材料知识图谱工作台")

left, right = st.columns([2, 1])

with left:
    st.subheader("第一阶段目标")
    st.write(
        "基于本地 PDF 文献，解析 HfO2 基铁电材料的组成、工艺、相结构、"
        "铁电性能和证据句，经人工审核后构建知识图谱，并提供可追溯问答。"
    )

    st.subheader("MVP 工作流")
    st.markdown(
        """
        1. PDF 文献入库与去重
        2. 本体构建与版本留痕
        3. PDF 文本、表格和图注解析
        4. HfO2 专用信息抽取
        5. 人工审核候选事实
        6. 构建知识图谱
        7. 基于证据的 RAG 问答
        8. 开放文献候选补充
        9. 导出论文图表和报告
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
        st.write("数据库表：")
        st.code("\n".join(list_tables(settings.db_path)))
    else:
        st.info("数据库尚未初始化。")

st.subheader("最近管线运行")
try:
    runs = recent_pipeline_runs(limit=8)
    if runs:
        st.dataframe(runs, use_container_width=True)
    else:
        st.write("暂无运行记录。")
except Exception:
    st.write("数据库初始化后会显示运行记录。")

st.divider()
st.info("请把本地 PDF 放入 data/raw_pdfs/。该目录已被 .gitignore 排除，不会提交到 Git。")
