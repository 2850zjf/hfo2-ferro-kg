from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.support import PROJECT_ROOT, render_top_nav
from backend.db.session import connect
from backend.services.paddleocr_parser import (
    DEFAULT_MODEL,
    DEFAULT_OPTIONAL_PAYLOAD,
    default_output_root,
    get_paddleocr_token,
    parse_document_with_paddleocr,
    result_to_dict,
)
from backend.services.pdf_parser import parse_pending_pdfs
from backend.services.table_extractor import extract_tables


st.set_page_config(page_title="PDF 解析", layout="wide", initial_sidebar_state="collapsed")
render_top_nav("文献处理")
st.title("PDF 解析")

limit = st.number_input("本轮解析 PDF 数量", min_value=1, max_value=500, value=5)
force = st.toggle("重新解析已解析 PDF", value=False)

if st.button("开始解析"):
    stats = parse_pending_pdfs(limit=int(limit), force=force)
    st.success(f"解析完成：{stats}")

if st.button("抽取表格"):
    stats = extract_tables(limit_pdfs=int(limit))
    st.success(f"表格抽取完成：{stats}")

st.divider()

st.subheader("PaddleOCR-VL 文档解析")
st.caption(
    "适合扫描版 PDF、复杂图文排版和需要 Markdown/图片输出的文档。默认只保存到 data/paddleocr，不直接覆盖主数据库解析结果。"
)
token_from_env = bool(get_paddleocr_token())
token_label = "PaddleOCR Token（已检测到环境变量，可留空）" if token_from_env else "PaddleOCR Token（不会保存到代码或文件）"
ocr_source = st.text_input("本地文件路径或文件 URL", placeholder="/path/to/paper.pdf 或 https://example.com/paper.pdf")
ocr_token = st.text_input(token_label, type="password", value="")
ocr_cols = st.columns(4)
with ocr_cols[0]:
    ocr_model = st.text_input("模型", value=DEFAULT_MODEL)
with ocr_cols[1]:
    ocr_orientation = st.toggle("方向分类", value=DEFAULT_OPTIONAL_PAYLOAD["useDocOrientationClassify"])
with ocr_cols[2]:
    ocr_unwarping = st.toggle("文档矫正", value=DEFAULT_OPTIONAL_PAYLOAD["useDocUnwarping"])
with ocr_cols[3]:
    ocr_chart = st.toggle("图表识别", value=DEFAULT_OPTIONAL_PAYLOAD["useChartRecognition"])

if st.button("用 PaddleOCR-VL 解析", type="primary", use_container_width=True):
    if not ocr_source.strip():
        st.error("请先填写本地文件路径或文件 URL。")
    else:
        optional_payload = {
            "useDocOrientationClassify": bool(ocr_orientation),
            "useDocUnwarping": bool(ocr_unwarping),
            "useChartRecognition": bool(ocr_chart),
        }
        try:
            with st.spinner("正在提交 PaddleOCR 任务并等待结果，页数多时会比较久..."):
                result = parse_document_with_paddleocr(
                    source=ocr_source.strip(),
                    token=ocr_token.strip() or None,
                    model=ocr_model.strip() or DEFAULT_MODEL,
                    optional_payload=optional_payload,
                )
            st.success(f"PaddleOCR 解析完成：{result.pages} 页")
            st.json(result_to_dict(result))
        except Exception as exc:
            st.error(f"PaddleOCR 解析失败：{exc}")

