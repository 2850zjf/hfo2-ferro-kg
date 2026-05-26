from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.config import get_settings
from backend.services.llm_extractor import llm_status
from backend.services.llm_quota_guard import clear_llm_pause, read_llm_pause
from backend.services.rag_answerer import answer_question
from backend.services.vector_store import build_lightweight_index


st.set_page_config(page_title="RAG 问答", layout="wide")
st.title("RAG 问答")
st.caption("优先使用 accepted facts、页码和证据句回答；LLM 只负责综合表达，不允许脱离证据编造。")

settings = get_settings()
status = llm_status()
pause = read_llm_pause()

col1, col2, col3 = st.columns([1, 2, 2])
with col1:
    if st.button("重建轻量检索索引", use_container_width=True):
        st.success(build_lightweight_index())
with col2:
    use_llm = st.checkbox(
        "使用 LLM 综合回答",
        value=bool(status["api_key_configured"] and not pause),
        help="开启后会把结构化事实、统计值和证据句交给大模型组织答案；不会把 API key 显示到页面。",
    )
with col3:
    llm_model = st.text_input("LLM 模型", value=settings.llm_model)

if pause:
    st.warning(
        "LLM 调用已被暂停保护。原因通常是额度、账单、认证或限流问题。"
        "当前页面会继续使用本地结构化事实回答。"
    )
    with st.expander("查看暂停原因"):
        st.code(str(pause.get("reason", ""))[:1500])
    if st.button("我已恢复额度/账单状态，清除暂停提示"):
        clear_llm_pause()
        st.rerun()
elif status["api_key_configured"]:
    st.success(
        f"LLM 已接入：{status['provider']} / {llm_model}。"
        "accepted facts = approved + preapproved_machine + needs_human_review。"
    )
else:
    st.warning("还没有检测到 LLM API key，页面会退回本地结构化回答。")

question = st.text_area("问题", value="HZO 的 2Pr 范围是多少？", height=120)
if st.button("回答", type="primary"):
    with st.spinner("正在检索证据并组织回答..."):
        st.markdown(answer_question(question, use_llm=use_llm, llm_model=llm_model))
