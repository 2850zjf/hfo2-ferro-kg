from __future__ import annotations

import sys
import time
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.config import get_settings
from backend.services.llm_extractor import llm_status
from backend.services.llm_quota_guard import clear_llm_pause, read_llm_pause
from backend.services.rag_job_service import (
    get_rag_job,
    list_rag_jobs,
    start_background_rag_job,
)
from backend.services.vector_store import build_lightweight_index


st.set_page_config(page_title="RAG 问答", layout="wide")
st.title("RAG 问答")
st.caption(
    "证据推理型 RAG：优先使用样品级关联事实和 benchmark 分层，再调用 LLM 组织答案。"
    "每个关键结论都要回到 fact_id、论文、页码、证据句和样品条件。"
)

settings = get_settings()
status = llm_status()
pause = read_llm_pause()

if "rag_question" not in st.session_state:
    st.session_state.rag_question = "HZO 的 2Pr 范围是多少？"
if "rag_job_id" not in st.session_state:
    st.session_state.rag_job_id = None

col1, col2, col3 = st.columns([1, 2, 2])
with col1:
    if st.button("重建轻量检索索引", use_container_width=True):
        st.success(build_lightweight_index())
with col2:
    default_use_llm = bool(status["api_key_configured"] and not pause)
    use_llm = st.checkbox(
        "使用 LLM 综合回答",
        value=default_use_llm,
        help="开启后会把结构化事实、分层统计和证据句交给大模型组织答案；API key 不会显示到页面。",
    )
with col3:
    llm_model = st.text_input("LLM 模型", value=settings.llm_model)

if pause:
    st.warning("LLM 调用已被暂停保护。当前页面会继续使用本地结构化事实回答。")
    with st.expander("查看暂停原因"):
        st.code(str(pause.get("reason", ""))[:1500])
    if st.button("我已恢复额度/账单状态，清除暂停提示"):
        clear_llm_pause()
        st.rerun()
elif status["api_key_configured"]:
    st.success(
        f"LLM 已接入：{status['provider']} / {llm_model}。"
        "回答会优先区分 strong_only、strong_partial 和 all_traceable。"
    )
else:
    st.warning("还没有检测到 LLM API key，页面会退回本地结构化证据回答。")

examples = [
    "HZO 的 2Pr 范围是多少？",
    "La 掺杂 HfO2 常见退火温度是多少？",
    "哪些论文报道了 orthorhombic Pca21 相？",
    "TiN 电极相关的 HfO2 铁电性能有哪些？",
    "哪些文献讨论了 wake-up 和 fatigue？",
    "HfO2 基 FeFET 的 memory window 有哪些报道？",
]

question = st.text_area(
    "问题",
    key="rag_question",
    height=120,
)
with st.expander("示例问题", expanded=False):
    for item in examples:
        if st.button(item, key=f"example_{item}", use_container_width=True):
            st.session_state.rag_question = item
            st.rerun()

left, right = st.columns([1, 3])
with left:
    if st.button("回答", type="primary", use_container_width=True):
        job = start_background_rag_job(
            st.session_state.rag_question,
            use_llm=use_llm and not bool(pause),
            llm_model=llm_model,
        )
        st.session_state.rag_job_id = job["job_id"]
        st.success("问答任务已放到后台。你可以切换页面，回来后结果仍会保留。")
        st.rerun()
with right:
    if st.button("刷新结果", use_container_width=True):
        st.rerun()

job_id = st.session_state.rag_job_id
if not job_id:
    recent_jobs = list_rag_jobs(limit=1)
    if recent_jobs:
        job_id = recent_jobs[0]["job_id"]
        st.session_state.rag_job_id = job_id

if job_id:
    job = get_rag_job(job_id)
    if job:
        st.subheader("当前问答任务")
        cols = st.columns(4)
        cols[0].metric("状态", job.get("status", "unknown"))
        cols[1].metric("模型", job.get("llm_model") or "local")
        cols[2].metric("创建时间", job.get("created_at") or "")
        cols[3].metric("完成时间", job.get("completed_at") or "")
        st.caption(f"job_id: `{job_id}`")

        if job.get("status") in {"queued", "running"}:
            st.info("正在后台检索事实、统计范围、整理证据并生成回答。切换页面不会中断。")
            time.sleep(2)
            st.rerun()
        elif job.get("status") == "failed":
            st.error(job.get("error_message") or "回答失败。")
        else:
            st.markdown(job.get("answer_markdown") or "没有返回内容。")

st.subheader("最近问答")
jobs = list_rag_jobs(limit=8)
if jobs:
    for item in jobs:
        with st.expander(
            f"{item.get('status')} | {item.get('created_at')} | {item.get('question')[:60]}",
            expanded=item.get("job_id") == st.session_state.rag_job_id,
        ):
            if st.button("切换到这个结果", key=f"switch_{item['job_id']}"):
                st.session_state.rag_job_id = item["job_id"]
                st.rerun()
            if item.get("answer_markdown"):
                st.markdown(item["answer_markdown"])
            elif item.get("error_message"):
                st.error(item["error_message"])
            else:
                st.write("还在等待结果。")
else:
    st.info("还没有问答记录。")
