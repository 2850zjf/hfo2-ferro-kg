from __future__ import annotations

import argparse
import html
import json
import re
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


PROGRESS_RE = re.compile(
    r"processed=(?P<processed>\d+)\s+"
    r"candidates=(?P<candidates>\d+)\s+"
    r"llm_used=(?P<llm_used>\d+)\s+"
    r"llm_failed=(?P<llm_failed>\d+)\s+"
    r"empty=(?P<empty>\d+)\s+"
    r"errors=(?P<errors>\d+)"
)
START_RE = re.compile(r"started\s+(?P<stamp>\d{4}-\d{2}-\d{2}T[^\s]+)")


def latest_progress_log(log_dir: Path) -> Path | None:
    logs = sorted(
        log_dir.glob("full_qwen_extraction_*.log"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return logs[0] if logs else None


def parse_log(path: Path, total: int) -> dict[str, object]:
    if not path.exists():
        return {"missing": True, "log": str(path), "total": total}

    text = path.read_text(encoding="utf-8", errors="replace")
    # Windows PowerShell can append Tee-Object output with UTF-16-style NUL
    # padding. Strip NULs so progress lines remain machine-readable.
    if "\x00" in text:
        text = text.replace("\x00", "")
    lines = text.splitlines()
    progress_matches = [PROGRESS_RE.search(line) for line in lines]
    progress_matches = [match for match in progress_matches if match]
    latest = progress_matches[-1] if progress_matches else None
    values = {
        "processed": 0,
        "candidates": 0,
        "llm_used": 0,
        "llm_failed": 0,
        "empty": 0,
        "errors": 0,
    }
    if latest:
        values.update({key: int(value) for key, value in latest.groupdict().items()})

    started_at = None
    for line in lines[:10]:
        match = START_RE.search(line)
        if match:
            try:
                started_at = datetime.fromisoformat(match.group("stamp"))
            except ValueError:
                started_at = None
            break

    now = datetime.now(started_at.tzinfo) if started_at else datetime.now()
    elapsed_seconds = max(0.0, (now - started_at).total_seconds()) if started_at else 0.0
    rate = values["processed"] / elapsed_seconds if elapsed_seconds and values["processed"] else 0.0
    remaining = max(0, total - values["processed"])
    eta_seconds = remaining / rate if rate else None
    percent = min(100.0, round(values["processed"] / total * 100, 1)) if total else 0.0

    completed = any("batch wrapper done" in line.lower() for line in lines[-20:])
    extraction_done = any("extraction exit code" in line.lower() for line in lines[-50:])

    return {
        "missing": False,
        "log": str(path),
        "total": total,
        "percent": percent,
        "remaining": remaining,
        "elapsed": format_duration(elapsed_seconds),
        "eta": format_duration(eta_seconds) if eta_seconds is not None else "估算中",
        "completed": completed,
        "extraction_done": extraction_done,
        "tail": "\n".join(lines[-80:]),
        **values,
    }


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "估算中"
    seconds = int(max(0, seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def render_html(data: dict[str, object], refresh: int) -> bytes:
    status = progress_status(data)
    percent = float(data.get("percent", 0) or 0)
    tail = html.escape(str(data.get("tail", "")))
    log_path = html.escape(str(data.get("log", "")))

    body = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HfO2-FerroKG 全量抽取进度</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #0f1117; color: #f6f7fb; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 32px 24px 48px; }}
    h1 {{ margin: 0 0 8px; font-size: 30px; }}
    .muted {{ color: #a4acb9; }}
    .bar {{ height: 18px; background: #2a2f3a; border-radius: 999px; overflow: hidden; margin: 24px 0; }}
    .fill {{ width: {percent}%; height: 100%; background: linear-gradient(90deg, #5bd489, #69a7ff); transition: width 0.35s ease; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 14px; }}
    .card {{ background: #181c24; border: 1px solid #303746; border-radius: 8px; padding: 16px; }}
    .label {{ color: #a4acb9; font-size: 13px; }}
    .value {{ font-size: 26px; font-weight: 700; margin-top: 6px; }}
    pre {{ white-space: pre-wrap; background: #080a0f; border: 1px solid #303746; border-radius: 8px; padding: 16px; line-height: 1.5; max-height: 520px; overflow: auto; }}
    a {{ color: #7bb2ff; }}
  </style>
</head>
<body>
<main>
  <h1>HfO2-FerroKG 全量抽取进度</h1>
  <div class="muted">状态：<span id="status">{html.escape(status)}</span> · 每 {refresh} 秒静默更新 · 日志：<span id="log-path">{log_path}</span></div>
  <div class="bar"><div class="fill" id="bar-fill"></div></div>
  <div class="grid">
    <div class="card"><div class="label">进度</div><div class="value" id="percent">{percent}%</div></div>
    <div class="card"><div class="label">已处理 chunk</div><div class="value" id="processed">{data.get("processed", 0)} / {data.get("total", 0)}</div></div>
    <div class="card"><div class="label">新增候选</div><div class="value" id="candidates">{data.get("candidates", 0)}</div></div>
    <div class="card"><div class="label">空结果</div><div class="value" id="empty">{data.get("empty", 0)}</div></div>
    <div class="card"><div class="label">模型失败</div><div class="value" id="llm-failed">{data.get("llm_failed", 0)}</div></div>
    <div class="card"><div class="label">程序错误</div><div class="value" id="errors">{data.get("errors", 0)}</div></div>
    <div class="card"><div class="label">已用时间</div><div class="value" id="elapsed">{data.get("elapsed", "0s")}</div></div>
    <div class="card"><div class="label">预计剩余</div><div class="value" id="eta">{data.get("eta", "估算中")}</div></div>
  </div>
  <h2>实时日志</h2>
  <pre id="tail">{tail}</pre>
</main>
<script>
const refreshMs = {refresh * 1000};
function setText(id, value) {{
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}}
function updateProgress(data) {{
  const percent = Number(data.percent || 0).toFixed(1);
  setText("status", data.status || "运行中");
  setText("log-path", data.log || "");
  setText("percent", `${{percent}}%`);
  setText("processed", `${{data.processed || 0}} / ${{data.total || 0}}`);
  setText("candidates", data.candidates || 0);
  setText("empty", data.empty || 0);
  setText("llm-failed", data.llm_failed || 0);
  setText("errors", data.errors || 0);
  setText("elapsed", data.elapsed || "0s");
  setText("eta", data.eta || "估算中");
  setText("tail", data.tail || "");
  const fill = document.getElementById("bar-fill");
  if (fill) fill.style.width = `${{percent}}%`;
}}
async function refreshProgress() {{
  try {{
    const response = await fetch("/api", {{ cache: "no-store" }});
    if (response.ok) updateProgress(await response.json());
  }} catch (error) {{
    console.warn("progress refresh failed", error);
  }}
}}
setInterval(refreshProgress, refreshMs);
refreshProgress();
</script>
</body>
</html>"""
    return body.encode("utf-8")


def progress_status(data: dict[str, object]) -> str:
    if data.get("completed"):
        return "已完成"
    if data.get("extraction_done"):
        return "抽取完成，正在后处理"
    if data.get("missing"):
        return "等待日志"
    return "运行中"


def render_json(data: dict[str, object]) -> bytes:
    payload = dict(data)
    payload["status"] = progress_status(data)
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def make_handler(log_path: Path | None, log_dir: Path, total: int, refresh: int):
    class ProgressHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path not in {"/", "/progress", "/api"}:
                self.send_error(404)
                return
            query = parse_qs(parsed.query)
            selected_log = Path(query["log"][0]) if "log" in query else log_path
            if selected_log is None:
                selected_log = latest_progress_log(log_dir)
            data = parse_log(selected_log, total) if selected_log else {"missing": True, "total": total}
            if parsed.path == "/api":
                payload = render_json(data)
                content_type = "application/json; charset=utf-8"
            else:
                payload = render_html(data, refresh)
                content_type = "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    return ProgressHandler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, default=None)
    parser.add_argument("--log-dir", type=Path, default=Path("logs"))
    parser.add_argument("--total", type=int, default=3454)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8502)
    parser.add_argument("--refresh", type=int, default=5)
    args = parser.parse_args()

    handler = make_handler(args.log, args.log_dir, args.total, args.refresh)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Progress dashboard: http://{args.host}:{args.port}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
