from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import fitz
import pandas as pd
import streamlit as st

PROJECT_ROOT_LOCAL = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT_LOCAL) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_LOCAL))

from backend.core.config import PROJECT_ROOT
from backend.services.active_learning import recommend_active_learning_candidates
from backend.services.computation_planner import plan_computational_feedback_tasks
from backend.services.computation_validation import (
    computation_job_summary,
    list_computation_jobs,
    prepare_computation_validation_jobs,
)
from backend.services.computation_workflow import run_computation_workflow
from backend.services.design_dataset import build_benchmark_tier_datasets, build_design_dataset
from backend.services.design_graph import build_design_graph
from backend.services.design_model import load_design_model_metrics, train_design_models, train_tiered_design_models
from backend.services.design_report import export_design_progress_report
from backend.services.llm_extractor import llm_status
from backend.services.llm_quota_guard import clear_llm_pause, read_llm_pause
from backend.services.progress_monitor import active_pipeline_processes, monitor_snapshot
from backend.services.simulation_runtime import (
    check_simulation_runtime,
    load_simulation_runtime_status,
)
from backend.services.workflow_runner import start_monitor_server, start_protected_full_pipeline
from app.support import render_top_nav


st.set_page_config(page_title="材料设计工作流", layout="wide", initial_sidebar_state="collapsed")


def _inject_workflow_theme() -> None:
    st.markdown(
        """
        <style>
        :root {
            --kg-bg: #f7f9fc;
            --kg-panel: rgba(255, 255, 255, 0.94);
            --kg-panel-2: #f1f5f9;
            --kg-border: #d7dee9;
            --kg-text: #202433;
            --kg-muted: #667085;
            --kg-soft-blue: #8bacdd;
            --kg-blue: #4d77a7;
            --kg-teal: #9ddfe4;
            --kg-lavender: #b2a4cf;
            --kg-rose: #f5ced0;
            --kg-shadow: 0 18px 48px rgba(33, 41, 62, 0.10);
        }
        html {
            scroll-behavior: smooth;
        }
        body,
        [data-testid="stAppViewContainer"] {
            background:
                linear-gradient(180deg, rgba(226,244,243,0.78) 0, rgba(247,249,252,0.96) 260px, #f7f9fc 100%);
            color: var(--kg-text);
        }
        header[data-testid="stHeader"],
        [data-testid="stHeader"],
        [data-testid="stToolbar"] {
            display: none !important;
            visibility: hidden !important;
            height: 0 !important;
        }
        [data-testid="stSidebar"] {
            display: none !important;
        }
        [data-testid="collapsedControl"] {
            display: none !important;
        }
        .block-container,
        [data-testid="block-container"],
        [data-testid="stMainBlockContainer"] {
            width: min(1880px, calc(100vw - 56px)) !important;
            max-width: none !important;
            padding-top: 0.78rem;
            padding-left: 0 !important;
            padding-right: 0 !important;
        }
        h1, h2, h3, h4 {
            letter-spacing: 0;
            color: var(--kg-text);
        }
        h2 {
            padding-top: 0.8rem;
            font-size: 1.55rem;
        }
        h3 {
            font-size: 1.12rem;
        }
        div[data-testid="stMetric"] {
            background: var(--kg-panel);
            border: 1px solid var(--kg-border);
            border-radius: 8px;
            padding: 0.88rem 1rem;
            box-shadow: 0 8px 24px rgba(33, 41, 62, 0.06);
        }
        div[data-testid="stMetric"] label {
            color: var(--kg-muted);
            font-weight: 650;
        }
        div[data-testid="stMetric"] [data-testid="stMetricValue"] {
            color: var(--kg-text);
            font-weight: 760;
        }
        div[data-testid="stDataFrame"],
        div[data-testid="stTable"],
        div[data-testid="stExpander"],
        div[data-testid="stTextArea"],
        div[data-testid="stImage"] {
            border-radius: 8px;
        }
        .stButton > button,
        .stDownloadButton > button,
        a[data-testid="stLinkButton"] {
            border-radius: 8px !important;
            border-color: #c8d3e1 !important;
            font-weight: 650 !important;
        }
        .stButton > button[kind="primary"],
        .stDownloadButton > button[kind="primary"] {
            background: #4d77a7 !important;
            border-color: #4d77a7 !important;
        }
        .workflow-shell {
            margin: 0.15rem 0 0.65rem 0;
        }
        .workflow-hero {
            position: relative;
            overflow: hidden;
            border: 1px solid var(--kg-border);
            border-radius: 8px;
            background:
                linear-gradient(90deg, rgba(255,255,255,0.96), rgba(246,249,253,0.96)),
                repeating-linear-gradient(90deg, rgba(77,119,167,0.08) 0, rgba(77,119,167,0.08) 1px, transparent 1px, transparent 42px),
                repeating-linear-gradient(0deg, rgba(157,223,228,0.08) 0, rgba(157,223,228,0.08) 1px, transparent 1px, transparent 42px);
            padding: 1.05rem 1.2rem 0.95rem 1.2rem;
            box-shadow: 0 10px 28px rgba(33, 41, 62, 0.07);
        }
        .workflow-eyebrow {
            color: #6d9aa5;
            font-size: 0.76rem;
            letter-spacing: 0;
            font-weight: 800;
            text-transform: uppercase;
            margin-bottom: 0.35rem;
        }
        .workflow-hero h1 {
            margin: 0;
            font-size: 2.55rem;
            line-height: 1.04;
            font-weight: 820;
        }
        .workflow-hero p {
            max-width: 980px;
            margin: 0.55rem 0 0 0;
            color: var(--kg-muted);
            font-size: 0.96rem;
            line-height: 1.62;
        }
        .workflow-pills {
            display: flex;
            flex-wrap: wrap;
            gap: 0.55rem;
            margin-top: 0.72rem;
        }
        .workflow-pill {
            border: 1px solid #d5e0ee;
            background: rgba(255,255,255,0.74);
            color: #536174;
            border-radius: 999px;
            padding: 0.38rem 0.68rem;
            font-size: 0.82rem;
            font-weight: 700;
        }
        .workflow-topnav,
        .workflow-viewnav {
            position: sticky;
            top: 2.75rem;
            z-index: 997;
            display: flex;
            gap: 0.5rem;
            overflow-x: auto;
            white-space: nowrap;
            margin: 0.65rem 0 0.8rem 0;
            padding: 0.38rem;
            border: 1px solid rgba(215, 222, 233, 0.92);
            border-radius: 8px;
            background: rgba(255,255,255,0.88);
            backdrop-filter: blur(18px);
            box-shadow: 0 12px 34px rgba(33, 41, 62, 0.08);
        }
        .workflow-topnav a {
            display: inline-flex;
            align-items: center;
            height: 2.18rem;
            padding: 0 0.7rem;
            color: #475467 !important;
            text-decoration: none !important;
            border: 1px solid transparent;
            border-radius: 8px;
            font-weight: 740;
            font-size: 0.9rem;
        }
        .workflow-topnav a:hover,
        .workflow-viewnav a:hover {
            color: #25344d !important;
            background: #edf4f8;
            border-color: #cddbea;
        }
        .workflow-viewnav {
            position: sticky;
            top: 0.8rem;
            margin: 0.65rem 0 0.8rem 0;
            background: rgba(255,255,255,0.92);
        }
        .workflow-viewnav a {
            display: inline-flex;
            align-items: center;
            height: 2.18rem;
            padding: 0 0.7rem;
            color: #475467 !important;
            text-decoration: none !important;
            border: 1px solid transparent;
            border-radius: 8px;
            font-weight: 740;
            font-size: 0.9rem;
        }
        .workflow-viewnav a.active {
            color: #20314f !important;
            background: #eaf3f7;
            border-color: #b9cfe1;
        }
        .workflow-module-caption {
            color: #718198;
            font-size: 0.72rem;
            font-weight: 780;
            margin: 0.66rem 0 0.35rem 0.1rem;
            text-transform: uppercase;
        }
        .workflow-module-row {
            border: 1px solid rgba(215, 222, 233, 0.92);
            border-radius: 8px;
            background: rgba(255,255,255,0.92);
            padding: 0.36rem;
            box-shadow: 0 10px 28px rgba(33, 41, 62, 0.06);
        }
        .section-anchor {
            scroll-margin-top: 7.2rem;
            height: 1px;
        }
        .section-title {
            display: flex;
            align-items: flex-end;
            justify-content: space-between;
            gap: 1rem;
            margin: 0.95rem 0 0.45rem 0;
        }
        .section-title h2 {
            margin: 0;
            padding: 0;
        }
        .section-title span {
            color: var(--kg-muted);
            font-size: 0.9rem;
        }
        .problem-card {
            min-height: 112px;
            border: 1px solid var(--kg-border);
            border-radius: 8px;
            padding: 0.82rem 0.9rem;
            background: var(--kg-panel);
            box-shadow: 0 5px 16px rgba(33, 41, 62, 0.04);
        }
        .problem-card .kicker {
            width: 2.4rem;
            height: 0.22rem;
            border-radius: 8px;
            background: var(--kg-soft-blue);
            margin-bottom: 0.58rem;
        }
        .problem-card h3 {
            margin: 0 0 0.35rem 0;
            font-size: 0.98rem;
        }
        .problem-card p {
            margin: 0;
            color: var(--kg-muted);
            line-height: 1.52;
            font-size: 0.88rem;
        }
        .workflow-stage {
            display: grid;
            grid-template-columns: 4.6rem 1fr;
            gap: 0.7rem;
            align-items: start;
            min-height: 112px;
            border: 1px solid var(--kg-border);
            border-radius: 8px;
            padding: 0.92rem;
            background: var(--kg-panel);
            box-shadow: 0 8px 24px rgba(33, 41, 62, 0.04);
        }
        .workflow-stage .num {
            display: flex;
            align-items: center;
            justify-content: center;
            width: 3.1rem;
            height: 3.1rem;
            border-radius: 8px;
            background: #edf4f8;
            color: #4d77a7;
            font-weight: 820;
        }
        .workflow-stage h3 {
            margin: 0 0 0.35rem 0;
            font-size: 0.98rem;
        }
        .workflow-stage p {
            margin: 0 0 0.45rem 0;
            color: var(--kg-muted);
            line-height: 1.52;
            font-size: 0.88rem;
        }
        .workflow-stage code {
            color: #536174;
            background: #f1f5f9;
            border: 1px solid #dce5ef;
            border-radius: 6px;
            padding: 0.1rem 0.32rem;
            white-space: normal;
        }
        .soft-panel-note {
            border: 1px solid #d5e0ee;
            border-radius: 8px;
            background: rgba(255,255,255,0.78);
            padding: 0.75rem 0.9rem;
            color: var(--kg-muted);
            line-height: 1.6;
        }
        .compact-card {
            min-height: 118px;
            border: 1px solid var(--kg-border);
            border-radius: 8px;
            background: var(--kg-panel);
            padding: 0.92rem;
            box-shadow: 0 5px 18px rgba(33, 41, 62, 0.04);
        }
        .compact-card h3 {
            margin: 0 0 0.35rem 0;
            font-size: 1rem;
        }
        .compact-card p {
            margin: 0;
            color: var(--kg-muted);
            line-height: 1.52;
            font-size: 0.88rem;
        }
        @media (max-width: 900px) {
            .block-container,
            [data-testid="block-container"],
            [data-testid="stMainBlockContainer"] {
                width: calc(100vw - 24px) !important;
                padding-left: 0 !important;
                padding-right: 0 !important;
            }
            .workflow-topnav {
                top: 2.3rem;
            }
            .workflow-hero {
                padding: 1.15rem;
            }
            .workflow-hero h1 {
                font-size: 2.1rem;
            }
            .workflow-stage {
                grid-template-columns: 1fr;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_hero() -> None:
    st.markdown(
        """
        <div class="workflow-shell" id="overview">
          <div class="workflow-hero">
            <div class="workflow-eyebrow">HfO2-FerroKG AI Materials Workbench</div>
            <h1>材料设计工作流</h1>
            <p>
              从 PDF 证据出发，把 HfO2/HZO 文献转化为样品级知识图谱、Pr/2Pr benchmark、
              预测模型验证、证据约束设计建议和计算反馈任务。
            </p>
            <div class="workflow-pills">
              <span class="workflow-pill">ontology-first extraction</span>
              <span class="workflow-pill">evidence trace</span>
              <span class="workflow-pill">Qwen 3.7 max</span>
              <span class="workflow-pill">benchmark tiers</span>
              <span class="workflow-pill">compute feedback</span>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _section_header(anchor: str, title: str, subtitle: str = "") -> None:
    st.markdown(
        f"""
        <div id="{anchor}" class="section-anchor"></div>
        <div class="section-title">
            <h2>{title}</h2>
            <span>{subtitle}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _run_background(command: list[str], prefix: str) -> dict[str, Any]:
    active = active_pipeline_processes()
    script_name = Path(command[1]).name if len(command) > 1 else ""
    if any(str(item.get("task", "")).endswith(script_name) for item in active):
        return {"started": False, "reason": "同类任务已经在后台运行"}
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_log = log_dir / f"{prefix}_{stamp}.out.log"
    err_log = log_dir / f"{prefix}_{stamp}.err.log"
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        stdout=out_log.open("w", encoding="utf-8"),
        stderr=err_log.open("w", encoding="utf-8"),
        creationflags=flags,
    )
    return {
        "started": True,
        "pid": process.pid,
        "stdout_log": str(out_log),
        "stderr_log": str(err_log),
    }


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def _metric(label: str, value: Any, help_text: str = "") -> None:
    st.metric(label, value if value not in (None, "") else "0", help=help_text or None)


def _start_pipeline_button(
    label: str,
    script: str,
    prefix: str,
    args: list[str] | None = None,
    primary: bool = False,
) -> None:
    if st.button(label, type="primary" if primary else "secondary", use_container_width=True):
        command = [sys.executable, script, *(args or [])]
        result = _run_background(command, prefix)
        if result.get("started"):
            st.success(f"已启动：PID {result['pid']}")
            st.caption(f"日志：{result['stdout_log']}")
        else:
            st.warning(result.get("reason", "没有启动"))


@st.cache_data(ttl=30)
def _preview_pdf_records(search_query: str = "", limit: int = 1200) -> list[dict[str, Any]]:
    from backend.db.session import connect

    query_text = " ".join(search_query.strip().split())
    with connect() as conn:
        if query_text:
            like = f"%{query_text}%"
            rows = conn.execute(
                """
                SELECT pf.pdf_id, pf.paper_id, pf.file_name, pf.file_path, pf.page_count, pf.parse_status,
                       p.title, p.doi, p.year,
                       (
                         SELECT MIN(pp.page_number)
                         FROM parsed_pages pp
                         WHERE pp.pdf_id = pf.pdf_id AND pp.text LIKE ?
                       ) AS first_hit_page,
                       (
                         SELECT COUNT(*)
                         FROM parsed_pages pp
                         WHERE pp.pdf_id = pf.pdf_id AND pp.text LIKE ?
                       ) AS text_hit_pages
                FROM pdf_files pf
                LEFT JOIN papers p ON p.paper_id = pf.paper_id
                WHERE pf.parse_status IN ('parsed', 'manifested')
                  AND (
                    COALESCE(p.title, '') LIKE ?
                    OR COALESCE(p.doi, '') LIKE ?
                    OR COALESCE(CAST(p.year AS TEXT), '') LIKE ?
                    OR pf.file_name LIKE ?
                    OR pf.pdf_id LIKE ?
                    OR EXISTS (
                        SELECT 1
                        FROM parsed_pages pp
                        WHERE pp.pdf_id = pf.pdf_id AND pp.text LIKE ?
                    )
                  )
                ORDER BY
                  CASE WHEN COALESCE(p.title, '') LIKE ? THEN 0 ELSE 1 END,
                  COALESCE(first_hit_page, 999999),
                  COALESCE(p.title, pf.file_name),
                  pf.file_name
                LIMIT ?
                """,
                (like, like, like, like, like, like, like, like, like, int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT pf.pdf_id, pf.paper_id, pf.file_name, pf.file_path, pf.page_count, pf.parse_status,
                       p.title, p.doi, p.year,
                       NULL AS first_hit_page,
                       0 AS text_hit_pages
                FROM pdf_files pf
                LEFT JOIN papers p ON p.paper_id = pf.paper_id
                WHERE pf.parse_status IN ('parsed', 'manifested')
                ORDER BY COALESCE(p.title, pf.file_name), pf.file_name
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
    return [dict(row) for row in rows]


@st.cache_data(ttl=30)
def _preview_page_text(pdf_id: str, page_number: int) -> str:
    from backend.db.session import connect

    with connect() as conn:
        row = conn.execute(
            """
            SELECT text
            FROM parsed_pages
            WHERE pdf_id = ? AND page_number = ?
            """,
            (pdf_id, page_number),
        ).fetchone()
    return str(row["text"] or "") if row else ""


@st.cache_data(ttl=30)
def _preview_page_tables(pdf_id: str, page_number: int) -> list[dict[str, Any]]:
    from backend.db.session import connect

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT table_id, table_index, row_count, col_count, table_text, table_json
            FROM pdf_tables
            WHERE pdf_id = ? AND page_number = ?
            ORDER BY table_index
            """,
            (pdf_id, page_number),
        ).fetchall()
    return [dict(row) for row in rows]


@st.cache_data(ttl=30)
def _preview_search_snippets(pdf_id: str, search_query: str, limit: int = 8) -> list[dict[str, Any]]:
    query_text = " ".join(search_query.strip().split())
    if not query_text:
        return []

    from backend.db.session import connect

    like = f"%{query_text}%"
    snippets: list[dict[str, Any]] = []
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT page_number, text
            FROM parsed_pages
            WHERE pdf_id = ? AND text LIKE ?
            ORDER BY page_number
            LIMIT ?
            """,
            (pdf_id, like, int(limit)),
        ).fetchall()
    lowered_query = query_text.lower()
    for row in rows:
        text = str(row["text"] or "")
        pos = text.lower().find(lowered_query)
        if pos == -1:
            pos = 0
        left = max(0, pos - 160)
        right = min(len(text), pos + len(query_text) + 260)
        snippet = " ".join(text[left:right].split())
        snippets.append({"page_number": row["page_number"], "snippet": snippet})
    return snippets


@st.cache_data(ttl=30)
def _preview_ocr_markdown_records() -> list[dict[str, Any]]:
    root = PROJECT_ROOT / "data" / "paddleocr"
    if not root.exists():
        return []

    records: list[dict[str, Any]] = []
    for manifest_path in sorted(root.rglob("manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
        output_dir = manifest_path.parent
        for md_path in sorted(output_dir.rglob("*.md")):
            records.append(
                {
                    "label": f"{output_dir.name} / {md_path.relative_to(output_dir)}",
                    "path": str(md_path),
                    "source": str(manifest.get("source") or ""),
                    "job_id": str(manifest.get("job_id") or ""),
                    "pages": manifest.get("pages", ""),
                    "mtime": md_path.stat().st_mtime,
                }
            )
    records.sort(key=lambda item: item["mtime"], reverse=True)
    return records


def _render_pdf_page(pdf_path: Path, page_number: int, zoom: float) -> tuple[bytes | None, str | None, int | None]:
    try:
        with fitz.open(pdf_path) as doc:
            page_count = doc.page_count
            safe_page = min(max(page_number, 1), page_count)
            page = doc.load_page(safe_page - 1)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            return pixmap.tobytes("png"), None, page_count
    except Exception as exc:
        return None, str(exc), None


@st.cache_data(ttl=30)
def _selected_pdf_processing_counts(pdf_id: str) -> dict[str, int]:
    from backend.db.session import connect

    with connect() as conn:
        chunks = conn.execute(
            "SELECT COUNT(*) FROM document_chunks WHERE pdf_id = ?",
            (pdf_id,),
        ).fetchone()[0]
        high_value_chunks = conn.execute(
            "SELECT COUNT(*) FROM document_chunks WHERE pdf_id = ? AND is_high_value = 1",
            (pdf_id,),
        ).fetchone()[0]
        candidates = conn.execute(
            "SELECT COUNT(DISTINCT chunk_id) FROM extraction_candidates WHERE pdf_id = ?",
            (pdf_id,),
        ).fetchone()[0]
        facts = conn.execute(
            "SELECT COUNT(*) FROM reviewed_facts WHERE pdf_id = ?",
            (pdf_id,),
        ).fetchone()[0]
    return {
        "chunks": int(chunks),
        "high_value_chunks": int(high_value_chunks),
        "extracted_chunks": int(candidates),
        "reviewed_facts": int(facts),
    }


def _write_pdf_chunk_queue(pdf_id: str, high_value_only: bool) -> dict[str, Any]:
    from backend.db.session import connect

    queue_dir = PROJECT_ROOT / "data" / "extraction_queues" / "single_paper"
    queue_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    queue_path = queue_dir / f"{pdf_id}_{'high_value' if high_value_only else 'all'}_{stamp}.csv"

    where = "WHERE pdf_id = ?"
    params: list[Any] = [pdf_id]
    if high_value_only:
        where += " AND is_high_value = 1"
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT chunk_id, paper_id, pdf_id, page_number, chunk_index, is_high_value
            FROM document_chunks
            {where}
            ORDER BY chunk_index
            """,
            params,
        ).fetchall()
    df = pd.DataFrame([dict(row) for row in rows])
    if df.empty:
        queue_path.write_text("chunk_id\n", encoding="utf-8")
    else:
        df.to_csv(queue_path, index=False)
    return {"path": queue_path, "chunks": len(df)}


