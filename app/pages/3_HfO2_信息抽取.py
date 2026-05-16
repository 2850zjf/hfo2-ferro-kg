from __future__ import annotations

import pandas as pd
import streamlit as st

from backend.core.config import get_settings
from backend.db.session import connect
from backend.services.chunker import build_chunks
from backend.services.hfo2_extractor import run_extraction
from backend.services.llm_extractor import llm_status


st.set_page_config(page_title="HfO2 信息抽取", layout="wide")
st.title("HfO2 信息抽取")

settings = get_settings()
status = llm_status()

st.subheader("LLM 配置自检")
st.json(status)

col1, col2 = st.columns(2)
with col1:
    pdf_limit = st.number_input("本轮切分 PDF 数量", min_value=1, max_value=500, value=20)
    if st.button("生成/刷新 chunk"):
        st.success(build_chunks(limit_pdfs=int(pdf_limit)))

with col2:
    chunk_limit = st.number_input("本轮抽取高价值 chunk 数量", min_value=1, max_value=5000, value=160)
    use_llm = st.toggle("启用 LLM 结构化抽取", value=settings.use_llm)
    dry_run = st.toggle("Dry-run，不写入候选/事实表", value=False)
    if st.button("开始抽取"):
        st.success(
            run_extraction(
                limit_chunks=int(chunk_limit),
                use_llm=use_llm,
                dry_run=dry_run,
            )
        )

with connect() as conn:
    chunk_df = pd.read_sql_query(
        """
        SELECT section, COUNT(*) AS n, SUM(is_high_value) AS high_value
        FROM document_chunks
        GROUP BY section
        ORDER BY n DESC
        """,
        conn,
    )
    candidate_df = pd.read_sql_query(
        """
        SELECT status, extractor_version, COUNT(*) AS n, AVG(confidence) AS avg_confidence
        FROM extraction_candidates
        GROUP BY status, extractor_version
        ORDER BY n DESC
        """,
        conn,
    )

st.subheader("Chunk 概览")
st.dataframe(chunk_df, use_container_width=True)

st.subheader("抽取候选概览")
st.dataframe(candidate_df, use_container_width=True)
