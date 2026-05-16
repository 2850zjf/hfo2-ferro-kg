from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from backend.db.session import connect
from backend.services.review_service import (
    REVIEW_STATUSES,
    export_approved_facts,
    list_review_facts,
    pdf_file_url,
    update_review_status,
)


st.set_page_config(page_title="抽取结果审核", layout="wide")
st.title("抽取结果审核")
st.caption("点击表格中的任意一行即可加载原文、证据、批注和 PDF 打开入口。")

left, middle, right = st.columns([1, 1, 1.5])
with left:
    status_filter = st.selectbox(
        "状态",
        ["all", "needs_human_review", "preapproved_machine", "approved", "rejected"],
    )
with middle:
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
with right:
    search_text = st.text_input("搜索", placeholder="fact_id / DOI / 标题 / 证据句")

facts = list_review_facts(status=status_filter, property_name=property_filter, limit=1000)
df = pd.DataFrame(facts)
if not df.empty and search_text.strip():
    needle = search_text.strip().lower()
    searchable_cols = [
        "fact_id",
        "paper_title",
        "doi",
        "material",
        "property_name",
        "evidence_text",
        "pdf_file_name",
    ]
    mask = pd.Series(False, index=df.index)
    for col in searchable_cols:
        if col in df:
            mask = mask | df[col].fillna("").astype(str).str.lower().str.contains(needle, regex=False)
    df = df[mask].reset_index(drop=True)

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
        "doi",
        "paper_title",
        "evidence_text",
    ]
    st.write("点击下方表格中的一行，详情区会自动切换到对应论文和事实。")
    table_event = st.dataframe(
        df[table_cols],
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="review_fact_table",
    )

    selected_rows = table_event.selection.rows if table_event.selection else []
    if selected_rows:
        st.session_state["selected_review_fact_id"] = df.iloc[selected_rows[0]]["fact_id"]
    if st.session_state.get("selected_review_fact_id") not in set(df["fact_id"]):
        st.session_state["selected_review_fact_id"] = df.iloc[0]["fact_id"]

    fact_id = st.selectbox(
        "当前审核 fact_id",
        df["fact_id"].tolist(),
        index=df["fact_id"].tolist().index(st.session_state["selected_review_fact_id"]),
        key="selected_review_fact_id",
    )
    selected = next(fact for fact in facts if fact["fact_id"] == fact_id)

    source_text = ""
    if selected.get("chunk_id"):
        with connect() as conn:
            row = conn.execute(
                "SELECT text FROM document_chunks WHERE chunk_id = ?",
                (selected["chunk_id"],),
            ).fetchone()
            source_text = row["text"] if row else ""

    st.divider()
    st.subheader("论文定位")
    p1, p2, p3, p4 = st.columns([2.4, 1, 1, 1.2])
    with p1:
        st.write(selected.get("paper_title") or "标题未识别")
        st.caption(f"PDF：{selected.get('pdf_file_name') or selected.get('pdf_id')}")
    with p2:
        st.metric("页码", selected.get("page_number") or "未识别")
    with p3:
        st.metric("年份", selected.get("year") or "未知")
    with p4:
        doi = selected.get("doi")
        if doi:
            st.link_button("打开 DOI", f"https://doi.org/{doi}", use_container_width=True)
        else:
            st.button("DOI 未识别", disabled=True, use_container_width=True)

    detail_left, detail_right = st.columns([3, 2])
    with detail_left:
        st.subheader("原文 chunk")
        source = source_text or selected.get("evidence_text") or ""
        evidence = selected.get("evidence_text") or ""
        if evidence and evidence in source:
            highlighted = source.replace(evidence, f"**{evidence}**", 1)
            st.markdown(highlighted)
        else:
            st.text_area("原文", source, height=380)
    with detail_right:
        st.subheader("候选事实")
        st.write(f"材料：`{selected.get('material')}`")
        st.write(f"材料体系：`{selected.get('material_family')}`")
        st.write(f"性能：`{selected.get('property_name')}`")
        st.write(f"数值：`{selected.get('value')} {selected.get('unit')}`")
        if selected.get("device_stack"):
            st.write(f"器件/堆栈：`{selected.get('device_stack')}`")
        st.write(f"证据句：{selected.get('evidence_text')}")

        with st.form("review_form"):
            new_status = st.selectbox(
                "审核状态",
                sorted(REVIEW_STATUSES),
                index=sorted(REVIEW_STATUSES).index(selected["review_status"])
                if selected["review_status"] in REVIEW_STATUSES
                else 0,
            )
            note_template = st.selectbox(
                "快速批注",
                [
                    "",
                    "证据清楚，材料/性能/单位一致。",
                    "疑似综述或引用二手数据，暂不批准。",
                    "疑似非 HfO2/HZO 材料，建议拒绝。",
                    "Pr/2Pr、单位或样品条件需要复核。",
                    "图表估读或上下文不足，保留人工复核。",
                ],
            )
            existing_notes = selected.get("reviewer_notes") or ""
            notes = st.text_area("审核备注 / 批注", existing_notes, height=160)
            submitted = st.form_submit_button("保存审核结果")
            if submitted:
                final_notes = notes
                if note_template and note_template not in final_notes:
                    final_notes = (final_notes + "\n" + note_template).strip()
                update_review_status(fact_id, new_status, final_notes)
                st.success("已保存审核结果。")
                st.rerun()

        st.subheader("原文 PDF")
        pdf_path = selected.get("pdf_path")
        pdf_url = pdf_file_url(pdf_path, selected.get("page_number"))
        if pdf_url:
            st.link_button("打开原文 PDF", pdf_url, use_container_width=True)
        else:
            st.button("PDF 文件未找到", disabled=True, use_container_width=True)
        if pdf_path and Path(pdf_path).exists():
            st.download_button(
                "备用：下载/打开 PDF",
                Path(pdf_path).read_bytes(),
                file_name=Path(pdf_path).name,
                mime="application/pdf",
                use_container_width=True,
            )
            st.caption(str(pdf_path))

approved_path = export_approved_facts()
st.download_button(
    "导出 approved_facts.csv",
    approved_path.read_bytes(),
    file_name="approved_facts.csv",
    mime="text/csv",
)