@st.cache_data(ttl=8, show_spinner=False)
def _cached_monitor_snapshot() -> dict[str, Any]:
    return monitor_snapshot()


@st.cache_data(ttl=8, show_spinner=False)
def _cached_llm_pause() -> dict[str, Any] | None:
    return read_llm_pause()


WORKFLOW_VIEWS = [
    ("overview", "总览"),
    ("evidence", "证据预览"),
    ("control", "执行控制"),
    ("reliability", "可靠性"),
    ("results", "结果产物"),
    ("compute", "计算闭环"),
]


def _query_value(name: str) -> str | None:
    value = st.query_params.get(name)
    if isinstance(value, list):
        return str(value[0]) if value else None
    return str(value) if value is not None else None


def _current_workflow_view() -> str:
    allowed = {key for key, _ in WORKFLOW_VIEWS}
    query_value = _query_value("view")
    if query_value in allowed:
        st.session_state["workflow_selected_view"] = query_value
        return query_value
    session_value = st.session_state.get("workflow_selected_view")
    if session_value in allowed:
        return str(session_value)
    st.session_state["workflow_selected_view"] = "overview"
    return "overview"


def _render_workflow_view_nav(current_view: str) -> str:
    st.markdown('<div class="workflow-module-caption">Workflow Modules</div>', unsafe_allow_html=True)
    cols = st.columns(len(WORKFLOW_VIEWS), gap="small")
    selected_view = current_view
    for col, (key, label) in zip(cols, WORKFLOW_VIEWS):
        with col:
            button_type = "primary" if key == current_view else "secondary"
            if st.button(
                label,
                key=f"workflow_view_button_{key}",
                type=button_type,
                use_container_width=True,
            ):
                selected_view = key
    if selected_view != current_view:
        st.session_state["workflow_selected_view"] = selected_view
        st.query_params.clear()
        st.query_params["view"] = selected_view
        st.rerun()
    return selected_view


def _workflow_paths() -> dict[str, Path]:
    phase_output = PROJECT_ROOT / "outputs" / "phase_smoke"
    phase_data = PROJECT_ROOT / "data" / "computation" / "tefs_hfo2_phase_smoke_20260622"
    return {
        "dataset": PROJECT_ROOT / "data" / "design" / "hfo2_design_dataset.csv",
        "candidates": PROJECT_ROOT / "data" / "design" / "active_learning_candidates.csv",
        "design_graph_html": PROJECT_ROOT / "data" / "exports" / "hfo2_design_graph.html",
        "report": PROJECT_ROOT / "data" / "exports" / "hfo2_design_progress_report.md",
        "model_compare_csv": PROJECT_ROOT / "models" / "model_comparison" / "strong_relevant_model_comparison.csv",
        "model_compare_report": PROJECT_ROOT / "models" / "model_comparison" / "strong_relevant_model_comparison.md",
        "reliability_summary": PROJECT_ROOT / "data" / "exports" / "model_validation_summary.csv",
        "reliability_details": PROJECT_ROOT / "data" / "exports" / "model_validation_details.csv",
        "physics_dataset": PROJECT_ROOT / "data" / "design" / "hfo2_design_dataset_physics_constrained.csv",
        "cv_summary": PROJECT_ROOT / "models" / "model_cross_validation" / "cross_validation_summary.csv",
        "cv_report": PROJECT_ROOT / "models" / "model_cross_validation" / "cross_validation_report.md",
        "physics_plan": PROJECT_ROOT / "docs" / "physics_knowledge_base_plan.md",
        "computation_tasks": PROJECT_ROOT / "data" / "computation" / "computational_feedback_tasks.csv",
        "computation_report": PROJECT_ROOT / "data" / "computation" / "computational_feedback_plan.md",
        "computation_jobs": PROJECT_ROOT / "data" / "computation" / "validation_jobs" / "computation_jobs.csv",
        "computation_jobs_report": PROJECT_ROOT
        / "data"
        / "computation"
        / "validation_jobs"
        / "computation_validation_jobs.md",
        "computation_workflow_report": PROJECT_ROOT
        / "data"
        / "computation"
        / "workflow"
        / "computation_workflow_report.md",
        "simulation_runtime_status": PROJECT_ROOT
        / "data"
        / "computation"
        / "simulation_runtime"
        / "runtime_status.json",
        "phase_smoke_csv": phase_data / "hfo2_phase_smoke_results_normalized.csv",
        "computed_descriptors": PROJECT_ROOT / "data" / "computation" / "computed_descriptors.csv",
        "phase_smoke_chart": phase_output / "hfo2_phase_relative_energy.png",
        "phase_smoke_landscape": phase_output / "hfo2_phase_energy_landscape.png",
        "phase_smoke_report": phase_output / "hfo2_phase_smoke_summary.md",
        "phase_smoke_structural_csv": phase_data / "hfo2_phase_smoke_structural_results.csv",
        "phase_smoke_convergence_csv": phase_data / "hfo2_phase_smoke_relax_convergence.csv",
        "phase_smoke_structure_chart": phase_output / "hfo2_phase_relaxed_structures.png",
        "phase_smoke_convergence_chart": phase_output / "hfo2_phase_relax_convergence.png",
        "phase_smoke_volume_chart": phase_output / "hfo2_phase_final_volume.png",
        "phase_smoke_energy_volume_chart": phase_output / "hfo2_phase_energy_volume.png",
        "phase_smoke_real_report": phase_output / "hfo2_phase_real_vasp_results.md",
        "phase_smoke_vesta_zip": phase_output / "hfo2_phase_vesta_cif_bundle.zip",
        "phase_compute_validation_card": phase_output / "hfo2_phase_compute_validation_card.md",
        "phase_compute_validation_table": phase_output / "hfo2_phase_compute_validation_table.csv",
        "phase_compute_quality_gates": phase_output / "hfo2_phase_compute_quality_gates.csv",
        "phase_compute_file_inventory": phase_output / "hfo2_phase_compute_file_inventory.csv",
        "phase_compute_validation_json": phase_output / "hfo2_phase_compute_validation_card.json",
        "publication_figure_png": phase_output / "publication" / "figure1_hfo2_phase_validation.png",
        "publication_figure_pdf": phase_output / "publication" / "figure1_hfo2_phase_validation.pdf",
        "publication_figure_svg": phase_output / "publication" / "figure1_hfo2_phase_validation.svg",
        "publication_caption": phase_output / "publication" / "figure1_hfo2_phase_validation_caption.md",
    }


