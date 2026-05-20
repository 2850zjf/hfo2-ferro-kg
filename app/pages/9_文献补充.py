from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.db.session import connect
from backend.services.literature_discovery import DEFAULT_QUERIES, discover_literature
from backend.services.open_access_downloader import download_open_access_pdfs


st.set_page_config(page_title="文献补充", layout="wide")
st.title("文献补充")
st.caption("只发现开放数据库中的论文候选，不自动下载学校订阅全文；候选确认后再进入 PDF 解析主流程。")

with st.expander("检索设置", expanded=True):
    query_text = st.text_area("检索式，每行一个", value="\n".join(DEFAULT_QUERIES), height=160)
    col1, col2, col3 = st.columns(3)
    with col1:
        from_date = st.text_input("起始日期", value="2024-01-01")
    with col2:
        to_date = st.text_input("结束日期，留空表示今天", value="")
    with col3:
        rows_per_source = st.number_input("每个来源每条检索式返回数量", min_value=5, max_value=100, value=25)
    min_score = st.slider("领域匹配阈值", min_value=0.0, max_value=1.0, value=0.25, step=0.05)
    sources = st.multiselect("公开数据源", ["OpenAlex", "Crossref"], default=["OpenAlex", "Crossref"])

if st.button("发现新文献候选", type="primary"):
    queries = [line.strip() for line in query_text.splitlines() if line.strip()]
    stats = discover_literature(
        queries=queries,
        from_date=from_date,
        to_date=to_date.strip() or None,
        rows_per_source=int(rows_per_source),
        include_openalex="OpenAlex" in sources,
        include_crossref="Crossref" in sources,
        min_score=float(min_score),
    )
    st.success("候选发现完成")
    st.json(stats)

st.subheader("开放全文下载")
download_col1, download_col2 = st.columns([1, 2])
with download_col1:
    download_limit = st.number_input("本轮最多下载 PDF 数", min_value=1, max_value=100, value=10)
with download_col2:
    download_score = st.slider("下载匹配阈值", min_value=0.0, max_value=1.0, value=0.45, step=0.05)
if st.button("下载合法开放全文 PDF"):
    stats = download_open_access_pdfs(limit=int(download_limit), min_score=float(download_score))
    st.success("开放全文下载完成")
    st.json(stats)

with connect() as conn:
    try:
        df = pd.read_sql_query(
            """
            SELECT candidate_id, year, title, doi, journal, source, oa_status, oa_url,
                   pdf_url, download_status, downloaded_pdf_pages, download_quality_score,
                   match_score, status, query
            FROM literature_candidates
            ORDER BY year DESC, match_score DESC, title
            """,
            conn,
        )
    except Exception:
        df = pd.DataFrame()

st.subheader("文献候选")
if df.empty:
    st.info("暂无候选。点击上方按钮从公开数据库发现新文献。")
else:
    col_a, col_b, col_c = st.columns(3)
    col_a.metric("候选数", len(df))
    col_b.metric("带 DOI", int(df["doi"].notna().sum()))
    col_c.metric("带开放链接", int(df["oa_url"].notna().sum() + df["pdf_url"].notna().sum()))

    keyword = st.text_input("标题 / DOI / 期刊过滤", value="")
    view = df
    if keyword.strip():
        needle = keyword.strip().lower()
        mask = view.apply(
            lambda row: needle in " ".join(str(value).lower() for value in row.values),
            axis=1,
        )
        view = view[mask]
    st.dataframe(view, use_container_width=True, hide_index=True)

    csv = view.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "下载当前候选 CSV",
        data=csv,
        file_name="hfo2_literature_candidates.csv",
        mime="text/csv",
    )
