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
from backend.services.design_graph import build_design_graph
from backend.services.graph_builder import build_graph
from backend.services.graph_visualizer import export_graph_html


st.set_page_config(page_title="知识图谱浏览", layout="wide")
st.title("知识图谱浏览")
st.caption("浏览可缩放、可点击、可搜索的 HfO2 知识图谱和设计图谱。")

kg_nodes_path = PROJECT_ROOT / "data" / "graph" / "nodes.csv"
kg_edges_path = PROJECT_ROOT / "data" / "graph" / "edges.csv"
kg_html_path = PROJECT_ROOT / "data" / "exports" / "hfo2_knowledge_graph.html"

design_nodes_path = PROJECT_ROOT / "data" / "design_graph" / "nodes.csv"
design_edges_path = PROJECT_ROOT / "data" / "design_graph" / "edges.csv"
design_html_path = PROJECT_ROOT / "data" / "exports" / "hfo2_design_graph.html"

tab_kg, tab_design = st.tabs(["知识图谱", "设计图谱"])


def _graph_panel(
    *,
    title: str,
    nodes_path: Path,
    edges_path: Path,
    html_path: Path,
    rebuild_csv,
    rebuild_html,
) -> None:
    actions = st.columns([1, 1, 1, 1.4])
    with actions[0]:
        if st.button(f"重建 {title} CSV", use_container_width=True):
            stats = rebuild_csv()
            st.success(f"已重建：{stats.get('nodes', 0)} 个节点，{stats.get('edges', 0)} 条关系。")
    with actions[1]:
        if st.button(f"生成 {title} HTML", use_container_width=True):
            stats = rebuild_html()
            st.success(f"已生成：{stats.get('output_path') or stats.get('html_path')}")
    with actions[2]:
        if html_path.exists():
            st.download_button(
                f"下载 {title} HTML",
                html_path.read_bytes(),
                file_name=html_path.name,
                mime="text/html",
                use_container_width=True,
            )
        else:
            st.button(f"下载 {title} HTML", disabled=True, use_container_width=True)
    with actions[3]:
        if html_path.exists():
            st.link_button(f"新窗口打开 {title}", html_path.resolve().as_uri(), use_container_width=True)
        else:
            st.button(f"新窗口打开 {title}", disabled=True, use_container_width=True)

    if nodes_path.exists() and edges_path.exists():
        nodes = pd.read_csv(nodes_path)
        edges = pd.read_csv(edges_path)
        m1, m2, m3 = st.columns(3)
        m1.metric("节点", len(nodes))
        m2.metric("关系", len(edges))
        m3.metric("节点类型", nodes["type"].nunique() if "type" in nodes else 0)

        if html_path.exists():
            st.subheader(f"{title} 可视化窗口")
            st.caption("滚轮缩放，拖动画布平移，点击节点查看详情；左侧面板可搜索材料、DOI、性能或证据句。")
            components.html(html_path.read_text(encoding="utf-8"), height=780, scrolling=False)
            st.code(str(html_path), language="text")
        else:
            st.info("还没有生成 HTML 图谱。请点击上方按钮生成。")

        with st.expander("节点 CSV", expanded=False):
            st.dataframe(nodes, use_container_width=True)
        with st.expander("关系 CSV", expanded=False):
            st.dataframe(edges, use_container_width=True)
    else:
        st.info("尚未生成图谱 CSV。请先点击上方重建按钮。")


with tab_kg:
    _graph_panel(
        title="知识图谱",
        nodes_path=kg_nodes_path,
        edges_path=kg_edges_path,
        html_path=kg_html_path,
        rebuild_csv=build_graph,
        rebuild_html=export_graph_html,
    )

with tab_design:
    _graph_panel(
        title="设计图谱",
        nodes_path=design_nodes_path,
        edges_path=design_edges_path,
        html_path=design_html_path,
        rebuild_csv=build_design_graph,
        rebuild_html=build_design_graph,
    )
