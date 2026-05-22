from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.config import PROJECT_ROOT
from backend.services.graph_builder import build_graph
from backend.services.graph_visualizer import export_graph_html


st.set_page_config(page_title="知识图谱浏览", layout="wide")
st.title("知识图谱浏览")
st.caption("基于已审核/机器预审事实构建图谱，并导出可缩放、可点击、可搜索的离线 HTML 窗口。")

nodes_path = PROJECT_ROOT / "data" / "graph" / "nodes.csv"
edges_path = PROJECT_ROOT / "data" / "graph" / "edges.csv"
html_path = PROJECT_ROOT / "data" / "exports" / "hfo2_knowledge_graph.html"

actions = st.columns([1, 1, 1, 1.4])
with actions[0]:
    if st.button("重建图谱 CSV", use_container_width=True):
        stats = build_graph()
        st.success(f"已重建：{stats['nodes']} 个节点，{stats['edges']} 条关系")
with actions[1]:
    if st.button("生成可视化 HTML", use_container_width=True):
        stats = export_graph_html()
        st.success(f"已生成：{stats['output_path']}")
with actions[2]:
    if html_path.exists():
        st.download_button(
            "下载 HTML 图谱",
            html_path.read_bytes(),
            file_name=html_path.name,
            mime="text/html",
            use_container_width=True,
        )
    else:
        st.button("下载 HTML 图谱", disabled=True, use_container_width=True)
with actions[3]:
    if html_path.exists():
        st.link_button("新窗口打开 HTML 图谱", html_path.resolve().as_uri(), use_container_width=True)
    else:
        st.button("新窗口打开 HTML 图谱", disabled=True, use_container_width=True)

if nodes_path.exists() and edges_path.exists():
    nodes = pd.read_csv(nodes_path)
    edges = pd.read_csv(edges_path)
    m1, m2, m3 = st.columns(3)
    m1.metric("节点", len(nodes))
    m2.metric("关系", len(edges))
    m3.metric("节点类型", nodes["type"].nunique() if "type" in nodes else 0)

    if html_path.exists():
        st.subheader("可缩放图谱窗口")
        st.caption("滚轮缩放，拖动画布平移，点击节点查看详情；左侧面板可搜索材料、DOI、性能或证据句。")
        components.html(html_path.read_text(encoding="utf-8"), height=780, scrolling=False)
        st.code(str(html_path), language="text")
    else:
        st.info("还没有生成 HTML 图谱。请点击上方“生成可视化 HTML”。")

    with st.expander("节点 CSV", expanded=False):
        st.dataframe(nodes, use_container_width=True)
    with st.expander("关系 CSV", expanded=False):
        st.dataframe(edges, use_container_width=True)
else:
    st.info("尚未生成图谱 CSV。请先点击“重建图谱 CSV”。")