def _render_overview_view(counts: dict[str, Any], runtime: dict[str, Any], tokens: dict[str, Any]) -> None:
    _section_header("overview-view", "项目总览", "只保留当前模块，避免长页面滚动干扰")
    status = "运行中" if runtime.get("active_step") else "空闲"
    cards = [
        (
            "证据层",
            f"PDF {counts.get('pdf_files', 0)}，chunk {counts.get('document_chunks', 0)}。目标是让每条事实都能回到论文、页码和原文证据。",
        ),
        (
            "知识层",
            f"审核事实 {counts.get('reviewed_facts', 0)}，样品级关联 {counts.get('sample_property_links', 0)}。主线围绕材料、工艺、相结构、器件和 Pr/2Pr。",
        ),
        (
            "验证层",
            f"Benchmark {counts.get('benchmark_ok', 0)}，AI 审核 {counts.get('ai_fact_audits', 0)}。模型用独立验证集和交叉验证一起看稳定性。",
        ),
        (
            "运行状态",
            f"当前状态：{status}。累计 token 约 {tokens.get('total_tokens', 0)}；后台步骤：{runtime.get('active_step', '无')}。",
        ),
    ]
    cols = st.columns(4)
    for col, (title, body) in zip(cols, cards):
        with col:
            st.markdown(f'<div class="compact-card"><h3>{title}</h3><p>{body}</p></div>', unsafe_allow_html=True)

    workflow_steps = [
        ("01", "范围锁定", "HfO2/HZO/doped HfO2 强相关文献，弱相关非破坏式隔离"),
        ("02", "文献解析", "PDF、PaddleOCR Markdown、表格、图注、正文 chunk"),
        ("03", "本体约束抽取", "材料、工艺、相结构、性能、机制、应用、理论证据"),
        ("04", "样品级关联", "性能值绑定厚度、退火、电极、器件和相结构"),
        ("05", "可靠性复核", "Pr/2Pr、单位、页码、二手引用、物理边界条件"),
        ("06", "模型验证", "SVR、树模型、线性模型、K-fold 和验证集指标"),
        ("07", "设计建议", "证据约束候选、相似文献、风险标记和不确定性"),
        ("08", "计算闭环", "DFT/TEFS 任务包、相稳定描述符、结果回写 KG"),
    ]
    st.dataframe(
        pd.DataFrame(workflow_steps, columns=["步骤", "模块", "产物"]),
        use_container_width=True,
        hide_index=True,
    )
    st.info("上方按钮是独立模块入口；点击后只显示对应模块，不再滚到长页面里的某个位置。")


def _render_evidence_view() -> None:
    _section_header("evidence-view", "证据预览", "检索文献，Markdown/PDF 对照，必要时单篇送入千问处理")
    search_cols = st.columns([3, 1])
    with search_cols[0]:
        preview_search_query = st.text_input(
            "检索文献",
            placeholder="标题、DOI、文件名、pdf_id、正文关键词，如 oxygen vacancy / Pca21 / TiN / 2Pr",
            key="view_preview_search",
        )
    with search_cols[1]:
        preview_limit = st.number_input("最多显示", min_value=20, max_value=1200, value=80, step=20, key="view_preview_limit")

    preview_records = _preview_pdf_records(preview_search_query, int(preview_limit))
    if not preview_records:
        st.warning("没有找到匹配的已解析 PDF。可以换关键词，或先确认 PDF 已入库并解析。")
        return

    labels = [
        (
            f"{idx + 1:04d} | {record.get('title') or record['file_name']} | {record['pdf_id']}"
            + (f" | 命中 {record.get('text_hit_pages')} 页" if record.get("text_hit_pages") else "")
        )
        for idx, record in enumerate(preview_records)
    ]
    selected_index = st.selectbox("选择文献", range(len(preview_records)), format_func=lambda idx: labels[idx], key="view_pdf_pick")
    selected_pdf = preview_records[int(selected_index)]
    page_count = max(int(selected_pdf.get("page_count") or 1), 1)

    control_cols = st.columns([1, 1, 1, 3])
    with control_cols[0]:
        preview_page = st.number_input("页码", min_value=1, max_value=page_count, value=1, step=1, key="view_preview_page")
    with control_cols[1]:
        preview_zoom = st.slider("PDF 清晰度", 1.0, 2.8, 1.7, 0.1, key="view_preview_zoom")
    with control_cols[2]:
        st.metric("总页数", page_count)
    with control_cols[3]:
        meta_bits = [selected_pdf["file_name"], selected_pdf["pdf_id"]]
        if selected_pdf.get("doi"):
            meta_bits.append(f"DOI: {selected_pdf['doi']}")
        st.caption(" | ".join(str(bit) for bit in meta_bits if bit))

    snippets = _preview_search_snippets(selected_pdf["pdf_id"], preview_search_query)
    if snippets:
        with st.expander("正文命中片段", expanded=False):
            for item in snippets:
                st.markdown(f"**第 {item['page_number']} 页**")
                st.write(item["snippet"])

    counts_for_pdf = _selected_pdf_processing_counts(selected_pdf["pdf_id"])
    metric_cols = st.columns(4)
    metric_cols[0].metric("本篇 chunk", counts_for_pdf["chunks"])
    metric_cols[1].metric("高价值 chunk", counts_for_pdf["high_value_chunks"])
    metric_cols[2].metric("已抽取 chunk", counts_for_pdf["extracted_chunks"])
    metric_cols[3].metric("事实记录", counts_for_pdf["reviewed_facts"])

    with st.expander("调用千问 3.7 max 处理当前文献", expanded=False):
        qwen_status = llm_status()
        qwen_cols = st.columns([1.2, 1, 1, 1])
        with qwen_cols[0]:
            qwen_model = st.text_input("模型", value="qwen3.7-max", key="view_single_paper_qwen_model")
        with qwen_cols[1]:
            extraction_scope = st.radio(
                "处理范围",
                ["全文全部 chunk", "只处理高价值 chunk"],
                key="view_single_paper_scope",
            )
        with qwen_cols[2]:
            overwrite_extract = st.toggle("覆盖重抽", value=True, key="view_single_paper_overwrite")
        with qwen_cols[3]:
            single_workers = st.number_input("并发", min_value=1, max_value=4, value=1, step=1, key="view_single_paper_workers")
        if not qwen_status.get("api_key_configured"):
            st.warning("当前没有检测到 DashScope/OpenAI-compatible API key。请先在 .env 里配置。")
        if st.button("用千问处理当前文献", type="primary", use_container_width=True, key="view_single_paper_run"):
            high_value_only = extraction_scope == "只处理高价值 chunk"
            queue = _write_pdf_chunk_queue(selected_pdf["pdf_id"], high_value_only=high_value_only)
            if queue["chunks"] <= 0:
                st.error("当前文献没有可处理的 chunk。")
            else:
                command = [
                    sys.executable,
                    "pipelines/05_run_extraction.py",
                    "--chunk-list",
                    str(queue["path"]),
                    "--model",
                    qwen_model.strip() or "qwen3.7-max",
                    "--commit-every",
                    "10",
                    "--progress-every",
                    "5",
                    "--max-workers",
                    str(int(single_workers)),
                    "--llm-strict",
                ]
                if extraction_scope == "全文全部 chunk":
                    command.append("--all-chunks")
                if not overwrite_extract:
                    command.append("--incremental")
                result = _run_background(command, "single_paper_qwen_extraction")
                if result.get("started"):
                    st.success(f"已启动：PID {result['pid']}，队列 {queue['chunks']} 个 chunk。")
                    st.caption(f"日志：{result['stdout_log']}")
                else:
                    st.warning(result.get("reason", "没有启动"))

    left_preview, right_preview = st.columns([1.05, 1.25], gap="large")
    with left_preview:
        text_tab, ocr_tab, table_tab = st.tabs(["解析文本", "OCR Markdown", "表格"])
        with text_tab:
            page_text = _preview_page_text(selected_pdf["pdf_id"], int(preview_page))
            if page_text.strip():
                st.download_button(
                    "下载当前页文本",
                    page_text,
                    file_name=f"{selected_pdf['pdf_id']}_p{int(preview_page):04d}.md",
                    mime="text/markdown",
                    use_container_width=True,
                )
                st.text_area("当前页解析文本", value=page_text, height=560, key=f"view_page_text_{selected_pdf['pdf_id']}_{preview_page}")
            else:
                st.info("当前页没有解析文本。")
        with ocr_tab:
            ocr_records = _preview_ocr_markdown_records()
            if not ocr_records:
                st.info("还没有 PaddleOCR Markdown 输出。")
            else:
                source_path = str(selected_pdf.get("file_path") or "")
                matched = [
                    item
                    for item in ocr_records
                    if source_path and (item.get("source") == source_path or Path(source_path).name in item.get("source", ""))
                ]
                display_records = matched or ocr_records
                md_index = st.selectbox(
                    "选择 Markdown",
                    range(len(display_records)),
                    format_func=lambda idx: display_records[idx]["label"],
                    key=f"view_ocr_md_{selected_pdf['pdf_id']}",
                )
                md_path = Path(display_records[int(md_index)]["path"])
                md_text = md_path.read_text(encoding="utf-8")
                st.download_button("下载 Markdown", md_text, md_path.name, "text/markdown", use_container_width=True)
                st.markdown(md_text)
        with table_tab:
            tables = _preview_page_tables(selected_pdf["pdf_id"], int(preview_page))
            if not tables:
                st.info("当前页没有抽取到表格。")
            for table_record in tables:
                with st.expander(f"Table {table_record['table_index']} | {table_record['row_count']} x {table_record['col_count']}", expanded=True):
                    try:
                        st.dataframe(pd.DataFrame(json.loads(table_record["table_json"])), use_container_width=True, hide_index=True)
                    except Exception:
                        st.text(table_record["table_text"])
    with right_preview:
        pdf_path = Path(selected_pdf["file_path"])
        if not pdf_path.exists():
            st.error("原始 PDF 文件不存在。")
            st.caption(str(pdf_path))
            return
        st.download_button("下载原始 PDF", pdf_path.read_bytes(), pdf_path.name, "application/pdf", use_container_width=True)
        image_bytes, error, rendered_page_count = _render_pdf_page(pdf_path, int(preview_page), float(preview_zoom))
        if image_bytes:
            st.image(
                image_bytes,
                caption=f"{pdf_path.name} | 第 {min(int(preview_page), rendered_page_count or int(preview_page))} 页",
                use_container_width=True,
            )
        else:
            st.error("PDF 页面渲染失败。")
            st.caption(error or "")


def _render_control_view() -> None:
    _section_header("control-view", "执行控制", "后台任务断点续跑；长任务写入日志，不清空已完成数据")
    st.markdown(
        """
        <div class="soft-panel-note">
          这里是任务面板，不是说明书。启动前会尽量复用断点；API 额度或网络异常时会暂停，不会自动删除已有数据。
        </div>
        """,
        unsafe_allow_html=True,
    )
    row1 = st.columns(4)
    with row1[0]:
        if st.button("启动实时监控", use_container_width=True):
            result = start_monitor_server()
            st.success(f"实时监控已启动：PID {result['pid']}")
            st.link_button("打开实时监控", result["url"], use_container_width=True)
    with row1[1]:
        if st.button("启动完整流水线", type="primary", use_container_width=True):
            result = start_protected_full_pipeline(clear_pause=True)
            if result.get("started"):
                st.success(f"已启动完整流水线：PID {result['pid']}")
                st.link_button("打开实时监控", result["monitor_url"], use_container_width=True)
            else:
                st.warning(f"没有启动：{result.get('reason')}")
    with row1[2]:
        _start_pipeline_button(
            "继续样品级关联",
            "pipelines/23_link_sample_facts.py",
            "sample_linking_page",
            ["--max-workers", "16", "--progress-every", "25", "--commit-every", "50", "--force-llm-when-paused"],
        )
    with row1[3]:
        _start_pipeline_button(
            "AI 二次审核",
            "pipelines/28_ai_audit_sample_links.py",
            "ai_audits",
            ["--max-workers", "16", "--progress-every", "25", "--commit-every", "50", "--force-llm-when-paused"],
        )

    row2 = st.columns(4)
    with row2[0]:
        _start_pipeline_button("生成 LLM 文献卡片", "pipelines/26_build_literature_cards.py", "literature_cards")
    with row2[1]:
        _start_pipeline_button("LLM 标注 chunk", "pipelines/27_label_chunks_semantically.py", "chunk_labels")
    with row2[2]:
        if st.button("重建设计图谱", use_container_width=True):
            stats = build_design_graph()
            st.success(f"设计图谱：{stats['nodes']} 个节点，{stats['edges']} 条关系。")
    with row2[3]:
        if st.button("构建分层 benchmark", use_container_width=True):
            stats = build_benchmark_tier_datasets()
            st.success("已生成 strong_only / strong_partial / all_traceable 三套数据。")
            st.json(stats)

    row3 = st.columns(4)
    with row3[0]:
        if st.button("构建数据集", use_container_width=True):
            stats = build_design_dataset()
            st.success(f"设计数据集：{stats['rows']} 行，其中可建模 {stats['model_rows']} 行。")
    with row3[1]:
        if st.button("训练基础模型", use_container_width=True):
            stats = train_design_models(min_rows=12)
            st.success(f"训练完成：{stats['trained_models']} 个模型。")
    with row3[2]:
        if st.button("训练分层基线", use_container_width=True):
            stats = train_tiered_design_models(min_rows=12)
            st.success("分层模型训练完成。")
            st.json({"metrics_path": stats.get("metrics_path")})
    with row3[3]:
        if st.button("生成设计建议候选", use_container_width=True):
            stats = recommend_active_learning_candidates()
            st.success(f"已生成 {stats.get('candidates', 0)} 个候选。")

    row4 = st.columns(4)
    with row4[0]:
        _start_pipeline_button("强相关模型验证", "pipelines/33_filter_and_compare_models.py", "predictive_model_validation", ["--min-rows", "30"])
    with row4[1]:
        if st.button("规划计算验证任务", use_container_width=True):
            stats = plan_computational_feedback_tasks()
            st.success(f"已生成 {stats.get('tasks', 0)} 个计算反馈任务。")
    with row4[2]:
        if st.button("准备计算任务包", use_container_width=True):
            stats = prepare_computation_validation_jobs(max_jobs=12)
            st.success(f"已准备 {stats.get('prepared_jobs', 0)} 个任务包；总任务包 {stats.get('jobs_total', 0)}。")
    with row4[3]:
        if st.button("导出进展报告", use_container_width=True):
            stats = export_design_progress_report()
            st.success(f"报告已导出：{stats['output_path']}")

    row5 = st.columns(4)
    with row5[0]:
        _start_pipeline_button("事实交叉验证", "pipelines/18_multi_model_validate_dataset.py", "fact_cross_validation", ["--limit", "500"])
    with row5[1]:
        _start_pipeline_button("K-fold 模型交叉验证", "pipelines/50_cross_validate_design_models.py", "model_cross_validation", ["--min-rows", "30", "--folds", "5"])
    with row5[2]:
        _start_pipeline_button("应用物理约束", "pipelines/37_apply_physical_constraints.py", "apply_physical_constraints")
    with row5[3]:
        if st.button("安全自动计算工作流", use_container_width=True):
            stats = run_computation_workflow(max_candidates=20, max_tasks=80, max_jobs=12)
            st.success(f"自动工作流完成：任务包总数 {stats.get('jobs_total', 0)}，结果数 {stats.get('results_total', 0)}。")


