from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.config import get_settings
from backend.services.llm_extractor import llm_status
from backend.services.rag_answerer import answer_question
from backend.services.vector_store import build_lightweight_index


st.set_page_config(page_title="RAG 问答", layout="wide")
st.title("RAG 问答")
st.caption(
    "当前版本默认使用 LLM 综合回答，并强制基于 accepted facts、页码和证据句；本地结构化检索作为兜底。"
)

settings = get_settings()
status = llm_status()

col1, col2, col3 = st.columns([1, 2, 2])
with col1:
    if st.button("重建轻量检索索引", use_container_width=True):
        st.success(build_lightweight_index())
with col2:
    use_llm = st.checkbox(
        "使用 LLM 综合回答",
        value=True,
        help="开启后会把结构化事实、统计值和证据句交给大模型组织答案；不会把 API key 显示到页面。",
    )
with col3:
    llm_model = st.text_input("LLM 模型", value=settings.llm_model)

if status["api_key_configured"]:
    st.success(
        f"LLM 已接入：{status['provider']} / {llm_model}。accepted facts = approved + preapproved_machine + needs_human_review。"
    )
else:
    st.warning("还没有检测到 LLM API key，页面会退回本地结构化回答。")

question = st.text_area("问题", value="HZO 的 2Pr 范围是多少？", height=120)
if st.button("回答", type="primary"):
    with st.spinner("正在检索证据并调用 LLM 综合回答..."):
        st.markdown(answer_question(question, use_llm=use_llm, llm_model=llm_model))
