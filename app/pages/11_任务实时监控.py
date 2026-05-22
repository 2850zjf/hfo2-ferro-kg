from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


MONITOR_URL = "http://127.0.0.1:8502/"


def _port_open(host: str = "127.0.0.1", port: int = 8502) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((host, port)) == 0


def _start_monitor_server() -> None:
    if _port_open():
        return
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [
            sys.executable,
            "scripts/serve_monitor.py",
            "--host",
            "127.0.0.1",
            "--port",
            "8502",
            "--refresh",
            "5",
        ],
        cwd=PROJECT_ROOT,
        stdout=(log_dir / "monitor_server.out.log").open("a", encoding="utf-8"),
        stderr=(log_dir / "monitor_server.err.log").open("a", encoding="utf-8"),
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )


st.set_page_config(page_title="任务实时监控", layout="wide")
st.title("任务实时监控")
st.caption("正在切换到独立无闪烁监控面板。")

_start_monitor_server()

st.link_button("打开无闪烁监控面板", MONITOR_URL, use_container_width=True)
components.html(
    f"""
    <script>
      window.parent.location.replace("{MONITOR_URL}");
    </script>
    <p>正在打开无闪烁监控面板。如果没有自动跳转，请点击上方按钮。</p>
    """,
    height=80,
)
