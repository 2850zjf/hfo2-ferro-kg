from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.support import PROJECT_ROOT
from backend.core.config import discover_pdf_root
from backend.db.session import connect
from backend.services.pdf_manifest import build_manifest


st.set_page_config(page_title="PDF 文献入库", layout="wide")
st.title("PDF 文献入库")

pdf_root = st.text_input("PDF 目录", value=str(discover_pdf_root()))

if st.button("扫描 PDF 并生成清单"):
    rows = build_manifest(pdf_root=PROJECT_ROOT.joinpath(pdf_root) if pdf_root.startswith(".") else __import__("pathlib").Path(pdf_root))
    st.success(f"已登记 {len(rows)} 个 PDF，重复文件 {sum(row.is_duplicate for row in rows)} 个。")

with connect() as conn:
    df = pd.read_sql_query(
        """
        SELECT file_name, file_size, page_count, parse_status, is_duplicate, duplicate_of_pdf_id, file_path
        FROM pdf_files
        ORDER BY file_name
        """,
        conn,
    )

if df.empty:
    st.info("还没有 PDF 清单。")
else:
    st.metric("PDF 数量", len(df))
    st.metric("重复文件", int(df["is_duplicate"].sum()))
    st.dataframe(df, use_container_width=True)
