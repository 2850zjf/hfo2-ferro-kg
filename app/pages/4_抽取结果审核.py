from __future__ import annotations

import sys
import html
import urllib.parse
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.db.session import connect
from backend.services.review_service import (
    REVIEW_STATUSES,
    export_approved_facts,
    list_review_facts,
    open_pdf_in_default_browser,
    open_pdf_with_default_app,
    pdf_viewer_url,
    update_review_status,
)


def _short_cell(value: object, limit: int = 120) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return html.escape(text)


def _render_clickable_fact_table(df: pd.DataFrame, active_fact_id: str | None) -> None:
    columns = [
        ("fact_id", "fact_id（点击切换）", 180),
        ("review_status", "review_status", 170),
        ("material", "material", 120),
        ("property_name", "property_name", 260),
        ("value", "value", 90),
        ("unit", "unit", 90),
        ("page_number", "page", 80),
        ("confidence", "confidence", 100),
        ("doi", "doi", 180),
        ("paper_title", "paper_title", 360),
        ("evidence_text", "evidence_text", 460),
    ]
    header = "".join(
        f'<th style="min-width:{width}px">{html.escape(label)}</th>'
        for key, label, width in columns
    )
    rows = []
    for _, row in df.iterrows():
        fact_id = str(row.get("fact_id", ""))
        row_class = "active-row" if fact_id == active_fact_id else ""
        cells: list[str] = []
        for key, _, _ in columns:
            if key == "fact_id":
                href = f"?fact_id={urllib.parse.quote(fact_id)}"
                cells.append(
                    '<td class="fact-id">'
                    f'<a href="{href}" target="_self" title="切换到 {html.escape(fact_id)}">'
                    f"{html.escape(fact_id)}</a></td>"
                )
            elif key == "confidence":
                value = row.get(key, "")
                try:
                    value = f"{float(value):.2f}"
                except (TypeError, ValueError):
                    pass
                cells.append(f"<td>{_short_cell(value)}</td>")
            elif key in {"paper_title", "evidence_text"}:
                cells.append(f"<td>{_short_cell(row.get(key, ''), 160)}</td>")
            else:
                cells.append(f"<td>{_short_cell(row.get(key, ''))}</td>")
        rows.append(f'<tr class="{row_class}">' + "".join(cells) + "</tr>")

    st.markdown(
        """
        <style>
        .review-table-wrap {
            max-height: 430px;
            overflow: auto;
            border: 1px solid rgba(250, 250, 250, 0.16);
            border-radius: 8px;
            margin-bottom: 1rem;
        }
        .review-table {
            border-collapse: collapse;
            width: 100%;
            font-size: 0.84rem;
        }
        .review-table th {
            position: sticky;
            top: 0;
            z-index: 1;
            background: #171821;
            color: rgba(250, 250, 250, 0.74);
            font-weight: 600;
            text-align: left;
        }
        .review-table th,
        .review-table td {
            border-bottom: 1px solid rgba(250, 250, 250, 0.11);
            border-right: 1px solid rgba(250, 250, 250, 0.08);
            padding: 0.48rem 0.58rem;
            white-space: nowrap;
            vertical-align: top;
        }
        .review-table tr:hover {
            background: rgba(255, 255, 255, 0.055);
        }
        .review-table .active-row {
            background: rgba(255, 75, 75, 0.14);
        }
        .review-table .fact-id a {
            color: #7eb6ff;
            text-decoration: none;
            font-weight: 600;
        }
        .review-table .fact-id a:hover {
            text-decoration: underline;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""
        <div class="review-table-wrap">
            <table class="review-table">
                <thead><tr>{header}</tr></thead>
                <tbody>{''.join(rows)}</tbody>
            </table>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_source_chunk(source: str, evidence: str) -> None:
    escaped_source = html.escape(source)
    if evidence and evidence in source:
        escaped_evidence = html.escape(evidence)
        escaped_source = escaped_source.replace(
            escaped_evidence,
            f'<mark class="evidence-highlight">{escaped_evidence}</mark>',
            1,
        )
    st.markdown(
        """
        <style>
        .source-chunk-box {
            max-height: 520px;
            overflow: auto;
            white-space: pre-wrap;
            background: rgba(255, 255, 255, 0.06);
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 8px;
            padding: 1rem;
            line-height: 1.55;
        }
        .source-chunk-box .evidence-highlight {
            background: rgba(255, 210, 77, 0.28);
            color: inherit;
            padding: 0 0.12rem;
            border-radius: 3px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="source-chunk-box">{escaped_source}</div>',
        unsafe_allow_html=True,
    )


def _self_link_button(label: str, href: str) -> None:
    st.markdown(
        f"""
        <a class="self-link-button" href="{html.escape(href)}" target="_self">
            {html.escape(label)}
        </a>
        <style>
        .self-link-button {{
            display: block;
            width: 100%;
            box-sizing: border-box;
            text-align: center;
            padding: 0.55rem 0.75rem;
            border: 1px solid rgba(250, 250, 250, 0.22);
            border-radius: 0.45rem;
            color: rgb(250, 250, 250) !important;
            text-decoration: none !important;
            font-weight: 600;
        }}
        .self-link-button:hover {{
            border-color: rgba(255, 75, 75, 0.75);
            background: rgba(255, 75, 75, 0.08);
        }}
        </style>
        """,
        unsafe_allow_html=True,
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
    visible_fact_ids = df["fact_id"].tolist()
    facts_by_id = {fact["fact_id"]: fact for fact in facts}

    query_fact_id = st.query_params.get("fact_id")
    if query_fact_id in visible_fact_ids:
        st.session_state["active_review_fact_id"] = query_fact_id
    if st.session_state.get("active_review_fact_id") not in set(visible_fact_ids):
        st.session_state["active_review_fact_id"] = visible_fact_ids[0]

    fact_id = st.session_state["active_review_fact_id"]
    st.write("点击或双击表格里的 fact_id，下面的当前审核对象会切换到对应论文和事实。")
    _render_clickable_fact_table(df[table_cols], fact_id)

    jump_left, jump_right = st.columns([1.3, 2])
    with jump_left:
        st.text_input("当前审核 fact_id", fact_id, disabled=True)
    with jump_right:
        manual_fact_id = st.selectbox(
            "手动跳转",
            visible_fact_ids,
            index=visible_fact_ids.index(fact_id),
            key=f"manual_review_fact_id_{fact_id}",
        )
        if manual_fact_id != fact_id:
            st.session_state["active_review_fact_id"] = manual_fact_id
            st.query_params["fact_id"] = manual_fact_id
            st.rerun()

    selected = facts_by_id[fact_id]

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
        _render_source_chunk(source, evidence)
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
        local_open_left, local_open_right = st.columns(2)
        with local_open_left:
            if st.button(
                "用默认浏览器打开 PDF",
                key=f"open_browser_pdf_{fact_id}",
                use_container_width=True,
            ):
                result = open_pdf_in_default_browser(
                    selected.get("pdf_id"),
                    selected.get("page_number"),
                )
                if result["ok"]:
                    st.success("已请求本机默认浏览器打开 PDF。")
                else:
                    st.error(result["message"])
        with local_open_right:
            if st.button(
                "用系统默认程序打开 PDF",
                key=f"open_default_pdf_{fact_id}",
                use_container_width=True,
            ):
                result = open_pdf_with_default_app(selected.get("pdf_id"))
                if result["ok"]:
                    st.success("已请求系统默认程序打开 PDF。")
                else:
                    st.error(result["message"])
        pdf_url = pdf_viewer_url(
            selected.get("pdf_id"),
            selected.get("page_number"),
            fact_id=fact_id,
        )
        if pdf_url:
            _self_link_button("网页内预览定位页", pdf_url)
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
