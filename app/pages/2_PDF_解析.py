from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.support import PROJECT_ROOT
from backend.db.session import connect
from backend.services.pdf_parser import parse_pending_pdfs
from backend.services.table_extractor import extract_tables


st.set_page_config(page_title="PDF 解析", layout="wide")
st.title("PDF 解析")

limit = st.number_input("本轮解析 PDF 数量", min_value=1, max_value=500, value=5)
force = st.toggle("重新解析已解析 PDF", value=False)

if st.button("开始解析"):
    stats = parse_pending_pdfs(limit=int(limit), force=force)
    st.success(f"解析完成：{stats}")

if st.button("抽取表格"):
    stats = extract_tables(limit_pdfs=int(limit))
    st.success(f"表格抽取完成：{stats}")

with connect() as conn:
    pdf_df = pd.read_sql_query(
        """
        SELECT file_name, page_count, text_page_count, blank_page_count, low_text_page_count,
               parse_quality_score, ocr_needed, parse_status, error_message
        FROM pdf_files
        ORDER BY file_name
        """,
        conn,
    )
    page_count = conn.execute("SELECT COUNT(*) FROM parsed_pages").fetchone()[0]
    table_count = conn.execute("SELECT COUNT(*) FROM pdf_tables").fetchone()[0]

st.metric("已解析页面", page_count)
st.metric("已抽取表格", table_count)
st.dataframe(pdf_df, use_container_width=True)