def _render_reliability_view() -> None:
    paths = _workflow_paths()
    _section_header("reliability-view", "可靠性与交叉验证", "事实一致性、物理边界、模型稳定性三层校验")
    cols = st.columns(4)
    with cols[0]:
        _start_pipeline_button("事实交叉验证", "pipelines/18_multi_model_validate_dataset.py", "fact_cross_validation", ["--limit", "500"])
    with cols[1]:
        _start_pipeline_button(
            "Qwen 复核小样本",
            "pipelines/18_multi_model_validate_dataset.py",
            "fact_cross_validation_qwen",
            ["--limit", "50", "--include-llm", "--models", "qwen3.7-max"],
        )
    with cols[2]:
        _start_pipeline_button("应用物理约束", "pipelines/37_apply_physical_constraints.py", "apply_physical_constraints")
    with cols[3]:
        _start_pipeline_button("K-fold 模型交叉验证", "pipelines/50_cross_validate_design_models.py", "model_cross_validation", ["--min-rows", "30", "--folds", "5"])

    tabs = st.tabs(["事实交叉验证", "物理约束", "K-fold 稳定性", "物理知识库计划"])
    with tabs[0]:
        df = _load_csv(paths["reliability_summary"])
        if df.empty:
            st.info("还没有事实交叉验证结果。")
        else:
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.download_button("下载交叉验证汇总", paths["reliability_summary"].read_bytes(), paths["reliability_summary"].name, "text/csv")
            if paths["reliability_details"].exists():
                st.download_button("下载事实级明细", paths["reliability_details"].read_bytes(), paths["reliability_details"].name, "text/csv")
    with tabs[1]:
        if paths["physics_dataset"].exists():
            physics_df = _load_csv(paths["physics_dataset"])
            metric_cols = st.columns(4)
            metric_cols[0].metric("物理约束行", len(physics_df))
            if "physical_recommendation_allowed" in physics_df:
                allowed = pd.to_numeric(physics_df["physical_recommendation_allowed"], errors="coerce").fillna(0)
                metric_cols[1].metric("允许推荐", int(allowed.sum()))
            if "physical_consistency_score" in physics_df:
                scores = pd.to_numeric(physics_df["physical_consistency_score"], errors="coerce").dropna()
                metric_cols[2].metric("平均物理分", f"{float(scores.mean()):.3f}" if not scores.empty else "NA")
            if "physical_hard_violations" in physics_df:
                hard = physics_df["physical_hard_violations"].fillna("").astype(str).str.strip().ne("")
                metric_cols[3].metric("硬违规", int(hard.sum()))
            preview_cols = [
                "target_property",
                "material_family",
                "model_target_value",
                "physical_consistency_score",
                "physical_recommendation_allowed",
                "physical_hard_violations",
                "physical_soft_warnings",
            ]
            st.dataframe(physics_df[[col for col in preview_cols if col in physics_df.columns]].head(300), use_container_width=True, hide_index=True)
            st.download_button("下载物理约束数据集", paths["physics_dataset"].read_bytes(), paths["physics_dataset"].name, "text/csv")
        else:
            st.info("还没有物理约束数据集。")
    with tabs[2]:
        cv_df = _load_csv(paths["cv_summary"])
        if cv_df.empty:
            st.info("还没有 K-fold 交叉验证结果。")
        else:
            display = cv_df.copy()
            for col in ["mae_mean", "mae_std", "rmse_mean", "rmse_std", "r2_mean", "r2_std"]:
                if col in display:
                    display[col] = pd.to_numeric(display[col], errors="coerce").round(4)
            st.dataframe(display, use_container_width=True, hide_index=True)
            st.download_button("下载 K-fold 汇总", paths["cv_summary"].read_bytes(), paths["cv_summary"].name, "text/csv")
            if paths["cv_report"].exists():
                st.download_button("下载 K-fold 报告", paths["cv_report"].read_bytes(), paths["cv_report"].name, "text/markdown")
    with tabs[3]:
        if paths["physics_plan"].exists():
            st.markdown(paths["physics_plan"].read_text(encoding="utf-8"))
        else:
            st.info("物理知识库计划文档尚未生成。")


def _render_results_view() -> None:
    paths = _workflow_paths()
    _section_header("results-view", "结果产物", "数据集、模型验证、设计候选和导出文件集中查看")
    artifact_cols = st.columns(6)
    artifact_specs = [
        ("设计数据集 CSV", "dataset", "text/csv"),
        ("设计候选 CSV", "candidates", "text/csv"),
        ("进展报告", "report", "text/markdown"),
        ("模型验证报告", "model_compare_report", "text/markdown"),
        ("计算任务 CSV", "computation_tasks", "text/csv"),
        ("TEFS 四相结果", "phase_smoke_csv", "text/csv"),
    ]
    for col, (label, key, mime) in zip(artifact_cols, artifact_specs):
        with col:
            path = paths[key]
            if path.exists():
                st.download_button(label, path.read_bytes(), path.name, mime, use_container_width=True)
            else:
                st.button(f"{label} 未生成", disabled=True, use_container_width=True)
    if paths["design_graph_html"].exists():
        st.link_button("打开设计图谱 HTML", paths["design_graph_html"].resolve().as_uri(), use_container_width=True)

    dataset_df = _load_csv(paths["dataset"])
    tab_dataset, tab_models, tab_candidates = st.tabs(["设计数据集", "模型验证", "设计建议"])
    with tab_dataset:
        if dataset_df.empty:
            st.info("还没有设计数据集。")
        else:
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("总行数", len(dataset_df))
            m2.metric("可建模行", int(pd.to_numeric(dataset_df.get("model_include", 0), errors="coerce").fillna(0).sum()))
            m3.metric("目标类型", dataset_df["target_property"].nunique() if "target_property" in dataset_df else 0)
            m4.metric("论文数", dataset_df["paper_id"].nunique() if "paper_id" in dataset_df else 0)
            m5.metric("材料体系", dataset_df["material_family"].nunique() if "material_family" in dataset_df else 0)
            st.dataframe(dataset_df.head(300), use_container_width=True, hide_index=True)
    with tab_models:
        metrics = load_design_model_metrics()
        if metrics:
            st.markdown("**基础模型指标**")
            st.dataframe(pd.DataFrame(metrics.get("targets", [])), use_container_width=True, hide_index=True)
        model_compare = _load_csv(paths["model_compare_csv"])
        if model_compare.empty:
            st.info("还没有强相关预测模型验证结果。")
        else:
            rename_map = {
                "model": "model_name",
                "within_5_uC_cm2_accuracy": "within_5uC_cm2",
                "within_10_uC_cm2_accuracy": "within_10uC_cm2",
            }
            model_compare = model_compare.rename(columns=rename_map)
            if "dataset_name" not in model_compare:
                model_compare["dataset_name"] = "strong_relevant"
            cols = [
                "dataset_name",
                "target_property",
                "model_name",
                "rows",
                "train_rows",
                "validation_rows",
                "mae",
                "rmse",
                "r2",
                "baseline_mae",
                "improvement_vs_baseline",
                "within_10uC_cm2",
            ]
            display = model_compare[[col for col in cols if col in model_compare.columns]].copy()
            for col in ["mae", "rmse", "r2", "baseline_mae", "improvement_vs_baseline", "within_10uC_cm2"]:
                if col in display:
                    display[col] = pd.to_numeric(display[col], errors="coerce").round(4)
            st.dataframe(display, use_container_width=True, hide_index=True)
    with tab_candidates:
        candidates = _load_csv(paths["candidates"])
        if candidates.empty:
            st.info("还没有设计建议候选。")
        else:
            st.dataframe(candidates, use_container_width=True, hide_index=True)


def _render_compute_view() -> None:
    paths = _workflow_paths()
    _section_header("compute-view", "计算闭环", "JAX 参数校验、FerroX 相场仿真、TEFS/VASP 结果和描述符回写")
    simulation_runtime = load_simulation_runtime_status()
    jax_runtime = simulation_runtime.get("jax", {})
    ferrox_runtime = simulation_runtime.get("ferrox", {})
    runtime_cols = st.columns(4)
    runtime_cols[0].metric(
        "JAX",
        jax_runtime.get("version") or "未安装",
        jax_runtime.get("backend") or "CPU 待自检",
    )
    runtime_cols[1].metric(
        "FerroX",
        ferrox_runtime.get("ferrox_version") or "未检测",
        "已编译" if ferrox_runtime.get("installed") else "未编译",
    )
    source_commit = str(ferrox_runtime.get("source_commit") or "")
    runtime_cols[2].metric(
        "FerroX 版本锁",
        source_commit[:8] if source_commit else "NA",
        "匹配" if ferrox_runtime.get("commit_matches_lock") else "待核对",
    )
    runtime_cols[3].metric(
        "本地模式",
        "CPU 微型自检",
        "云端大算例需人工确认",
    )

    runtime_actions = st.columns(2)
    with runtime_actions[0]:
        if st.button("刷新 JAX / FerroX 环境", use_container_width=True):
            simulation_runtime = check_simulation_runtime(run_jax_smoke=True)
            st.success("环境检查已更新，未启动相场仿真。")
            st.rerun()
    with runtime_actions[1]:
        if st.button("运行 8×8×8 / 1 步微型自检", use_container_width=True):
            simulation_runtime = check_simulation_runtime(
                run_jax_smoke=True,
                run_ferrox_smoke=True,
            )
            ferrox_smoke = simulation_runtime.get("ferrox", {}).get("smoke", {})
            if ferrox_smoke.get("status") == "ok":
                st.success("JAX 与 FerroX 微型自检通过。")
            else:
                st.error(f"FerroX 自检未通过：{ferrox_smoke.get('status', 'unknown')}")
            st.rerun()

    phase_results = _load_csv(paths["phase_smoke_csv"])
    if phase_results.empty:
        st.info("还没有导入 TEFS 四相计算结果。完成云端 smoke test 后运行绘图/导入脚本。")
    else:
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("计算相数", len(phase_results))
        ok_count = int(phase_results["quality_status"].astype(str).eq("ok").sum()) if "quality_status" in phase_results else 0
        p2.metric("收敛结果", ok_count)
        if "relative_energy_meV_per_fu" in phase_results and "phase" in phase_results:
            o_phase = phase_results[phase_results["phase"].astype(str) == "orthorhombic"]
            p3.metric("o 相相对 m 相", f"{float(o_phase['relative_energy_meV_per_fu'].iloc[0]):.1f} meV/HfO2" if not o_phase.empty else "NA")
        else:
            p3.metric("o 相相对 m 相", "NA")
        p4.metric("计算平台", "TEFS/VASP")

        chart_cols = st.columns(2)
        with chart_cols[0]:
            if paths["phase_smoke_chart"].exists():
                st.image(str(paths["phase_smoke_chart"]), caption="四相相对能量排序")
        with chart_cols[1]:
            if paths["phase_smoke_landscape"].exists():
                st.image(str(paths["phase_smoke_landscape"]), caption="相对 monoclinic 的能量景观")
        display_cols = [
            "phase",
            "atoms",
            "n_formula_units",
            "total_energy_eV",
            "energy_eV_per_fu",
            "relative_energy_meV_per_fu",
            "quality_status",
            "method",
        ]
        st.dataframe(phase_results[[col for col in display_cols if col in phase_results.columns]], use_container_width=True, hide_index=True)

    runtime_tab, structure_tab, validation_tab, tasks_tab = st.tabs(
        ["仿真引擎", "相结构与论文图", "验证卡片", "任务包"]
    )
    with runtime_tab:
        flow = pd.DataFrame(
            [
                ("1", "文献 / DFT 证据", "Landau 参数、相稳定性、薄膜与电极边界"),
                ("2", "JAX", "自动微分、敏感性分析与参数校验"),
                ("3", "FerroX / AMReX", "TDGL + Poisson 域结构与开关趋势"),
                ("4", "质量门槛", "网格、时间步、边界条件与参数来源"),
                ("5", "KG / Benchmark", "审核后回写计算描述符与不确定性"),
            ],
            columns=["阶段", "引擎", "输出"],
        )
        st.dataframe(flow, use_container_width=True, hide_index=True)
        smoke = simulation_runtime.get("ferrox", {}).get("smoke", {})
        if smoke:
            smoke_cols = st.columns(4)
            smoke_cols[0].metric("FerroX 自检", smoke.get("status", "unknown"))
            smoke_cols[1].metric("网格", "×".join(str(v) for v in smoke.get("mesh", [])) or "NA")
            smoke_cols[2].metric("时间步", smoke.get("steps", 0))
            smoke_cols[3].metric("Plotfile", len(smoke.get("plotfiles", [])))
        if paths["simulation_runtime_status"].exists():
            st.download_button(
                "下载仿真环境清单",
                paths["simulation_runtime_status"].read_bytes(),
                paths["simulation_runtime_status"].name,
                "application/json",
                use_container_width=True,
            )
        with st.expander("环境细节", expanded=False):
            st.json(simulation_runtime)
    with structure_tab:
        if paths["phase_smoke_structure_chart"].exists():
            st.image(str(paths["phase_smoke_structure_chart"]), caption="CONTCAR 解析的 HfO2 四相 relaxed 结构投影")
        if paths["publication_figure_png"].exists():
            st.image(str(paths["publication_figure_png"]), caption="Figure draft: HfO2 phase validation descriptors")
        dl_cols = st.columns(4)
        for col, (label, key, mime) in zip(
            dl_cols,
            [
                ("VESTA CIF 包", "phase_smoke_vesta_zip", "application/zip"),
                ("Figure PDF", "publication_figure_pdf", "application/pdf"),
                ("Figure SVG", "publication_figure_svg", "image/svg+xml"),
                ("英文图注", "publication_caption", "text/markdown"),
            ],
        ):
            with col:
                path = paths[key]
                if path.exists():
                    st.download_button(label, path.read_bytes(), path.name, mime, use_container_width=True)
    with validation_tab:
        quality = _load_csv(paths["phase_compute_quality_gates"])
        table = _load_csv(paths["phase_compute_validation_table"])
        if not quality.empty:
            st.dataframe(quality, use_container_width=True, hide_index=True)
        if not table.empty:
            st.dataframe(table, use_container_width=True, hide_index=True)
        if paths["phase_compute_validation_card"].exists():
            with st.expander("计算验证卡片 Markdown", expanded=False):
                st.markdown(paths["phase_compute_validation_card"].read_text(encoding="utf-8"))
        dl_cols = st.columns(4)
        for col, (label, key, mime) in zip(
            dl_cols,
            [
                ("验证卡片", "phase_compute_validation_card", "text/markdown"),
                ("验证表", "phase_compute_validation_table", "text/csv"),
                ("质量门槛", "phase_compute_quality_gates", "text/csv"),
                ("文件清单", "phase_compute_file_inventory", "text/csv"),
            ],
        ):
            with col:
                path = paths[key]
                if path.exists():
                    st.download_button(label, path.read_bytes(), path.name, mime, use_container_width=True)
    with tasks_tab:
        computation_tasks = _load_csv(paths["computation_tasks"])
        if computation_tasks.empty:
            st.info("还没有计算反馈任务。")
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("任务数", len(computation_tasks))
            c2.metric("任务类型", computation_tasks["task_family"].nunique() if "task_family" in computation_tasks else 0)
            c3.metric("安全状态", computation_tasks["safety_status"].mode().iloc[0] if "safety_status" in computation_tasks else "planned")
            st.dataframe(computation_tasks.head(160), use_container_width=True, hide_index=True)
        jobs = list_computation_jobs(limit=120)
        if jobs:
            st.markdown("**自动计算验证任务包**")
            st.dataframe(pd.DataFrame(jobs), use_container_width=True, hide_index=True)
        if paths["computation_report"].exists():
            with st.expander("计算反馈计划报告", expanded=False):
                st.markdown(paths["computation_report"].read_text(encoding="utf-8"))


