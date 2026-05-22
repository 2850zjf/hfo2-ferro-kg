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
st.caption(
    "按 HfO2-FerroKG 本体抽取材料体系、样品工艺、相结构、器件、性能和证据句。"
    "机器结果先进入候选和预审核区，后续仍需人工确认。"
)

settings = get_settings()
status = llm_status()

st.subheader("本体与抽取范围")
try:
    ontology_bundle = load_ontology_bundle(build_if_missing=True)
    st.success(f"当前抽取本体：{ontology_bundle['version']}")
    st.caption(f"Bundle: {ontology_bundle['bundle_path']}")
except Exception as exc:
    st.warning(f"本体尚未构建：{exc}")

if st.button("构建 / 刷新本体"):
    st.success(build_ontology())

st.markdown(
    """
本轮抽取会优先保留这些上下文字段：

- 材料体系
- Pr 或 2Pr
- 薄膜厚度
- 退火温度 / 时间 / 气氛
- 电极 stack
- 沉积方法
- 器件类型
- 相结构
- wake-up / endurance 状态
- 证据句和页码
"""
)

st.subheader("大模型配置自检")
st.json(status)
if not status["api_key_configured"]:
    st.warning("还没有检测到本地 API key。请在 .env 中配置，不要写入代码或提交 Git。")
elif status.get("provider") == "dashscope":
    st.info(
        "已配置 DashScope/Qwen。系统会使用 OpenAI-compatible chat 接口，"
        "并用本地 Pydantic Schema 严格校验返回 JSON。"
    )
elif status.get("provider") == "openai":
    st.info("已配置 OpenAI 结构化输出。")

left, right = st.columns(2)

with left:
    st.subheader("1. 重新切分文献")
    pdf_limit = st.number_input("本轮切分 PDF 数量", min_value=1, max_value=500, value=20)
    st.caption("建议先用 5-20 篇校准分段质量，确认后再全量处理。")
    if st.button("生成 / 刷新 chunk"):
        with st.spinner("正在切分文献，请稍候..."):
            st.success(build_chunks(limit_pdfs=int(pdf_limit)))

with right:
    st.subheader("2. 运行知识抽取")
    chunk_limit = st.number_input("本轮抽取高价值 chunk 数量", min_value=1, max_value=10000, value=10)
    use_llm = st.toggle("启用 LLM 结构化抽取", value=settings.use_llm)
    dry_run = st.toggle("Dry-run：只测试，不写入候选和事实表", value=True)
    reset_existing = st.toggle("重置已有候选和预审核事实", value=False)
    st.caption("推荐先 dry-run 1-5 个 chunk；确认模型输出稳定后，再关闭 dry-run 做正式写入。")
    if st.button("开始抽取"):
        with st.spinner("正在抽取 HfO2 知识，请稍候..."):
            stats = run_extraction(
                limit_chunks=int(chunk_limit),
                use_llm=use_llm,
                dry_run=dry_run,
                reset_existing=reset_existing,
            )
        st.success(stats)

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
