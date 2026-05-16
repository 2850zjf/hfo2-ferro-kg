from __future__ import annotations

import pandas as pd
import streamlit as st

from backend.db.session import connect
from backend.services.review_service import (
    REVIEW_STATUSES,
    export_approved_facts,
    list_review_facts,
    update_review_status,
)


st.set_page_config(page_title="抽取结果审核", layout="wide")
st.title("抽取结果审核")
st.caption("先把机器预审核结果作为候选事实；人工确认后再标记为 approved。")

left, right = st.columns([1, 1])
with left:
    status_filter = st.selectbox(
        "状态",
        ["all", "needs_human_review", "preapproved_machine", "approved", "rejected"],
    )
with right:
    property_filter = st.selectbox(
        "性能",
        [
            "all",
            "double_remanent_polarization_2Pr",
            "remanent_polarization_Pr",
            "coercive_field_Ec",
            "endurance_cycles",
            "memory_window",
        ],
    )

facts = list_review_facts(status=status_filter, property_name=property_filter, limit=1000)
df = pd.DataFrame(facts)

if df.empty:
    st.info("当前筛选条件下没有候选事实。")
else:
    table_cols = [
        "fact_id",
        "review_status",
        "material",
        "property_name",
        "value",
        "unit",
        "page_number",
        "confidence",
        "evidence_text",
    ]
    st.dataframe(df[table_cols], use_container_width=True, hide_index=True)

    fact_id = st.selectbox("选择要审核的 fact_id", df["fact_id"].tolist())
    selected = next(fact for fact in facts if fact["fact_id"] == fact_id)

    source_text = ""
    if selected.get("chunk_id"):
        with connect() as conn:
            row = conn.execute(
                "SELECT text FROM document_chunks WHERE chunk_id = ?",
                (selected["chunk_id"],),
            ).fetchone()
            source_text = row["text"] if row else ""

    detail_left, detail_right = st.columns([3, 2])
    with detail_left:
        st.subheader("原文 chunk")
        st.write(f"页码：{selected.get('page_number') or '未识别'}")
        st.text_area("原文", source_text or selected.get("evidence_text") or "", height=320)
    with detail_right:
        st.subheader("候选事实")
        st.write(f"材料：`{selected.get('material')}`")
        st.write(f"性能：`{selected.get('property_name')}`")
        st.write(f"数值：`{selected.get('value')} {selected.get('unit')}`")
        st.write(f"证据句：{selected.get('evidence_text')}")

        with st.form("review_form"):
            new_status = st.selectbox(
                "审核状态",
                sorted(REVIEW_STATUSES),
                index=sorted(REVIEW_STATUSES).index(selected["review_status"])
                if selected["review_status"] in REVIEW_STATUSES
                else 0,
            )
            notes = st.text_area("审核备注", selected.get("reviewer_notes") or "")
            submitted = st.form_submit_button("保存审核结果")
            if submitted:
                update_review_status(fact_id, new_status, notes)
                st.success("已保存审核结果。")
                st.rerun()

approved_path = export_approved_facts()
st.download_button(
    "导出 approved_facts.csv",
    approved_path.read_bytes(),
    file_name="approved_facts.csv",
    mime="text/csv",
)
