from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from backend.db.session import connect


st.set_page_config(page_title="机器预审核留痕", layout="wide")
st.title("机器预审核留痕")
st.info("第一版暂不做人机审核编辑；这里展示机器预审核结果，后续人工复核时在此基础上扩展。")

status_filter = st.selectbox(
    "状态筛选",
    ["all", "preapproved_machine", "needs_human_review", "approved", "rejected"],
)

query = """
SELECT fact_id, review_status, paper_id, pdf_id, page_number, payload_json, reviewer_notes, created_at
FROM reviewed_facts
"""
params = ()
if status_filter != "all":
    query += " WHERE review_status = ?"
    params = (status_filter,)
query += " ORDER BY created_at DESC"

with connect() as conn:
    rows = conn.execute(query, params).fetchall()

records = []
for row in rows:
    payload = json.loads(row["payload_json"])
    prop = payload.get("property", {})
    material = payload.get("material") or {}
    preaudit = payload.get("preaudit", {})
    records.append(
        {
            "fact_id": row["fact_id"],
            "status": row["review_status"],
            "material": material.get("canonical_name"),
            "property": prop.get("property_name"),
            "value": prop.get("normalized_value") or prop.get("value"),
            "unit": prop.get("normalized_unit") or prop.get("unit"),
            "page": row["page_number"],
            "source": preaudit.get("extraction_source"),
            "confidence": preaudit.get("confidence"),
            "evidence": prop.get("evidence_text"),
        }
    )

df = pd.DataFrame(records)
if df.empty:
    st.info("还没有预审核事实。")
else:
    st.dataframe(df, use_container_width=True)
    st.download_button(
        "导出机器预审核事实 CSV",
        df.to_csv(index=False).encode("utf-8-sig"),
        file_name="machine_preaudited_facts.csv",
        mime="text/csv",
    )
