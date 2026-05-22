from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.config import PROJECT_ROOT, get_settings
from backend.services.multi_model_validator import run_multi_model_validation


st.set_page_config(page_title="多模型数据验证", layout="wide")
st.title("多模型数据验证")
st.caption(
    "把当前 reviewed_facts 视为 accepted baseline，用多个验证模型检查事实一致性，并展示各模型与基准集的一致率。"
)

summary_csv = PROJECT_ROOT / "data" / "exports" / "model_validation_summary.csv"
details_csv = PROJECT_ROOT / "data" / "exports" / "model_validation_details.csv"

with st.expander("验证设置", expanded=True):
    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        limit = st.number_input(
            "验证条数，0 表示全量",
            min_value=0,
            value=50,
            step=50,
            help="LLM 复核会逐条调用模型。建议先看 50 条；确认效果后再设为 0 全量运行。",
        )
    with c2:
        include_llm = st.checkbox("同时调用 LLM 复核", value=True)
    with c3:
        default_model = get_settings().llm_model
        llm_models = st.text_input(
            "LLM 模型列表",
            value=default_model,
            help="多个模型用英文逗号分隔；本地规则模型会始终运行。",
        )
    if st.button("运行多模型验证", type="primary"):
        models = [item.strip() for item in llm_models.split(",") if item.strip()]
        with st.spinner("正在验证数据集..."):
            stats = run_multi_model_validation(
                limit=None if int(limit) == 0 else int(limit),
                include_llm=include_llm,
                llm_models=models,
            )
        st.success(f"完成：{stats['facts']} 条事实，{stats['models']} 个模型/验证器。")

if summary_csv.exists():
    summary = pd.read_csv(summary_csv)
    st.subheader("模型一致率")
    chart_df = summary[["model_name", "agreement_accuracy"]].copy()
    chart_df["agreement_accuracy"] = chart_df["agreement_accuracy"] * 100
    st.bar_chart(chart_df, x="model_name", y="agreement_accuracy")

    c1, c2, c3, c4 = st.columns(4)
    best = summary.sort_values("agreement_accuracy", ascending=False).iloc[0]
    c1.metric("最高一致率", f"{best['agreement_accuracy'] * 100:.1f}%", best["model_name"])
    c2.metric("模型数量", len(summary))
    c3.metric("验证事实数", int(summary["total"].max()))
    c4.metric("平均置信度", f"{summary['average_confidence'].mean() * 100:.1f}%")

    st.subheader("模型结果表")
    display = summary.copy()
    display["agreement_accuracy"] = (display["agreement_accuracy"] * 100).round(1)
    display["average_confidence"] = (display["average_confidence"] * 100).round(1)
    st.dataframe(display, use_container_width=True, hide_index=True)

    st.download_button(
        "下载模型准确率汇总 CSV",
        summary_csv.read_bytes(),
        file_name=summary_csv.name,
        mime="text/csv",
        use_container_width=True,
    )

    if details_csv.exists():
        details = pd.read_csv(details_csv)
        st.subheader("问题事实明细")
        issue_details = details[details["verdict"] != "valid"].copy()
        st.dataframe(issue_details.head(300), use_container_width=True, hide_index=True)
        st.download_button(
            "下载验证明细 CSV",
            details_csv.read_bytes(),
            file_name=details_csv.name,
            mime="text/csv",
            use_container_width=True,
        )

    st.subheader("Top Issues")
    for _, row in summary.iterrows():
        with st.expander(str(row["model_name"]), expanded=False):
            try:
                issues = json.loads(row["top_issues"])
            except Exception:
                issues = {}
            if issues:
                st.table(pd.DataFrame([{"issue": key, "count": value} for key, value in issues.items()]))
            else:
                st.write("没有明显问题。")
else:
    st.info("还没有验证结果。点击上方“运行多模型验证”生成图示。")