def _render_selected_workflow_view(view: str, counts: dict[str, Any], runtime: dict[str, Any], tokens: dict[str, Any]) -> None:
    if view == "overview":
        _render_overview_view(counts, runtime, tokens)
    elif view == "evidence":
        _render_evidence_view()
    elif view == "control":
        _render_control_view()
    elif view == "reliability":
        _render_reliability_view()
    elif view == "results":
        _render_results_view()
    elif view == "compute":
        _render_compute_view()
    else:
        _render_overview_view(counts, runtime, tokens)


snapshot = _cached_monitor_snapshot()
counts = snapshot.get("counts", {})
runtime = snapshot.get("runtime", {})
tokens = snapshot.get("tokens", {})
pause = _cached_llm_pause()

_inject_workflow_theme()
render_top_nav("设计工作流")
_render_hero()

if pause:
    st.error("LLM 抽取已暂停。通常是额度、欠费、限流或 key 状态问题。已完成的数据不会丢。")
    with st.expander("暂停详情", expanded=False):
        st.write(pause.get("reason", ""))
        st.write(pause.get("resume_hint", "恢复额度或配置新 key 后，重新运行同一条 pipeline 即可断点继续。"))
    if st.button("我已恢复 API，清除暂停标记"):
        clear_llm_pause()
        st.success("已清除暂停标记。")

if snapshot.get("active"):
    st.info(
        f"后台正在运行：{runtime.get('active_step', '处理中')} | "
        f"总运行 {runtime.get('elapsed', '估算中')} | "
        f"当前步骤 {runtime.get('active_elapsed', '估算中')} | "
        f"预计剩余 {runtime.get('eta', '估算中')} | "
        f"速度 {runtime.get('rate_per_min', 0)} 条/分钟"
    )
else:
    st.success("当前没有检测到全量后台任务。可以查看结果，或启动下一步。")

top_cols = st.columns(5)
with top_cols[0]:
    _metric("PDF", counts.get("pdf_files", 0))
with top_cols[1]:
    _metric("chunk", counts.get("document_chunks", 0))
with top_cols[2]:
    _metric("审核事实", counts.get("reviewed_facts", 0))
with top_cols[3]:
    _metric("样品级关联", counts.get("sample_property_links", 0))
with top_cols[4]:
    _metric("Benchmark", counts.get("benchmark_ok", 0), f"AI 审核 {counts.get('ai_fact_audits', 0)}")

selected_workflow_view = _current_workflow_view()
selected_workflow_view = _render_workflow_view_nav(selected_workflow_view)
_render_selected_workflow_view(selected_workflow_view, counts, runtime, tokens)
st.stop()

_section_header("problem", "我们要解决的问题", "从论文证据到可建模材料设计的四个关键断点")
problem_cols = st.columns(4)
cards = [
    ("样品条件缺失", "只有材料名和 Pr/2Pr 不够。性能值必须绑定厚度、退火、电极、沉积方法、器件、相结构和状态。"),
    ("证据不可追溯", "每个结论都要能回到 DOI、论文标题、页码、chunk 和原文证据句。"),
    ("预测需要验证", "用 SVR、树模型、线性模型等在训练/验证集上检验 Pr/2Pr 可预测性，主报 MAE、RMSE、R2 和容差命中率。"),
    ("计算闭环缺口", "把高分候选转化为相稳定、氧空位、界面、相场和动力学任务，再把 descriptors 回写 KG 与 benchmark。"),
]
card_accents = ["#8bacdd", "#9ddfe4", "#b2a4cf", "#f5ced0"]
for col, (title, body), accent in zip(problem_cols, cards, card_accents):
    with col:
        st.markdown(
            f"""
            <div class="problem-card">
              <div class="kicker" style="background:{accent};"></div>
              <h3>{title}</h3>
              <p>{body}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

_section_header("pipeline", "完整工作流", "每一步都有可复现产物，后续论文图和方法表从这里生成")
workflow_steps = [
    ("01", "范围锁定", "只保留 HfO2/HZO/doped HfO2 主线，弱相关数据非破坏式排除", "strong relevance queue"),
    ("02", "文献解析", "PDF、表格、图注、正文 chunk 统一进入证据层", "parsed_pages / chunks / tables"),
    ("03", "本体约束抽取", "材料、样品、工艺、相、性能、机制、设计规则", "reviewed_facts / benchmark_extractions"),
    ("04", "样品级关联", "同一性能值绑定材料、厚度、退火、电极、相结构", "sample_property_links"),
    ("05", "AI 审核与 Gold set", "排查 Pr/2Pr、单位、综述二手值、样品错配", "ai_fact_audits / manual_annotations"),
    ("06", "设计图谱", "可控变量、目标变量、约束变量、机制变量、证据", "design_graph CSV + HTML"),
    ("07", "Benchmark 分层", "strong_only / strong_partial / all_traceable", "tiered benchmark CSV"),
    ("08", "交叉验证", "事实级交叉验证、物理约束评分、K-fold 稳定性诊断", "validation / cross-validation"),
    ("09", "设计建议", "结合预测值、不确定性和相似文献证据推荐候选", "evidence-constrained candidates"),
    ("10", "计算反馈", "把候选转为 DFT、氧空位、界面、相场、MD/ML 势任务", "computational feedback tasks"),
]
for row_start in range(0, len(workflow_steps), 5):
    stage_cols = st.columns(5)
    for col, (num, title, target, artifact) in zip(stage_cols, workflow_steps[row_start : row_start + 5]):
        with col:
            st.markdown(
                f"""
                <div class="workflow-stage">
                  <div class="num">{num}</div>
                  <div>
                    <h3>{title}</h3>
                    <p>{target}</p>
                    <code>{artifact}</code>
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

_section_header("evidence", "文献证据预览窗口", "检索、Markdown/PDF 对照、单篇千问处理都在这里完成")
st.markdown(
    """
    <div class="soft-panel-note">
      先检索文献，再左侧查看 Markdown、解析文本和表格，右侧对照原始 PDF；确认后可以只把当前文献送入千问 3.7 max 做结构化处理。
    </div>
    """,
    unsafe_allow_html=True,
)

search_cols = st.columns([3, 1])
with search_cols[0]:
    preview_search_query = st.text_input(
        "检索文献",
        placeholder="输入标题、DOI、文件名、pdf_id，或正文关键词，如 oxygen vacancy / Pca21 / TiN / 2Pr",
        key="workflow_preview_search",
    )
with search_cols[1]:
    preview_limit = st.number_input("最多显示结果", min_value=20, max_value=1200, value=120, step=20)

preview_records = _preview_pdf_records(preview_search_query, int(preview_limit))
if not preview_records:
    st.warning("没有找到匹配的已解析 PDF。可以换一个关键词，或先确认 PDF 已入库并解析。")
else:
    st.caption(f"检索结果：{len(preview_records)} 篇")
    preview_labels = [
        (
            f"{idx + 1:04d} | {record.get('title') or record['file_name']} | {record['pdf_id']}"
            + (
                f" | 命中 {record.get('text_hit_pages')} 页，首个 p{record.get('first_hit_page')}"
                if record.get("text_hit_pages")
                else ""
            )
        )
        for idx, record in enumerate(preview_records)
    ]
    selected_index = st.selectbox(
        "选择文献",
        range(len(preview_records)),
        format_func=lambda idx: preview_labels[idx],
        key="workflow_preview_pdf",
    )
    selected_pdf = preview_records[int(selected_index)]
    page_count = int(selected_pdf.get("page_count") or 1)
    current_page_state = int(st.session_state.get("workflow_preview_page", 1) or 1)
    if current_page_state < 1:
        st.session_state["workflow_preview_page"] = 1
    elif current_page_state > max(page_count, 1):
        st.session_state["workflow_preview_page"] = max(page_count, 1)
    first_hit_page = int(selected_pdf.get("first_hit_page") or 0)
    if first_hit_page:
        hit_cols = st.columns([2, 1])
        with hit_cols[0]:
            st.info(f"当前检索词在这篇文献中命中 {selected_pdf.get('text_hit_pages')} 页，首个命中页是第 {first_hit_page} 页。")
        with hit_cols[1]:
            if st.button("跳到首个命中页", use_container_width=True):
                st.session_state["workflow_preview_page"] = min(max(first_hit_page, 1), max(page_count, 1))
                st.rerun()
    control_cols = st.columns([1, 1, 1, 2])
    with control_cols[0]:
        preview_page = st.number_input(
            "页码",
            min_value=1,
            max_value=max(page_count, 1),
            value=1,
            step=1,
            key="workflow_preview_page",
        )
    with control_cols[1]:
        preview_zoom = st.slider("PDF 清晰度", 1.0, 2.8, 1.7, 0.1, key="workflow_preview_zoom")
    with control_cols[2]:
        st.metric("总页数", page_count)
    with control_cols[3]:
        meta_bits = [selected_pdf["file_name"], selected_pdf["pdf_id"]]
        if selected_pdf.get("doi"):
            meta_bits.append(f"DOI: {selected_pdf['doi']}")
        if selected_pdf.get("year"):
            meta_bits.append(str(selected_pdf["year"]))
        st.caption(" | ".join(str(bit) for bit in meta_bits if bit))

    snippets = _preview_search_snippets(selected_pdf["pdf_id"], preview_search_query)
    if snippets:
        with st.expander("查看正文检索命中片段", expanded=False):
            for item in snippets:
                st.markdown(f"**第 {item['page_number']} 页**")
                st.write(item["snippet"])

    processing_counts = _selected_pdf_processing_counts(selected_pdf["pdf_id"])
    status_cols = st.columns(4)
    with status_cols[0]:
        st.metric("本篇 chunk", processing_counts["chunks"])
    with status_cols[1]:
        st.metric("高价值 chunk", processing_counts["high_value_chunks"])
    with status_cols[2]:
        st.metric("已抽取 chunk", processing_counts["extracted_chunks"])
    with status_cols[3]:
        st.metric("事实记录", processing_counts["reviewed_facts"])

    with st.expander("调用千问 3.7 max 处理当前文献", expanded=False):
        qwen_status = llm_status()
        qwen_cols = st.columns([1.2, 1, 1, 1])
        with qwen_cols[0]:
            qwen_model = st.text_input("模型", value="qwen3.7-max", key="single_paper_qwen_model")
        with qwen_cols[1]:
            extraction_scope = st.radio(
                "处理范围",
                ["全文全部 chunk", "只处理高价值 chunk"],
                horizontal=False,
                key="single_paper_extraction_scope",
            )
        with qwen_cols[2]:
            overwrite_extract = st.toggle("覆盖重抽", value=True, key="single_paper_overwrite")
        with qwen_cols[3]:
            single_workers = st.number_input("并发", min_value=1, max_value=4, value=1, step=1, key="single_paper_workers")
        if not qwen_status.get("api_key_configured"):
            st.warning("当前没有检测到 DashScope/OpenAI-compatible API key。请先在 .env 里配置 DASHSCOPE_API_KEY。")
        st.caption(
            "这个按钮只处理当前选中文献。结果会写入 extraction_candidates / reviewed_facts，继续进入审核、本体、benchmark 和向量库流程。"
        )
        if st.button("用千问处理当前文献", type="primary", use_container_width=True):
            high_value_only = extraction_scope == "只处理高价值 chunk"
            queue = _write_pdf_chunk_queue(selected_pdf["pdf_id"], high_value_only=high_value_only)
            if queue["chunks"] <= 0:
                st.error("当前文献没有可处理的 chunk。")
            else:
                command = [
                    sys.executable,
                    "pipelines/05_run_extraction.py",
                    "--chunk-list",
                    str(queue["path"]),
                    "--model",
                    qwen_model.strip() or "qwen3.7-max",
                    "--commit-every",
                    "10",
                    "--progress-every",
                    "5",
                    "--max-workers",
                    str(int(single_workers)),
                    "--llm-strict",
                ]
                if extraction_scope == "全文全部 chunk":
                    command.append("--all-chunks")
                if not overwrite_extract:
                    command.append("--incremental")
                result = _run_background(command, "single_paper_qwen_extraction")
                if result.get("started"):
                    st.success(f"已启动当前文献抽取：PID {result['pid']}，队列 {queue['chunks']} 个 chunk。")
                    st.caption(f"队列：{queue['path']}")
                    st.caption(f"日志：{result['stdout_log']}")
                else:
                    st.warning(result.get("reason", "没有启动"))

    left_preview, right_preview = st.columns([1.05, 1.25], gap="large")
    with left_preview:
        text_tab, ocr_tab, table_tab = st.tabs(["解析文本 / Markdown", "PaddleOCR Markdown", "表格"])
        page_text = _preview_page_text(selected_pdf["pdf_id"], int(preview_page))
        with text_tab:
            if page_text.strip():
                st.download_button(
                    "下载当前页解析文本",
                    page_text,
                    file_name=f"{selected_pdf['pdf_id']}_p{int(preview_page):04d}.md",
                    mime="text/markdown",
                    use_container_width=True,
                )
                render_page_text = st.toggle(
                    "按 Markdown 渲染当前页",
                    value=False,
                    key=f"workflow_render_page_text_{selected_pdf['pdf_id']}_{int(preview_page)}",
                )
                if render_page_text:
                    st.markdown(page_text)
                else:
                    st.text_area(
                        "当前页解析文本",
                        value=page_text,
                        height=620,
                        key=f"workflow_page_text_{selected_pdf['pdf_id']}_{int(preview_page)}",
                    )
            else:
                st.info("当前页没有解析文本。")
        with ocr_tab:
            ocr_records = _preview_ocr_markdown_records()
            if not ocr_records:
                st.info("还没有 PaddleOCR Markdown 输出。用 PDF 解析页提交 PaddleOCR 后，这里会自动出现。")
            else:
                source_path = str(selected_pdf.get("file_path") or "")
                matched = [
                    item
                    for item in ocr_records
                    if source_path and (item.get("source") == source_path or Path(source_path).name in item.get("source", ""))
                ]
                display_records = matched or ocr_records
                md_index = st.selectbox(
                    "选择 Markdown",
                    range(len(display_records)),
                    format_func=lambda idx: display_records[idx]["label"],
                    key=f"workflow_ocr_md_{selected_pdf['pdf_id']}",
                )
                md_path = Path(display_records[int(md_index)]["path"])
                try:
                    md_text = md_path.read_text(encoding="utf-8")
                    st.download_button(
                        "下载 Markdown",
                        md_text,
                        file_name=md_path.name,
                        mime="text/markdown",
                        use_container_width=True,
                    )
                    render_markdown = st.toggle("渲染 Markdown", value=True, key=f"render_md_{md_path}")
                    if render_markdown:
                        st.markdown(md_text)
                    else:
                        st.text_area("Markdown 原文", md_text, height=620, key=f"raw_md_{md_path}")
                except Exception as exc:
                    st.error(f"读取 Markdown 失败：{exc}")
        with table_tab:
            table_records = _preview_page_tables(selected_pdf["pdf_id"], int(preview_page))
            if not table_records:
                st.info("当前页没有抽取到表格。")
            for table_record in table_records:
                with st.expander(
                    f"Table {table_record['table_index']} | {table_record['row_count']} x {table_record['col_count']}",
                    expanded=True,
                ):
                    try:
                        table_rows = json.loads(table_record["table_json"])
                        st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)
                    except Exception:
                        st.text(table_record["table_text"])
    with right_preview:
        pdf_path = Path(selected_pdf["file_path"])
        if not pdf_path.exists():
            st.error("原始 PDF 文件不存在。")
            st.caption(str(pdf_path))
        else:
            st.download_button(
                "下载原始 PDF",
                pdf_path.read_bytes(),
                file_name=pdf_path.name,
                mime="application/pdf",
                use_container_width=True,
            )
            image_bytes, error, rendered_page_count = _render_pdf_page(pdf_path, int(preview_page), float(preview_zoom))
            if image_bytes:
                st.image(
                    image_bytes,
                    caption=f"{pdf_path.name} | 第 {min(int(preview_page), rendered_page_count or int(preview_page))} 页",
                    use_container_width=True,
                )
            else:
                st.error("PDF 页面渲染失败。")
                st.caption(error or "")

