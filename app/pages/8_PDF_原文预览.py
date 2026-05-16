from __future__ import annotations

import html
import sys
import urllib.parse
from pathlib import Path

import fitz
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.review_service import get_pdf_viewer_record


def _query_value(name: str) -> str | None:
    value = st.query_params.get(name)
    if isinstance(value, list):
        return value[0] if value else None
    return value


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


def _page_url(pdf_id: str, page_number: int, fact_id: str | None) -> str:
    params = {"pdf_id": pdf_id, "page": str(page_number)}
    if fact_id:
        params["fact_id"] = fact_id
    return f"/PDF_原文预览?{urllib.parse.urlencode(params)}"


st.set_page_config(page_title="PDF 原文预览", layout="wide")
st.title("PDF 原文预览")

pdf_id = _query_value("pdf_id")
page_value = _query_value("page")
fact_id = _query_value("fact_id")

try:
    page_number = int(page_value) if page_value else 1
except ValueError:
    page_number = 1
page_number = max(page_number, 1)

if not pdf_id:
    st.warning("没有收到 pdf_id。请从“抽取结果审核”页面点击“打开原文 PDF”。")
    st.stop()

record = get_pdf_viewer_record(pdf_id)
if record is None:
    st.error(f"没有在数据库中找到 PDF：{pdf_id}")
    st.stop()

if not record["exists"]:
    st.error("数据库里有这篇 PDF 的记录，但本地文件不存在。")
    st.caption(record["file_path"])
    st.stop()

if not record["is_safe_path"]:
    st.error("这个 PDF 不在 data/raw_pdfs 安全目录下，已阻止预览。")
    st.caption(record["file_path"])
    st.stop()

pdf_path = Path(record["file_path"])
top_left, top_right = st.columns([3, 1.2])
with top_left:
    st.subheader(record["paper_title"] or record["file_name"])
    meta_bits = [record["file_name"]]
    if record["year"]:
        meta_bits.append(str(record["year"]))
    if record["doi"]:
        meta_bits.append(f"DOI: {record['doi']}")
    if fact_id:
        meta_bits.append(f"fact_id: {fact_id}")
    st.caption(" | ".join(meta_bits))
with top_right:
    st.metric("定位页码", page_number)
    if fact_id:
        _self_link_button("返回审核页", f"/抽取结果审核?fact_id={fact_id}")

pdf_bytes = pdf_path.read_bytes()
st.download_button(
    "下载 / 用本机 PDF 阅读器打开",
    pdf_bytes,
    file_name=pdf_path.name,
    mime="application/pdf",
    use_container_width=True,
)

try:
    with fitz.open(pdf_path) as doc:
        page_count = doc.page_count
        page_number = min(page_number, page_count)
        nav_left, nav_middle, nav_right = st.columns([1, 1, 1])
        with nav_left:
            if page_number > 1:
                _self_link_button("上一页", _page_url(pdf_id, page_number - 1, fact_id))
        with nav_middle:
            st.caption(f"第 {page_number} / {page_count} 页")
        with nav_right:
            if page_number < page_count:
                _self_link_button("下一页", _page_url(pdf_id, page_number + 1, fact_id))

        page = doc.load_page(page_number - 1)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(1.8, 1.8), alpha=False)
        st.image(
            pixmap.tobytes("png"),
            caption=f"{record['file_name']} | 第 {page_number} 页",
            use_container_width=True,
        )
except Exception as exc:
    st.error("PDF 页面渲染失败，可以先用上面的下载按钮打开完整 PDF。")
    st.caption(str(exc))
