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


st.set_page_config(page_title="知识图谱浏览", layout="wide", initial_sidebar_state="collapsed")

st.markdown(
    """
    <style>
    :root {
        --kg-bg: #F8F5FD;
        --kg-panel: rgba(255, 255, 255, 0.84);
        --kg-panel-2: rgba(248, 245, 253, 0.76);
        --kg-border: rgba(118, 126, 145, 0.20);
        --kg-text: #252936;
        --kg-muted: #6F7481;
        --kg-cyan: #9DDFE4;
        --kg-blue: #84A7CD;
        --kg-green: #A5CAB5;
        --kg-amber: #F5CED0;
        --kg-red: #7F6E9C;
    }
    .stApp {
        background:
          radial-gradient(circle at 14% 10%, rgba(218, 240, 244, 0.74), transparent 28%),
          radial-gradient(circle at 82% 18%, rgba(235, 216, 234, 0.64), transparent 30%),
          linear-gradient(135deg, #F8F5FD 0%, #F6FAFC 52%, #EFE8E8 100%);
        color: var(--kg-text);
    }
    header[data-testid="stHeader"] { background: transparent; }
    .block-container {
        max-width: 1680px;
        padding-top: 1.35rem;
        padding-left: 1rem;
        padding-right: 1rem;
        padding-bottom: 2rem;
    }
    [data-testid="collapsedControl"] {
        display: flex;
    }
    [data-testid="stSidebar"] {
        background: rgba(238, 243, 251, 0.92);
        border-right: 1px solid rgba(23, 32, 42, 0.08);
    }
    .kg-hero {
        border: 1px solid var(--kg-border);
        background:
            linear-gradient(135deg, rgba(255, 255, 255, 0.92), rgba(248, 245, 253, 0.82)),
            linear-gradient(90deg, rgba(181, 214, 234, 0.28), rgba(235, 216, 234, 0.24));
        box-shadow: 0 24px 70px rgba(86, 93, 112, 0.14);
        border-radius: 8px;
        padding: 24px 26px;
        margin-bottom: 16px;
        position: relative;
        overflow: hidden;
    }
    .kg-hero:after {
        content: "";
        position: absolute;
        inset: 0;
        background-image:
            linear-gradient(rgba(132,167,205,0.10) 1px, transparent 1px),
            linear-gradient(90deg, rgba(132,167,205,0.10) 1px, transparent 1px);
        background-size: 28px 28px;
        mask-image: linear-gradient(90deg, transparent, black 22%, black 72%, transparent);
        pointer-events: none;
    }
    .kg-kicker {
        color: var(--kg-cyan);
        font-size: 12px;
        font-weight: 800;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        margin-bottom: 10px;
    }
    .kg-title {
        color: var(--kg-text);
        font-size: clamp(34px, 4vw, 58px);
        line-height: 1.02;
        font-weight: 850;
        letter-spacing: 0;
        margin: 0 0 12px;
    }
    .kg-subtitle {
        color: var(--kg-muted);
        font-size: 16px;
        line-height: 1.6;
        max-width: 940px;
        margin: 0;
    }
    .kg-chip-row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 18px; }
    .kg-chip {
        border: 1px solid rgba(132,167,205,0.32);
        background: rgba(255,255,255,0.58);
        color: #4D6176;
        border-radius: 999px;
        padding: 6px 10px;
        font-size: 12px;
        font-weight: 700;
    }
    .kg-panel {
        border: 1px solid var(--kg-border);
        background: var(--kg-panel);
        border-radius: 8px;
        padding: 16px;
        box-shadow: 0 18px 48px rgba(86, 93, 112, 0.14);
        backdrop-filter: blur(18px);
    }
    .kg-panel h3 {
        margin: 0 0 8px;
        color: var(--kg-text);
        font-size: 18px;
    }
    .kg-note { color: var(--kg-muted); font-size: 13px; line-height: 1.55; }
    .kg-metric {
        border: 1px solid var(--kg-border);
        background: var(--kg-panel-2);
        border-radius: 8px;
        min-height: 96px;
        padding: 14px;
    }
    .kg-metric-label { color: var(--kg-muted); font-size: 12px; font-weight: 750; margin-bottom: 8px; }
    .kg-metric-value { color: var(--kg-text); font-size: 31px; font-weight: 850; line-height: 1; }
    .kg-metric-note { color: var(--kg-muted); font-size: 12px; margin-top: 8px; }
    div[data-testid="stMetric"] {
        border: 1px solid var(--kg-border);
        background: var(--kg-panel-2);
        border-radius: 8px;
        padding: 12px 14px;
    }
    div[data-testid="stMetricLabel"] { color: var(--kg-muted); }
    div[data-testid="stMetricValue"] { color: var(--kg-text); font-size: 30px; font-weight: 850; }
    .stButton > button, .stDownloadButton > button, .stLinkButton > a {
        border-radius: 8px !important;
        border: 1px solid rgba(132,167,205,0.32) !important;
        background: rgba(255, 255, 255, 0.72) !important;
        color: #252936 !important;
        min-height: 46px;
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.06);
    }
    .stButton > button:hover, .stDownloadButton > button:hover, .stLinkButton > a:hover {
        border-color: rgba(132,167,205,0.68) !important;
        background: rgba(242, 246, 250, 0.96) !important;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        border-bottom: 1px solid rgba(139,168,214,0.18);
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px 8px 0 0;
        color: var(--kg-muted);
        font-weight: 800;
    }
    iframe { border-radius: 8px; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <section class="kg-hero">
      <div class="kg-kicker">HfO2 文献知识图谱</div>
      <h1 class="kg-title">HfO2-FerroKG 图谱探索台</h1>
      <p class="kg-subtitle">
        查看 HfO2/HZO 文献中的材料体系、工艺条件、相结构、器件与 Pr/2Pr 性能证据；
        通过设计图谱把样品级事实整理成可筛选、可追溯、可建模的材料设计线索。
      </p>
      <div class="kg-chip-row">
        <span class="kg-chip">语义检索</span>
        <span class="kg-chip">邻域聚焦</span>
        <span class="kg-chip">证据链追踪</span>
        <span class="kg-chip">样品-性能关联</span>
        <span class="kg-chip">Pr / 2Pr 数据集</span>
      </div>
    </section>
    """,
    unsafe_allow_html=True,
)

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
    if nodes_path.exists() and edges_path.exists():
        nodes = pd.read_csv(nodes_path)
        edges = pd.read_csv(edges_path)
        type_count = nodes["type"].nunique() if "type" in nodes else 0
        relation_count = edges["type"].nunique() if "type" in edges else 0
        top_types = []
        if "type" in nodes:
            top_types = [f"{idx} {val}" for idx, val in nodes["type"].value_counts().head(5).items()]

        st.markdown(
            f"""
            <div class="kg-panel">
              <h3>{title} 探索视图</h3>
              <div class="kg-note">
                图谱已按节点类型分层布局。进入画布后可搜索材料、DOI、性能值或证据句；
                点击节点后可切换到邻域视图，只保留与当前节点直接相连的证据链。
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if html_path.exists():
            view_actions = st.columns([1.15, 1, 1, 1])
            with view_actions[0]:
                st.link_button(f"打开 {title} 大画布", html_path.resolve().as_uri(), use_container_width=True)
            with view_actions[1]:
                st.download_button(
                    f"下载 {title} HTML",
                    html_path.read_bytes(),
                    file_name=html_path.name,
                    mime="text/html",
                    use_container_width=True,
                )
            with view_actions[2]:
                if st.button(f"生成 {title} HTML", use_container_width=True):
                    stats = rebuild_html()
                    st.success(f"已生成：{stats.get('output_path') or stats.get('html_path')}")
            with view_actions[3]:
                if st.button(f"重建 {title} CSV", use_container_width=True):
                    stats = rebuild_csv()
                    st.success(f"已重建：{stats.get('nodes', 0)} 个节点，{stats.get('edges', 0)} 条关系。")
            components.html(html_path.read_text(encoding="utf-8"), height=1120, scrolling=False)
        else:
            st.info("还没有生成 HTML 图谱。请点击上方按钮生成。")

        with st.expander("图谱结构摘要", expanded=False):
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("节点", f"{len(nodes):,}")
            m2.metric("关系", f"{len(edges):,}")
            m3.metric("节点类型", f"{type_count:,}")
            m4.metric("关系类型", f"{relation_count:,}")
            st.write("主要节点类型：", " / ".join(top_types) if top_types else "暂无")

        with st.expander("节点 CSV 原始表", expanded=False):
            st.dataframe(nodes, use_container_width=True)
        with st.expander("关系 CSV 原始表", expanded=False):
            st.dataframe(edges, use_container_width=True)
    else:
        st.info("尚未生成图谱 CSV。请先点击下方重建按钮。")
        if st.button(f"重建 {title} CSV", use_container_width=True):
            stats = rebuild_csv()
            st.success(f"已重建：{stats.get('nodes', 0)} 个节点，{stats.get('edges', 0)} 条关系。")


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