_section_header("control", "执行控制", "后台任务断点续跑，长任务会写入日志并保留已完成数据")
st.markdown(
    """
    <div class="soft-panel-note">
      如果 token、网络或额度出问题，LLM 任务会先暂停而不是清空数据。恢复 API 后重新运行同一条 pipeline 即可继续。
    </div>
    """,
    unsafe_allow_html=True,
)
row1 = st.columns(4)
with row1[0]:
    if st.button("启动实时监控", use_container_width=True):
        result = start_monitor_server()
        st.success(f"实时监控已启动：PID {result['pid']}")
        st.link_button("打开实时监控", result["url"], use_container_width=True)
with row1[1]:
    if st.button("启动完整流水线", type="primary", use_container_width=True):
        result = start_protected_full_pipeline(clear_pause=True)
        if result.get("started"):
            st.success(f"已启动完整流水线：PID {result['pid']}")
            st.link_button("打开实时监控", result["monitor_url"], use_container_width=True)
        else:
            st.warning(f"没有启动：{result.get('reason')}")
with row1[2]:
    _start_pipeline_button(
        "继续样品级关联",
        "pipelines/23_link_sample_facts.py",
        "sample_linking_page",
        ["--max-workers", "16", "--progress-every", "25", "--commit-every", "50", "--force-llm-when-paused"],
    )
with row1[3]:
    _start_pipeline_button(
        "AI 二次审核",
        "pipelines/28_ai_audit_sample_links.py",
        "ai_audits",
        ["--max-workers", "16", "--progress-every", "25", "--commit-every", "50", "--force-llm-when-paused"],
    )

row2 = st.columns(4)
with row2[0]:
    _start_pipeline_button(
        "生成 LLM 文献卡片",
        "pipelines/26_build_literature_cards.py",
        "literature_cards",
        ["--max-workers", "12", "--progress-every", "10", "--commit-every", "20", "--force-llm-when-paused"],
    )
with row2[1]:
    _start_pipeline_button(
        "LLM 标注 chunk",
        "pipelines/27_label_chunks_semantically.py",
        "chunk_labels",
        ["--max-workers", "16", "--progress-every", "25", "--commit-every", "50", "--force-llm-when-paused"],
    )
with row2[2]:
    if st.button("重建设计图谱", use_container_width=True):
        stats = build_design_graph()
        st.success(f"设计图谱：{stats['nodes']} 个节点，{stats['edges']} 条关系。")
with row2[3]:
    if st.button("构建分层 benchmark", use_container_width=True):
        stats = build_benchmark_tier_datasets()
        st.success("已生成 strong_only / strong_partial / all_traceable 三套数据。")
        st.json(stats)

row3 = st.columns(4)
with row3[0]:
    if st.button("构建数据集", use_container_width=True):
        stats = build_design_dataset()
        st.success(f"设计数据集：{stats['rows']} 行，其中可建模 {stats['model_rows']} 行。")
with row3[1]:
    if st.button("训练基础模型", use_container_width=True):
        stats = train_design_models(min_rows=12)
        st.success(f"训练完成：{stats['trained_models']} 个模型。")
with row3[2]:
    if st.button("训练分层基线", use_container_width=True):
        stats = train_tiered_design_models(min_rows=12)
        st.success("分层模型训练完成。")
        st.json({"metrics_path": stats.get("metrics_path")})
with row3[3]:
    if st.button("生成设计建议候选", use_container_width=True):
        stats = recommend_active_learning_candidates()
        st.success(f"已生成 {stats.get('candidates', 0)} 个候选。")

if st.button("导出进展报告", use_container_width=True):
    stats = export_design_progress_report()
    st.success(f"报告已导出：{stats['output_path']}")

row4 = st.columns(4)
with row4[0]:
    _start_pipeline_button(
        "强相关模型验证",
        "pipelines/33_filter_and_compare_models.py",
        "predictive_model_validation",
        ["--min-rows", "30"],
    )
with row4[1]:
    if st.button("规划计算验证任务", use_container_width=True):
        stats = plan_computational_feedback_tasks()
        st.success(f"已生成 {stats.get('tasks', 0)} 个计算反馈任务；未启动本机或云端计算。")
        if stats.get("report_path"):
            st.caption(f"计划报告：{stats['report_path']}")
with row4[2]:
    if st.button("准备计算任务包", use_container_width=True):
        stats = prepare_computation_validation_jobs(max_jobs=12)
        st.success(
            f"已准备 {stats.get('prepared_jobs', 0)} 个任务包；总任务包 {stats.get('jobs_total', 0)}。"
        )
        st.caption("没有启动 VASP、MD、SSH 或云端任务。")
with row4[3]:
    st.page_link("pages/10_事实一致性复核.py", label="事实一致性复核", use_container_width=True)

row5 = st.columns(4)
with row5[0]:
    st.page_link("pages/13_人工标注.py", label="人工标注 Gold set", use_container_width=True)
with row5[1]:
    if st.button("安全自动计算工作流", use_container_width=True):
        stats = run_computation_workflow(max_candidates=20, max_tasks=80, max_jobs=12)
        st.success(
            f"自动工作流完成：任务包总数 {stats.get('jobs_total', 0)}，结果数 {stats.get('results_total', 0)}。"
        )
        st.caption("安全模式未启动 VASP、MD、SSH 或云端任务。")
        if stats.get("report_path"):
            st.caption(f"工作流报告：{stats['report_path']}")

_section_header("reliability", "可靠性与交叉验证", "事实、物理边界、模型稳定性三层互相校验")
st.markdown(
    """
    <div class="soft-panel-note">
      主论文指标仍使用独立验证集；K-fold 交叉验证用于检查模型稳定性。事实级交叉验证用于发现证据、单位、Pr/2Pr 和本体关系问题。
    </div>
    """,
    unsafe_allow_html=True,
)
reliability_cols = st.columns(4)
with reliability_cols[0]:
    _start_pipeline_button(
        "事实交叉验证",
        "pipelines/18_multi_model_validate_dataset.py",
        "fact_cross_validation",
        ["--limit", "500"],
    )
with reliability_cols[1]:
    _start_pipeline_button(
        "Qwen 复核小样本",
        "pipelines/18_multi_model_validate_dataset.py",
        "fact_cross_validation_qwen",
        ["--limit", "50", "--include-llm", "--models", "qwen3.7-max"],
    )
with reliability_cols[2]:
    _start_pipeline_button(
        "应用物理约束",
        "pipelines/37_apply_physical_constraints.py",
        "apply_physical_constraints",
    )
with reliability_cols[3]:
    _start_pipeline_button(
        "K-fold 模型交叉验证",
        "pipelines/50_cross_validate_design_models.py",
        "model_cross_validation",
        ["--min-rows", "30", "--folds", "5"],
    )

reliability_summary_path = PROJECT_ROOT / "data" / "exports" / "model_validation_summary.csv"
reliability_details_path = PROJECT_ROOT / "data" / "exports" / "model_validation_details.csv"
physics_dataset_path = PROJECT_ROOT / "data" / "design" / "hfo2_design_dataset_physics_constrained.csv"
cv_summary_path = PROJECT_ROOT / "models" / "model_cross_validation" / "cross_validation_summary.csv"
cv_report_path = PROJECT_ROOT / "models" / "model_cross_validation" / "cross_validation_report.md"
physics_plan_path = PROJECT_ROOT / "docs" / "physics_knowledge_base_plan.md"

