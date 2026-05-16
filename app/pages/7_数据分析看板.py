from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.analytics import collect_summary, write_markdown_report


st.set_page_config(page_title="数据分析看板", layout="wide")
st.title("数据分析看板")

summary = collect_summary()

col1, col2 = st.columns(2)
with col1:
    st.subheader("文献年份分布")
    years = pd.DataFrame(
        [{"year": key, "count": value} for key, value in summary["paper_years"].items()]
    )
    st.bar_chart(years, x="year", y="count") if not years.empty else st.info("暂无数据")

with col2:
    st.subheader("预审核状态")
    statuses = pd.DataFrame(
        [{"status": key, "count": value} for key, value in summary["review_statuses"].items()]
    )
    st.bar_chart(statuses, x="status", y="count") if not statuses.empty else st.info("暂无数据")

st.subheader("材料体系")
families = pd.DataFrame(
    [{"family": key, "count": value} for key, value in summary["material_families"].items()]
)
st.dataframe(families, use_container_width=True)

st.subheader("性能类型")
props = pd.DataFrame(
    [{"property": key, "count": value} for key, value in summary["properties"].items()]
)
st.dataframe(props, use_container_width=True)

if st.button("导出 Markdown 报告"):
    st.success(f"已导出：{write_markdown_report()}")
