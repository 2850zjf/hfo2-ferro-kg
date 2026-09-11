from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import quote

import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


GLOBAL_NAV_ITEMS = [
    ("首页", "/"),
    ("文献处理", "/PDF_解析"),
    ("抽取审核", "/抽取结果审核"),
    ("图谱浏览", "/知识图谱浏览"),
    ("RAG 问答", "/RAG_问答"),
    ("数据看板", "/数据分析看板"),
    ("任务监控", "/任务实时监控"),
    ("设计工作流", "/材料设计工作流?view=overview"),
]


def _streamlit_href(path: str) -> str:
    if path == "/":
        return path
    if "?" not in path:
        return "/" + quote(path.lstrip("/"), safe="")
    route, query = path.split("?", 1)
    return "/" + quote(route.lstrip("/"), safe="") + "?" + query


def render_top_nav(active: str = "") -> None:
    st.markdown(
        """
        <style>
        header[data-testid="stHeader"],
        [data-testid="stHeader"],
        [data-testid="stToolbar"],
        #MainMenu,
        footer {
            display: none !important;
            visibility: hidden !important;
            height: 0 !important;
        }
        [data-testid="stSidebar"],
        section[data-testid="stSidebar"],
        [data-testid="collapsedControl"] {
            display: none !important;
        }
        .block-container,
        [data-testid="block-container"],
        [data-testid="stMainBlockContainer"] {
            width: min(1880px, calc(100vw - 56px)) !important;
            max-width: none !important;
            padding-top: 0.78rem !important;
            padding-left: 0 !important;
            padding-right: 0 !important;
            padding-bottom: 2.2rem !important;
        }
        .st-key-ferro_global_nav {
            position: sticky;
            top: 0;
            z-index: 1000;
            margin: 0 0 1rem 0;
            padding: 0.42rem 0.52rem;
            border: 1px solid rgba(197, 211, 226, 0.92);
            border-radius: 8px;
            background: rgba(255, 255, 255, 0.94);
            backdrop-filter: blur(20px);
            box-shadow: 0 10px 28px rgba(33, 41, 62, 0.07);
        }
        .st-key-ferro_global_nav [data-testid="stHorizontalBlock"] {
            align-items: center;
            gap: 0.36rem;
        }
        .ferro-global-brand p {
            margin: 0;
            color: #20314f;
            font-size: 0.92rem;
            font-weight: 840;
            white-space: nowrap;
            padding: 0 0.12rem;
        }
        .st-key-ferro_global_nav [data-testid="stLinkButton"] a,
        .st-key-ferro_global_nav a[data-testid="stLinkButton"] {
            width: 100%;
            min-height: 2rem;
            padding: 0 0.52rem;
            border: 1px solid #cbd8e6;
            border-radius: 8px;
            background: rgba(255, 255, 255, 0.92);
            color: #334155 !important;
            text-decoration: none !important;
            font-weight: 740;
            justify-content: center;
        }
        .st-key-ferro_global_nav [data-testid="stLinkButton"] a p,
        .st-key-ferro_global_nav a[data-testid="stLinkButton"] p {
            font-size: 0.82rem;
            line-height: 1.1;
            white-space: nowrap;
        }
        .st-key-ferro_global_nav [data-testid="stLinkButton"] a:hover,
        .st-key-ferro_global_nav a[data-testid="stLinkButton"]:hover {
            border-color: #9fb9d2;
            background: #edf4f8;
            color: #20314f !important;
        }
        .st-key-ferro_global_nav [data-testid="stLinkButton"] a[aria-disabled="true"],
        .st-key-ferro_global_nav [data-testid="stLinkButton"] a[disabled],
        .st-key-ferro_global_nav a[data-testid="stLinkButton"][aria-disabled="true"],
        .st-key-ferro_global_nav a[data-testid="stLinkButton"][disabled] {
            border-color: #9fb9d2;
            background: #eaf3f7;
            color: #20314f !important;
            box-shadow: inset 0 -2px 0 #84a7cd;
        }
        @media (max-width: 980px) {
            .block-container,
            [data-testid="block-container"],
            [data-testid="stMainBlockContainer"] {
                width: calc(100vw - 24px) !important;
            }
            .st-key-ferro_global_nav {
                overflow-x: auto;
                scrollbar-width: thin;
            }
            .st-key-ferro_global_nav [data-testid="stHorizontalBlock"] {
                min-width: 920px;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key="ferro_global_nav"):
        columns = st.columns([1.05, 0.72, 0.88, 0.88, 0.88, 0.88, 0.88, 0.88, 1.0], gap="small")
        with columns[0]:
            st.markdown('<div class="ferro-global-brand">HfO2-FerroKG</div>', unsafe_allow_html=True)
        for column, (label, path) in zip(columns[1:], GLOBAL_NAV_ITEMS):
            with column:
                st.link_button(
                    label=label,
                    url=_streamlit_href(path),
                    disabled=active == label,
                    width="stretch",
                )