rel_tabs = st.tabs(["事实交叉验证", "物理约束", "K-fold 稳定性", "物理知识库计划"])
with rel_tabs[0]:
    rel_df = _load_csv(reliability_summary_path)
    if rel_df.empty:
        st.info("还没有事实交叉验证结果。点击“事实交叉验证”后会生成。")
    else:
        st.dataframe(rel_df, use_container_width=True, hide_index=True)
        download_cols = st.columns(2)
        with download_cols[0]:
            st.download_button(
                "下载交叉验证汇总",
                reliability_summary_path.read_bytes(),
                reliability_summary_path.name,
                "text/csv",
                use_container_width=True,
            )
        with download_cols[1]:
            if reliability_details_path.exists():
                st.download_button(
                    "下载事实级明细",
                    reliability_details_path.read_bytes(),
                    reliability_details_path.name,
                    "text/csv",
                    use_container_width=True,
                )
with rel_tabs[1]:
    if physics_dataset_path.exists():
        physics_df = _load_csv(physics_dataset_path)
        metric_cols = st.columns(4)
        if not physics_df.empty:
            metric_cols[0].metric("物理约束行", len(physics_df))
            if "physical_recommendation_allowed" in physics_df:
                allowed = pd.to_numeric(physics_df["physical_recommendation_allowed"], errors="coerce").fillna(0)
                metric_cols[1].metric("允许推荐", int(allowed.sum()))
            if "physical_consistency_score" in physics_df:
                scores = pd.to_numeric(physics_df["physical_consistency_score"], errors="coerce").dropna()
                metric_cols[2].metric("平均物理分", f"{float(scores.mean()):.3f}" if not scores.empty else "NA")
            if "physical_hard_violations" in physics_df:
                hard = physics_df["physical_hard_violations"].fillna("").astype(str).str.strip().ne("")
                metric_cols[3].metric("硬违规", int(hard.sum()))
            preview_cols = [
                "target_property",
                "material_family",
                "model_target_value",
                "physical_consistency_score",
                "physical_recommendation_allowed",
                "physical_hard_violations",
                "physical_soft_warnings",
                "physical_risk_flags",
            ]
            st.dataframe(
                physics_df[[col for col in preview_cols if col in physics_df.columns]].head(300),
                use_container_width=True,
                hide_index=True,
            )
        st.download_button(
            "下载物理约束数据集",
            physics_dataset_path.read_bytes(),
            physics_dataset_path.name,
            "text/csv",
            use_container_width=True,
        )
    else:
        st.info("还没有物理约束数据集。点击“应用物理约束”后会生成。")
with rel_tabs[2]:
    cv_df = _load_csv(cv_summary_path)
    if cv_df.empty:
        st.info("还没有 K-fold 交叉验证结果。点击“K-fold 模型交叉验证”后会生成。")
    else:
        display = cv_df.copy()
        for col in ["mae_mean", "mae_std", "rmse_mean", "rmse_std", "r2_mean", "r2_std"]:
            if col in display:
                display[col] = pd.to_numeric(display[col], errors="coerce").round(4)
        st.dataframe(display, use_container_width=True, hide_index=True)
        cv_download_cols = st.columns(2)
        with cv_download_cols[0]:
            st.download_button(
                "下载 K-fold 汇总",
                cv_summary_path.read_bytes(),
                cv_summary_path.name,
                "text/csv",
                use_container_width=True,
            )
        with cv_download_cols[1]:
            if cv_report_path.exists():
                st.download_button(
                    "下载 K-fold 报告",
                    cv_report_path.read_bytes(),
                    cv_report_path.name,
                    "text/markdown",
                    use_container_width=True,
                )
with rel_tabs[3]:
    if physics_plan_path.exists():
        st.markdown(physics_plan_path.read_text(encoding="utf-8"))
    else:
        st.info("物理知识库计划文档尚未生成。")

dataset_path = PROJECT_ROOT / "data" / "design" / "hfo2_design_dataset.csv"
candidates_path = PROJECT_ROOT / "data" / "design" / "active_learning_candidates.csv"
computation_tasks_path = PROJECT_ROOT / "data" / "computation" / "computational_feedback_tasks.csv"
computation_report_path = PROJECT_ROOT / "data" / "computation" / "computational_feedback_plan.md"
computation_jobs_path = PROJECT_ROOT / "data" / "computation" / "validation_jobs" / "computation_jobs.csv"
computation_jobs_report_path = PROJECT_ROOT / "data" / "computation" / "validation_jobs" / "computation_validation_jobs.md"
computation_workflow_report_path = PROJECT_ROOT / "data" / "computation" / "workflow" / "computation_workflow_report.md"
phase_smoke_csv = (
    PROJECT_ROOT
    / "data"
    / "computation"
    / "tefs_hfo2_phase_smoke_20260622"
    / "hfo2_phase_smoke_results_normalized.csv"
)
computed_descriptors_path = PROJECT_ROOT / "data" / "computation" / "computed_descriptors.csv"
phase_smoke_output_dir = PROJECT_ROOT / "outputs" / "phase_smoke"
phase_smoke_chart = phase_smoke_output_dir / "hfo2_phase_relative_energy.png"
phase_smoke_landscape = phase_smoke_output_dir / "hfo2_phase_energy_landscape.png"
phase_smoke_report = phase_smoke_output_dir / "hfo2_phase_smoke_summary.md"
phase_smoke_structural_csv = (
    PROJECT_ROOT
    / "data"
    / "computation"
    / "tefs_hfo2_phase_smoke_20260622"
    / "hfo2_phase_smoke_structural_results.csv"
)
phase_smoke_convergence_csv = (
    PROJECT_ROOT
    / "data"
    / "computation"
    / "tefs_hfo2_phase_smoke_20260622"
    / "hfo2_phase_smoke_relax_convergence.csv"
)
phase_smoke_structure_chart = phase_smoke_output_dir / "hfo2_phase_relaxed_structures.png"
phase_smoke_convergence_chart = phase_smoke_output_dir / "hfo2_phase_relax_convergence.png"
phase_smoke_volume_chart = phase_smoke_output_dir / "hfo2_phase_final_volume.png"
phase_smoke_energy_volume_chart = phase_smoke_output_dir / "hfo2_phase_energy_volume.png"
phase_smoke_real_report = phase_smoke_output_dir / "hfo2_phase_real_vasp_results.md"
phase_smoke_vesta_zip = phase_smoke_output_dir / "hfo2_phase_vesta_cif_bundle.zip"
phase_compute_validation_card = phase_smoke_output_dir / "hfo2_phase_compute_validation_card.md"
phase_compute_validation_table = phase_smoke_output_dir / "hfo2_phase_compute_validation_table.csv"
phase_compute_quality_gates = phase_smoke_output_dir / "hfo2_phase_compute_quality_gates.csv"
phase_compute_file_inventory = phase_smoke_output_dir / "hfo2_phase_compute_file_inventory.csv"
phase_compute_validation_json = phase_smoke_output_dir / "hfo2_phase_compute_validation_card.json"
publication_figure_png = phase_smoke_output_dir / "publication" / "figure1_hfo2_phase_validation.png"
publication_figure_pdf = phase_smoke_output_dir / "publication" / "figure1_hfo2_phase_validation.pdf"
publication_figure_svg = phase_smoke_output_dir / "publication" / "figure1_hfo2_phase_validation.svg"
publication_caption = phase_smoke_output_dir / "publication" / "figure1_hfo2_phase_validation_caption.md"
design_graph_html = PROJECT_ROOT / "data" / "exports" / "hfo2_design_graph.html"
report_path = PROJECT_ROOT / "data" / "exports" / "hfo2_design_progress_report.md"
model_compare_csv = PROJECT_ROOT / "models" / "model_comparison" / "strong_relevant_model_comparison.csv"
model_compare_report = PROJECT_ROOT / "models" / "model_comparison" / "strong_relevant_model_comparison.md"
metrics = load_design_model_metrics()

_section_header("artifacts", "当前产物", "导出文件、图谱、报告和计算结果集中下载")
artifact_cols = st.columns(8)
with artifact_cols[0]:
    if dataset_path.exists():
        st.download_button("下载设计数据集 CSV", dataset_path.read_bytes(), dataset_path.name, "text/csv", use_container_width=True)
    else:
        st.button("设计数据集未生成", disabled=True, use_container_width=True)
with artifact_cols[1]:
    if candidates_path.exists():
        st.download_button("下载设计建议候选 CSV", candidates_path.read_bytes(), candidates_path.name, "text/csv", use_container_width=True)
    else:
        st.button("设计建议候选未生成", disabled=True, use_container_width=True)
with artifact_cols[2]:
    if design_graph_html.exists():
        st.link_button("打开设计图谱 HTML", design_graph_html.resolve().as_uri(), use_container_width=True)
    else:
        st.button("设计图谱 HTML 未生成", disabled=True, use_container_width=True)
with artifact_cols[3]:
    if report_path.exists():
        st.download_button("下载进展报告", report_path.read_bytes(), report_path.name, "text/markdown", use_container_width=True)
    else:
        st.button("进展报告未生成", disabled=True, use_container_width=True)
with artifact_cols[4]:
    if model_compare_report.exists():
        st.download_button(
            "下载模型验证报告",
            model_compare_report.read_bytes(),
            model_compare_report.name,
            "text/markdown",
            use_container_width=True,
        )
    else:
        st.button("模型验证报告未生成", disabled=True, use_container_width=True)
with artifact_cols[5]:
    if computation_tasks_path.exists():
        st.download_button(
            "下载计算任务 CSV",
            computation_tasks_path.read_bytes(),
            computation_tasks_path.name,
            "text/csv",
            use_container_width=True,
        )
    else:
        st.button("计算任务未生成", disabled=True, use_container_width=True)
with artifact_cols[6]:
    if computation_jobs_path.exists():
        st.download_button(
            "下载计算任务包索引",
            computation_jobs_path.read_bytes(),
            computation_jobs_path.name,
            "text/csv",
            use_container_width=True,
        )
    else:
        st.button("任务包索引未生成", disabled=True, use_container_width=True)
with artifact_cols[7]:
    if phase_smoke_csv.exists():
        st.download_button(
            "下载 TEFS 四相结果",
            phase_smoke_csv.read_bytes(),
            phase_smoke_csv.name,
            "text/csv",
            use_container_width=True,
        )
    else:
        st.button("TEFS 结果未导入", disabled=True, use_container_width=True)

df = _load_csv(dataset_path)
_section_header("dataset", "设计数据集", "样品级事实转化为可训练、可验证的材料设计表")
if not df.empty:
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("总行数", len(df))
    m2.metric("可建模行", int(pd.to_numeric(df.get("model_include", 0), errors="coerce").fillna(0).sum()))
    m3.metric("目标类型", df["target_property"].nunique() if "target_property" in df else 0)
    m4.metric("论文数", df["paper_id"].nunique() if "paper_id" in df else 0)
    m5.metric("材料体系", df["material_family"].nunique() if "material_family" in df else 0)

    if "target_property" in df:
        counts_df = df["target_property"].value_counts().reset_index()
        counts_df.columns = ["target_property", "count"]
        st.bar_chart(counts_df, x="target_property", y="count")
    if "benchmark_tier" in df:
        tier_df = df["benchmark_tier"].fillna("未分层").astype(str).value_counts().reset_index()
        tier_df.columns = ["benchmark_tier", "count"]
        st.bar_chart(tier_df, x="benchmark_tier", y="count")
    st.dataframe(df.head(300), use_container_width=True, hide_index=True)
else:
    st.info("还没有设计数据集。可以点击上面的“构建数据集”或“启动完整流水线”。")

_section_header("models", "模型验证与设计建议", "训练/验证集评估、模型对比和证据约束候选推荐")
if metrics:
    st.markdown("### 基础模型指标")
    metrics_df = pd.DataFrame(metrics.get("targets", []))
    st.dataframe(metrics_df, use_container_width=True, hide_index=True)
    trained = metrics_df[metrics_df.get("status", "") == "trained"] if not metrics_df.empty else pd.DataFrame()
    if not trained.empty and "mae" in trained:
        st.bar_chart(trained, x="target_property", y="mae")

model_compare = _load_csv(model_compare_csv)
if not model_compare.empty:
    st.markdown("### 预测模型验证结果")
    st.caption(
        "以下结果使用训练集/验证集划分评判。accuracy 指验证集容差命中率；Pr/2Pr 主看 MAE、RMSE、R2 和 within_10uC_cm2。"
    )
    rename_map = {
        "model": "model_name",
        "within_5_uC_cm2_accuracy": "within_5uC_cm2",
        "within_10_uC_cm2_accuracy": "within_10uC_cm2",
    }
    model_compare = model_compare.rename(columns=rename_map)
    if "dataset_name" not in model_compare:
        model_compare["dataset_name"] = "strong_relevant"
    display_cols = [
        "dataset_name",
        "target_property",
        "model_name",
        "rows",
        "train_rows",
        "validation_rows",
        "mae",
        "rmse",
        "r2",
        "baseline_mae",
        "improvement_vs_baseline",
        "within_5uC_cm2",
        "within_10uC_cm2",
    ]
    available_cols = [col for col in display_cols if col in model_compare.columns]
    display = model_compare[available_cols].copy()
    for col in ["mae", "rmse", "r2", "baseline_mae", "improvement_vs_baseline"]:
        if col in display:
            display[col] = pd.to_numeric(display[col], errors="coerce").round(4)
    for col in ["within_5uC_cm2", "within_10uC_cm2"]:
        if col in display:
            display[col] = (pd.to_numeric(display[col], errors="coerce") * 100).round(2)
    st.dataframe(display, use_container_width=True, hide_index=True)
else:
    st.info("还没有强相关预测模型验证结果。可以点击“强相关模型验证”运行 SVR、树模型和线性模型对比。")

candidates = _load_csv(candidates_path)
if not candidates.empty:
    st.markdown("### 证据约束设计建议")
    st.caption("排序综合考虑预测性能、模型不确定性、可实现性和相似文献证据。")
    st.dataframe(candidates, use_container_width=True, hide_index=True)
else:
    st.info("还没有设计建议候选。训练模型后点击“生成设计建议候选”。")

