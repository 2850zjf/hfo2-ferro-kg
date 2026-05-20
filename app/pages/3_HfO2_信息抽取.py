from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.config import get_settings
from backend.db.session import connect
from backend.services.chunker import build_chunks
from backend.services.hfo2_extractor import run_extraction
from backend.services.llm_extractor import llm_status
from backend.services.ontology_builder import build_ontology, load_ontology_bundle


st.set_page_config(page_title="HfO2 信息抽取", layout="wide")
st.title("HfO2 信息抽取")
st.caption("按 HfO2-FerroKG 数据本体进行 chunk 筛选、结构化抽取、预审核和证据留痕。")

settings = get_settings()
status = llm_status()

st.subheader("本体版本")
try:
    ontology_bundle = load_ontology_bundle(build_if_missing=True)
    st.success(f"当前抽取本体：{ontology_bundle['version']}")
    st.caption(f"Bundle: {ontology_bundle['bundle_path']}")
except Exception as exc:
    st.warning(f"本体尚未构建：{exc}")

if st.button("先构建 / 刷新本体"):
    st.success(build_ontology())

st.subheader("LLM 配置自检")
st.json(status)

col1, col2 = st.columns(2)
with col1:
    pdf_limit = st.number_input("本轮切分 PDF 数量", min_value=1, max_value=500, value=20)
    if st.button("生成 / 刷新 chunk"):
        st.success(build_chunks(limit_pdfs=int(pdf_limit)))

with col2:
    chunk_limit = st.number_input("本轮抽取高价值 chunk 数量", min_value=1, max_value=10000, value=160)
    use_llm = st.toggle("启用 LLM 结构化抽取", value=settings.use_llm)
    dry_run = st.toggle("Dry-run，不写入候选和事实表", value=False)
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
        SELECT status, extractor_version, ontology_version, COUNT(*) AS n, AVG(confidence) AS avg_confidence
        FROM extraction_candidates
        GROUP BY status, extractor_version, ontology_version
        ORDER BY n DESC
        """,
        conn,
    )

st.subheader("Chunk 概览")
st.dataframe(chunk_df, use_container_width=True)

st.subheader("抽取候选概览")
st.dataframe(candidate_df, use_container_width=True)