ocr_root = default_output_root()
if ocr_root.exists():
    result_dirs = sorted([path for path in ocr_root.iterdir() if path.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)
    if result_dirs:
        st.markdown("**最近 PaddleOCR 输出**")
        rows = []
        for path in result_dirs[:20]:
            manifest_path = path / "manifest.json"
            pages = ""
            source = ""
            job_id = ""
            if manifest_path.exists():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    pages = manifest.get("pages", "")
                    source = manifest.get("source", "")
                    job_id = manifest.get("job_id", "")
                except Exception:
                    pass
            rows.append(
                {
                    "output_dir": str(path),
                    "job_id": job_id,
                    "pages": pages,
                    "source": source,
                    "manifest": str(manifest_path) if manifest_path.exists() else "",
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

st.divider()
st.subheader("PaddleOCR-VL 全量 Markdown 转换")
st.caption(
    "把已入库且非重复、非无关的 PDF 批量转成 PaddleOCR Markdown。输出到 "
    "`data/paddleocr/full_pdf_markdown/<pdf_id>/`，默认断点跳过已完成文献。"
)


def _paddleocr_batch_status() -> dict[str, object]:
    output_root = PROJECT_ROOT / "data" / "paddleocr" / "full_pdf_markdown"
    done_dirs = []
    if output_root.exists():
        for manifest_path in output_root.glob("*/manifest.json"):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if manifest.get("state") == "done" and manifest.get("markdown_files"):
                done_dirs.append(manifest_path.parent)
    latest_log_dirs = sorted((PROJECT_ROOT / "logs").glob("paddleocr_batch_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    latest_status = latest_log_dirs[0] / "status.csv" if latest_log_dirs else None
    latest_summary = latest_log_dirs[0] / "summary.json" if latest_log_dirs else None
    return {
        "output_root": output_root,
        "done_count": len(done_dirs),
        "latest_run_dir": latest_log_dirs[0] if latest_log_dirs else None,
        "latest_status": latest_status if latest_status and latest_status.exists() else None,
        "latest_summary": latest_summary if latest_summary and latest_summary.exists() else None,
    }


with connect() as conn:
    eligible_pdfs = conn.execute(
        "SELECT COUNT(*) FROM pdf_files WHERE parse_status = 'parsed' AND is_duplicate = 0"
    ).fetchone()[0]

batch_status = _paddleocr_batch_status()
batch_cols = st.columns(4)
batch_cols[0].metric("待转换范围", eligible_pdfs, "parsed & 非重复")
batch_cols[1].metric("已完成 Markdown", batch_status["done_count"])
batch_cols[2].metric("剩余估算", max(0, int(eligible_pdfs) - int(batch_status["done_count"])))
batch_cols[3].metric("模式", "断点继续")

batch_token_from_env = bool(get_paddleocr_token())
batch_token = st.text_input(
    "PaddleOCR Token（仅用于本次后台子进程，不保存到文件）"
    if not batch_token_from_env
    else "PaddleOCR Token（已检测到环境变量，可留空）",
    type="password",
    key="paddleocr_batch_token",
)
batch_limit = st.number_input("本次最多提交 PDF 数量（0 表示全量继续）", min_value=0, max_value=2000, value=0, step=10)
batch_options = st.columns(4)
with batch_options[0]:
    batch_force = st.toggle("强制重跑已完成", value=False)
with batch_options[1]:
    batch_include_excluded = st.toggle("包含无关隔离文献", value=False)
with batch_options[2]:
    batch_doc_unwarping = st.toggle("批量文档矫正", value=False)
with batch_options[3]:
    batch_chart = st.toggle("批量图表识别", value=False)

if st.button("启动 / 继续全量 PaddleOCR Markdown 转换", type="primary", use_container_width=True):
    token_value = batch_token.strip() or get_paddleocr_token()
    if not token_value:
        st.error("没有检测到 PaddleOCR Token。请在上方输入 token，或在 `.env` 里设置 `PADDLEOCR_TOKEN`。")
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_log = PROJECT_ROOT / "logs" / f"paddleocr_batch_submit_{stamp}.out.log"
        err_log = PROJECT_ROOT / "logs" / f"paddleocr_batch_submit_{stamp}.err.log"
        command = [
            sys.executable,
            "pipelines/51_parse_pdfs_with_paddleocr_batch.py",
            "--output-root",
            str(PROJECT_ROOT / "data" / "paddleocr" / "full_pdf_markdown"),
            "--model",
            DEFAULT_MODEL,
            "--sleep-between",
            "1",
            "--max-wait",
            "3600",
        ]
        if int(batch_limit) > 0:
            command.extend(["--limit", str(int(batch_limit))])
        if batch_force:
            command.append("--force")
        if batch_include_excluded:
            command.append("--include-excluded")
        if batch_doc_unwarping:
            command.append("--doc-unwarping")
        if batch_chart:
            command.append("--chart-recognition")

        env = os.environ.copy()
        env["PADDLEOCR_TOKEN"] = str(token_value)
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdout=out_log.open("w", encoding="utf-8"),
            stderr=err_log.open("w", encoding="utf-8"),
            env=env,
            start_new_session=True,
        )
        st.success(f"已启动后台 PaddleOCR 全量转换：PID {process.pid}")
        st.caption(f"stdout: {out_log}")
        st.caption(f"stderr: {err_log}")

if batch_status["latest_run_dir"]:
    st.markdown("**最近批处理记录**")
    st.caption(str(batch_status["latest_run_dir"]))
    if batch_status["latest_status"]:
        try:
            status_df = pd.read_csv(batch_status["latest_status"])
            st.dataframe(status_df.tail(30), use_container_width=True, hide_index=True)
        except Exception as exc:
            st.warning(f"读取最近状态失败：{exc}")
    if batch_status["latest_summary"]:
        try:
            st.json(json.loads(Path(batch_status["latest_summary"]).read_text(encoding="utf-8")))
        except Exception:
            pass

with connect() as conn:
    pdf_df = pd.read_sql_query(
        """
        SELECT file_name, page_count, text_page_count, blank_page_count, low_text_page_count,
               parse_quality_score, ocr_needed, parse_status, error_message
        FROM pdf_files
        ORDER BY file_name
        """,
        conn,
    )
    page_count = conn.execute("SELECT COUNT(*) FROM parsed_pages").fetchone()[0]
    table_count = conn.execute("SELECT COUNT(*) FROM pdf_tables").fetchone()[0]

st.metric("已解析页面", page_count)
st.metric("已抽取表格", table_count)
st.dataframe(pdf_df, use_container_width=True)