_section_header("compute", "计算闭环", "TEFS/VASP 验证、结构图、计算任务包和结果回写")
phase_results = _load_csv(phase_smoke_csv)
if not phase_results.empty:
    st.markdown("### TEFS 计算验证：HfO2 四相稳定性 smoke test")
    st.caption(
        "这是计算闭环的第一道可复现验证门：用 Materials Project 来源结构在 TEFS/VASP 上完成 relax + static，"
        "把相对能量作为 computed descriptor 回写 KG/benchmark。结果用于验证工作流，不作为最终 DFT benchmark。"
    )
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("计算相数", len(phase_results))
    p2.metric("收敛结果", int((phase_results.get("quality_status", "") == "ok").sum()))
    if "relative_energy_meV_per_fu" in phase_results:
        o_phase = phase_results[phase_results["phase"].astype(str) == "orthorhombic"]
        p3.metric(
            "o 相相对 m 相",
            f"{float(o_phase['relative_energy_meV_per_fu'].iloc[0]):.1f} meV/HfO2" if not o_phase.empty else "NA",
        )
    else:
        p3.metric("o 相相对 m 相", "NA")
    p4.metric("计算平台", "TEFS/VASP")

    validation_table = _load_csv(phase_compute_validation_table)
    quality_gates = _load_csv(phase_compute_quality_gates)
    if phase_compute_validation_card.exists() or not quality_gates.empty:
        st.markdown("**计算验证卡片**")
        st.caption("这部分是面向论文方法和复现的质量门槛：能说明哪些结果可以用于闭环展示，哪些还需要后续正式收敛性检查。")
        if not quality_gates.empty:
            gate_cols = st.columns(4)
            file_gate = quality_gates[quality_gates["gate"].astype(str) == "file_completeness"]
            gate_cols[0].metric("质量门槛", len(quality_gates))
            gate_cols[1].metric("通过", int((quality_gates["status"] == "pass").sum()))
            gate_cols[2].metric("需跟进", int((quality_gates["status"].astype(str).str.contains("followup|review", regex=True)).sum()))
            gate_cols[3].metric("文件完整性", file_gate["status"].iloc[0] if not file_gate.empty else "NA")
            st.dataframe(quality_gates, use_container_width=True, hide_index=True)
        if not validation_table.empty:
            card_cols = [
                "phase",
                "energy_eV_per_HfO2",
                "relative_energy_meV_per_HfO2",
                "volume_A3_per_HfO2",
                "max_force_eV_A",
                "external_pressure_kB",
                "ionic_steps",
                "lattice_check_status",
                "force_gate_status",
                "pressure_gate_status",
            ]
            display_validation = validation_table[[col for col in card_cols if col in validation_table.columns]].copy()
            for col in display_validation.select_dtypes(include=["number"]).columns:
                display_validation[col] = display_validation[col].round(4)
            st.dataframe(display_validation, use_container_width=True, hide_index=True)
        if phase_compute_validation_card.exists():
            with st.expander("查看计算验证卡片 Markdown", expanded=False):
                st.markdown(phase_compute_validation_card.read_text(encoding="utf-8"))

    chart_cols = st.columns(2)
    with chart_cols[0]:
        if phase_smoke_chart.exists():
            st.image(str(phase_smoke_chart), caption="四相相对能量排序")
    with chart_cols[1]:
        if phase_smoke_landscape.exists():
            st.image(str(phase_smoke_landscape), caption="相对 monoclinic 的能量景观")
    display_cols = [
        "phase",
        "atoms",
        "n_formula_units",
        "total_energy_eV",
        "energy_eV_per_fu",
        "relative_energy_meV_per_fu",
        "quality_status",
        "method",
    ]
    st.dataframe(
        phase_results[[col for col in display_cols if col in phase_results.columns]],
        use_container_width=True,
        hide_index=True,
    )

    structural_results = _load_csv(phase_smoke_structural_csv)
    convergence_results = _load_csv(phase_smoke_convergence_csv)
    st.markdown("**真实 VASP 输出与相结构**")
    structure_tab, convergence_tab, table_tab = st.tabs(["四相结构", "弛豫与能量-体积", "结构参数表"])
    with structure_tab:
        st.caption("结构图由 relaxed CONTCAR 解析得到；VESTA 包含四个相的 CIF，可直接在 VESTA 中打开。")
        if phase_smoke_structure_chart.exists():
            st.image(str(phase_smoke_structure_chart), caption="CONTCAR 解析的 HfO2 四相 relaxed 结构投影")
        vesta_cols = st.columns(3)
        with vesta_cols[0]:
            if phase_smoke_vesta_zip.exists():
                st.download_button(
                    "下载 VESTA CIF 结构包",
                    phase_smoke_vesta_zip.read_bytes(),
                    phase_smoke_vesta_zip.name,
                    "application/zip",
                    use_container_width=True,
                )
        with vesta_cols[1]:
            if (phase_smoke_output_dir / "hfo2_phase_relaxed_structures.pdf").exists():
                fig_path = phase_smoke_output_dir / "hfo2_phase_relaxed_structures.pdf"
                st.download_button("下载结构图 PDF", fig_path.read_bytes(), fig_path.name, "application/pdf", use_container_width=True)
        with vesta_cols[2]:
            st.caption("VESTA 已安装在 /Applications/VESTA.app；首次打开如有 macOS 安全提示，点击允许即可。")
    with convergence_tab:
        real_chart_cols = st.columns(3)
        with real_chart_cols[0]:
            if phase_smoke_convergence_chart.exists():
                st.image(str(phase_smoke_convergence_chart), caption="relax.output 离子弛豫能量轨迹")
        with real_chart_cols[1]:
            if phase_smoke_volume_chart.exists():
                st.image(str(phase_smoke_volume_chart), caption="CONTCAR 最终晶胞体积")
        with real_chart_cols[2]:
            if phase_smoke_energy_volume_chart.exists():
                st.image(str(phase_smoke_energy_volume_chart), caption="相对能量-体积描述符")
        if not convergence_results.empty:
            st.dataframe(convergence_results.head(120), use_container_width=True, hide_index=True)
    with table_tab:
        if not structural_results.empty:
            structure_cols = [
                "phase",
                "composition",
                "atoms",
                "hfo2_units",
                "energy_eV_per_HfO2",
                "relative_energy_meV_per_HfO2",
                "a_A",
                "b_A",
                "c_A",
                "alpha_deg",
                "beta_deg",
                "gamma_deg",
                "volume_A3_per_HfO2",
                "external_pressure_kB",
                "max_force_eV_A",
                "fermi_eV",
                "quality_status",
            ]
            rounded = structural_results[[col for col in structure_cols if col in structural_results.columns]].copy()
            numeric_cols = rounded.select_dtypes(include=["number"]).columns
            rounded[numeric_cols] = rounded[numeric_cols].round(4)
            st.dataframe(rounded, use_container_width=True, hide_index=True)
        else:
            st.info("还没有结构参数表。运行 scripts/build_hfo2_phase_smoke_assets.py 后会生成。")

    st.markdown("**论文级 Figure 草稿**")
    st.caption("多面板图按投稿图标准整理：Times New Roman、低饱和色、panel label、矢量 PDF/SVG、灰度可读预览。")
    if publication_figure_png.exists():
        st.image(str(publication_figure_png), caption="Figure 1 draft: HfO2 phase validation descriptors")
    publication_cols = st.columns(4)
    with publication_cols[0]:
        if publication_figure_pdf.exists():
            st.download_button(
                "下载 Figure PDF",
                publication_figure_pdf.read_bytes(),
                publication_figure_pdf.name,
                "application/pdf",
                use_container_width=True,
            )
    with publication_cols[1]:
        if publication_figure_svg.exists():
            st.download_button(
                "下载 Figure SVG",
                publication_figure_svg.read_bytes(),
                publication_figure_svg.name,
                "image/svg+xml",
                use_container_width=True,
            )
    with publication_cols[2]:
        if publication_caption.exists():
            st.download_button(
                "下载英文图注草稿",
                publication_caption.read_bytes(),
                publication_caption.name,
                "text/markdown",
                use_container_width=True,
            )
    with publication_cols[3]:
        if publication_figure_svg.exists():
            st.caption("建议用 Inkscape 打开 SVG 做最终字体/线宽/版面微调。")

    download_cols = st.columns(6)
    with download_cols[0]:
        if computed_descriptors_path.exists():
            st.download_button(
                "下载 computed descriptors",
                computed_descriptors_path.read_bytes(),
                computed_descriptors_path.name,
                "text/csv",
                use_container_width=True,
            )
    with download_cols[1]:
        if phase_smoke_report.exists():
            st.download_button(
                "下载计算验证摘要",
                phase_smoke_report.read_bytes(),
                phase_smoke_report.name,
                "text/markdown",
                use_container_width=True,
            )
    with download_cols[2]:
        if (PROJECT_ROOT / "outputs" / "phase_smoke" / "hfo2_phase_relative_energy.pdf").exists():
            fig_path = PROJECT_ROOT / "outputs" / "phase_smoke" / "hfo2_phase_relative_energy.pdf"
            st.download_button("下载能量图 PDF", fig_path.read_bytes(), fig_path.name, "application/pdf", use_container_width=True)
    with download_cols[3]:
        if (PROJECT_ROOT / "outputs" / "phase_smoke" / "hfo2_phase_energy_landscape.pdf").exists():
            fig_path = PROJECT_ROOT / "outputs" / "phase_smoke" / "hfo2_phase_energy_landscape.pdf"
            st.download_button("下载景观图 PDF", fig_path.read_bytes(), fig_path.name, "application/pdf", use_container_width=True)
    with download_cols[4]:
        if phase_smoke_structural_csv.exists():
            st.download_button(
                "下载结构参数 CSV",
                phase_smoke_structural_csv.read_bytes(),
                phase_smoke_structural_csv.name,
                "text/csv",
                use_container_width=True,
            )
    with download_cols[5]:
        if phase_smoke_real_report.exists():
            st.download_button(
                "下载真实 VASP 输出摘要",
                phase_smoke_real_report.read_bytes(),
                phase_smoke_real_report.name,
                "text/markdown",
                use_container_width=True,
            )
    validation_download_cols = st.columns(5)
    with validation_download_cols[0]:
        if phase_compute_validation_card.exists():
            st.download_button(
                "下载验证卡片",
                phase_compute_validation_card.read_bytes(),
                phase_compute_validation_card.name,
                "text/markdown",
                use_container_width=True,
            )
    with validation_download_cols[1]:
        if phase_compute_validation_table.exists():
            st.download_button(
                "下载验证结果表",
                phase_compute_validation_table.read_bytes(),
                phase_compute_validation_table.name,
                "text/csv",
                use_container_width=True,
            )
    with validation_download_cols[2]:
        if phase_compute_quality_gates.exists():
            st.download_button(
                "下载质量门槛表",
                phase_compute_quality_gates.read_bytes(),
                phase_compute_quality_gates.name,
                "text/csv",
                use_container_width=True,
            )
    with validation_download_cols[3]:
        if phase_compute_file_inventory.exists():
            st.download_button(
                "下载文件清单",
                phase_compute_file_inventory.read_bytes(),
                phase_compute_file_inventory.name,
                "text/csv",
                use_container_width=True,
            )
    with validation_download_cols[4]:
        if phase_compute_validation_json.exists():
            st.download_button(
                "下载验证 JSON",
                phase_compute_validation_json.read_bytes(),
                phase_compute_validation_json.name,
                "application/json",
                use_container_width=True,
            )
else:
    st.info("还没有导入 TEFS 四相计算结果。完成云端 smoke test 后运行 scripts/build_hfo2_phase_smoke_assets.py。")

computation_tasks = _load_csv(computation_tasks_path)
if not computation_tasks.empty:
    st.markdown("### 计算反馈任务")
    st.caption(
        "这些任务只做规划，不会在本机或云端自动启动。人工确认结构、参数、费用和队列后，才能接入腾讯云运行。"
    )
    c1, c2, c3 = st.columns(3)
    c1.metric("任务数", len(computation_tasks))
    c2.metric("任务类型", computation_tasks["task_family"].nunique() if "task_family" in computation_tasks else 0)
    c3.metric(
        "安全状态",
        computation_tasks["safety_status"].mode().iloc[0]
        if "safety_status" in computation_tasks and not computation_tasks["safety_status"].empty
        else "planned",
    )
    if "task_family" in computation_tasks:
        family_df = computation_tasks["task_family"].value_counts().reset_index()
        family_df.columns = ["task_family", "count"]
        st.bar_chart(family_df, x="task_family", y="count")
    st.dataframe(computation_tasks.head(120), use_container_width=True, hide_index=True)
    if computation_report_path.exists():
        with st.expander("计算反馈计划报告", expanded=False):
            st.markdown(computation_report_path.read_text(encoding="utf-8"))
else:
    st.info("还没有计算反馈任务。生成设计建议候选后点击“规划计算验证任务”。")

job_summary = computation_job_summary()
jobs = list_computation_jobs(limit=120)
if jobs:
    st.markdown("### 自动计算验证任务包")
    st.caption(
        "任务包是面向腾讯云/本地集群的可复现输入目录；当前状态仍是安全准备层，不自动运行计算。"
    )
    j1, j2, j3 = st.columns(3)
    j1.metric("任务包", job_summary.get("jobs", 0))
    j2.metric("已导入结果", job_summary.get("results", 0))
    j3.metric("状态类型", len(job_summary.get("by_status", {})))
    st.dataframe(pd.DataFrame(jobs), use_container_width=True, hide_index=True)
    if computation_jobs_report_path.exists():
        with st.expander("计算任务包报告", expanded=False):
            st.markdown(computation_jobs_report_path.read_text(encoding="utf-8"))
    if computation_workflow_report_path.exists():
        with st.expander("自动计算工作流报告", expanded=False):
            st.markdown(computation_workflow_report_path.read_text(encoding="utf-8"))
