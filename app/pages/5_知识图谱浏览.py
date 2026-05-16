from __future__ import annotations

import pandas as pd
import streamlit as st

from backend.core.config import PROJECT_ROOT
from backend.services.graph_builder import build_graph


st.set_page_config(page_title="知识图谱浏览", layout="wide")
st.title("知识图谱浏览")

if st.button("基于机器预审核事实构建图谱 CSV"):
    st.success(build_graph())

nodes_path = PROJECT_ROOT / "data" / "graph" / "nodes.csv"
edges_path = PROJECT_ROOT / "data" / "graph" / "edges.csv"

if nodes_path.exists() and edges_path.exists():
    nodes = pd.read_csv(nodes_path)
    edges = pd.read_csv(edges_path)
    st.metric("节点", len(nodes))
    st.metric("边", len(edges))
    st.subheader("节点")
    st.dataframe(nodes, use_container_width=True)
    st.subheader("边")
    st.dataframe(edges, use_container_width=True)
else:
    st.info("尚未生成图谱 CSV。")
