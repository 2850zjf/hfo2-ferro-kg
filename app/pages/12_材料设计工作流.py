from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.config import PROJECT_ROOT
from backend.services.design_dataset import build_design_dataset
from backend.services.design_model import load_design_model_metrics, train_design_models
from backend.services.progress_monitor import monitor_snapshot


st.set_page_config(page_title="材料设计工作流", layout="wide")

st.title("材料设计工作流")
st.caption("把文献抽取结果变成可验证、可训练、可迭代的 HfO2 铁电材料 benchmark。")

snapshot = monitor_snapshot()
runtime = snapshot.get("runtime") or {}
counts = snapshot.get("counts") or {}

status_cols = st.columns(5)
status_cols[0].metric("PDF", counts.get("pdf_files", 0))
status_cols[1].metric("已解析 PDF", counts.get("parsed_pdfs", 0))
status_cols[2].metric("高价值 chunk", counts.get("high_value_chunks", 0))
status_cols[3].metric("审核/预审核事实", counts.get("reviewed_facts", 0))
status_cols[4].metric("Benchmark 抽取", counts.get("benchmark_extractions", 0))

if snapshot.get("active"):
    st.info(
        f"后台仍在运行：{runtime.get('active_step', '处理中')}，"
        f"已运行 {runtime.get('elapsed', '未知')}，预计剩余 {runtime.get('eta', '估算中')}。"
    )
    st.link_button("打开稳定实时监控", "http://127.0.0.1:8502/", use_container_width=False)
else:
    st.success("当前没有检测到后台全量任务。可以构建设计数据集或训练 baseline 模型。")

st.divider()

st.subheader("完整闭环")
steps = [
    ("1. 文献解析", "PDF、表格、图注、图片对象和正文 chunk。"),
    ("2. LLM 全量抽取", "结构化事实 + 开放 benchmark 记录，不再只限旧本体。"),
    ("3. AI 预审核", "检查 Pr/2Pr、单位、页码、证据句和样品条件是否一致。"),
    ("4. 人工抽查确认", "先看高风险和高价值事实，确认后作为可信集合。"),
    ("5. 知识图谱", "保留材料、工艺、相结构、器件、性能和证据之间的关系。"),
    ("6. Benchmark 数据集", "把输入变量和目标性能整理成可训练表格。"),
    ("7. 模型训练", "先做 baseline，再加入多模型验证、主动学习和不确定性。"),
    ("8. 反推工艺", "提出候选材料/厚度/退火/电极组合，再回到实验验证。"),
]
for row_start in range(0, len(steps), 4):
    cols = st.columns(4)
    for col, (title, body) in zip(cols, steps[row_start : row_start + 4]):
        with col:
            st.markdown(f"**{title}**")
            st.write(body)

st.subheader("Knowledge Graph 和 Benchmark 的分工")
st.dataframe(
    pd.DataFrame(
        [
            {
                "模块": "Knowledge Graph",
                "主要回答": "为什么、来自哪里、哪些事实相互关联",
                "数据形态": "节点 + 关系 + 证据",
                "用途": "溯源、解释、RAG、发现关联路径",
            },
            {
                "模块": "Benchmark",
                "主要回答": "给定材料和工艺，性能能否预测或比较",
                "数据形态": "一行样品/条件 + 一个或多个目标值",
                "用途": "模型训练、模型评测、工艺优化、候选排序",
            },
        ]
    ),
    use_container_width=True,
    hide_index=True,
)

st.divider()

dataset_path = PROJECT_ROOT / "data" / "design" / "hfo2_design_dataset.csv"
metrics = load_design_model_metrics()
actions = st.columns([1, 1, 2])

with actions[0]:
    if st.button("构建设计数据集", type="primary", use_container_width=True):
        stats = build_design_dataset()
        st.success(f"已生成 {stats['rows']} 行：{stats['output_path']}")

with actions[1]:
    min_rows = st.number_input("单个目标最少行数", min_value=5, max_value=200, value=12, step=1)
    if st.button("训练 baseline 模型", use_container_width=True):
        stats = train_design_models(min_rows=int(min_rows))
        st.success(
            f"训练完成：{stats['trained_models']} 个模型，跳过 {stats['skipped_models']} 个目标。"
        )
        metrics = stats

with actions[2]:
    st.markdown("**建议训练顺序**")
    st.write("先训练 2Pr、Pr、Ec；数据量稳定后再训练 endurance、retention、memory window。")

if dataset_path.exists():
    df = pd.read_csv(dataset_path)
    st.subheader("设计数据集概览")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("训练行数", len(df))
    c2.metric("目标类型", df["target_property"].nunique() if "target_property" in df else 0)
    c3.metric("论文数", df["paper_id"].nunique() if "paper_id" in df else 0)
    c4.metric("材料体系", df["material_family"].nunique() if "material_family" in df else 0)

    if "target_property" in df:
        target_counts = df["target_property"].value_counts().reset_index()
        target_counts.columns = ["target_property", "count"]
        st.bar_chart(target_counts, x="target_property", y="count")
    st.dataframe(df.head(200), use_container_width=True, hide_index=True)
    st.download_button(
        "下载设计数据集 CSV",
        dataset_path.read_bytes(),
        file_name=dataset_path.name,
        mime="text/csv",
        use_container_width=True,
    )
else:
    st.warning("还没有设计数据集。等抽取完成后点击“构建设计数据集”。")

if metrics:
    st.subheader("Baseline 模型结果")
    metrics_df = pd.DataFrame(metrics.get("targets", []))
    if not metrics_df.empty:
        st.dataframe(metrics_df, use_container_width=True, hide_index=True)
        trained = metrics_df[metrics_df["status"] == "trained"].copy()
        if not trained.empty and "mae" in trained:
            st.bar_chart(trained, x="target_property", y="mae")
    st.code(metrics.get("metrics_path", ""), language="text")

st.divider()
st.subheader("后续优化方向")
st.markdown(
    """
- 用 LLM 继续补全弱上下文事实：厚度、退火、电极、相结构和 wake-up/endurance 状态。
- 用知识图谱做约束：模型推荐必须能追溯到相近文献证据。
- 用 benchmark 做模型评测：按年份、材料体系、论文分组拆分训练/测试，避免同一论文泄漏。
- 用主动学习做实验建议：优先推荐“预测高性能 + 不确定性高 + 可实验实现”的组合。
""".strip()
)
