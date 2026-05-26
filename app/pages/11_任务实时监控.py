from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.progress_monitor import monitor_snapshot


MONITOR_HOST = "127.0.0.1"
MONITOR_PORT = 8502
MONITOR_URL = f"http://{MONITOR_HOST}:{MONITOR_PORT}/"


def _port_open(host: str = MONITOR_HOST, port: int = MONITOR_PORT) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.4)
            return sock.connect_ex((host, port)) == 0
    except OSError:
        return False


def _start_monitor_server() -> bool:
    if _port_open():
        return True
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.Popen(
            [
                sys.executable,
                "scripts/serve_monitor.py",
                "--host",
                MONITOR_HOST,
                "--port",
                str(MONITOR_PORT),
                "--refresh",
                "5",
            ],
            cwd=PROJECT_ROOT,
            stdout=(log_dir / "monitor_server.out.log").open("a", encoding="utf-8"),
            stderr=(log_dir / "monitor_server.err.log").open("a", encoding="utf-8"),
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
    except Exception as exc:
        st.error(f"监控服务启动失败：{exc}")
        return False
    return _port_open()


def _metric_row(tokens: dict, runtime: dict) -> None:
    cols = st.columns(5)
    cols[0].metric("当前步骤", runtime.get("active_step", "等待启动"))
    cols[1].metric("总运行时间", runtime.get("elapsed", "0s"))
    cols[2].metric("当前步骤运行", runtime.get("active_elapsed", "0s"))
    cols[3].metric("预计剩余", runtime.get("eta", "估算中"))
    cols[4].metric("速度", f"{runtime.get('rate_per_min', 0)} chunk/min")

    cols = st.columns(5)
    currency = tokens.get("currency", "CNY")
    cols[0].metric("真实总 token", f"{tokens.get('actual_total_tokens', 0):,}")
    cols[1].metric("输入 token", f"{tokens.get('actual_prompt_tokens', 0):,}")
    cols[2].metric("输出 token", f"{tokens.get('actual_completion_tokens', 0):,}")
    cols[3].metric("真实消费", f"{tokens.get('actual_cost', 0)} {currency}")
    cols[4].metric("估算消费", f"{tokens.get('estimated_cost', 0)} {currency}")


st.set_page_config(page_title="任务实时监控", layout="wide")
st.title("任务实时监控")
st.caption("这里是主站内嵌监控入口；下方大面板来自独立 8502 监控服务，数字更新时不会刷新整个 Streamlit 页面。")

server_ready = _start_monitor_server()
snapshot = monitor_snapshot()
tokens = snapshot.get("tokens", {})
runtime = snapshot.get("runtime", {})

_metric_row(tokens, runtime)
st.caption(str(tokens.get("note", "")))

left, right = st.columns([1, 1])
with left:
    st.link_button("打开独立无闪烁监控页", MONITOR_URL, use_container_width=True)
with right:
    if st.button("刷新主站摘要", use_container_width=True):
        st.rerun()

if snapshot.get("llm_pause"):
    pause = snapshot["llm_pause"]
    st.warning(f"LLM 已暂停：{pause.get('reason', '')}\n\n{pause.get('resume_hint', '')}")

st.subheader("后台任务")
processes = snapshot.get("processes", [])
if processes:
    st.dataframe(
        pd.DataFrame(processes)[["pid", "task", "command"]],
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("当前没有检测到后台任务。")

st.subheader("内嵌实时面板")
if server_ready:
    components.iframe(MONITOR_URL, height=900, scrolling=True)
else:
    st.error("8502 监控服务暂时没有启动成功。请点上方刷新，或稍等几秒后再进入本页。")
