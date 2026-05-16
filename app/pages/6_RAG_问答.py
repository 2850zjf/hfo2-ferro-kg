from __future__ import annotations

import streamlit as st

from backend.services.rag_answerer import answer_question
from backend.services.vector_store import build_lightweight_index


st.set_page_config(page_title="RAG 问答", layout="wide")
st.title("RAG 问答")
st.caption("当前版本使用机器预审核事实 + 关键词检索 + 本地 TF-IDF 向量检索。")

if st.button("构建轻量检索索引"):
    st.success(build_lightweight_index())

question = st.text_area("问题", value="HZO 的 2Pr 范围是多少？")
if st.button("回答"):
    st.markdown(answer_question(question))
