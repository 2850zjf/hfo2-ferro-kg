from __future__ import annotations

import argparse
import html
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.progress_monitor import monitor_snapshot


def _metric(label: str, value: object) -> str:
    return f"""<div class="card"><div class="label">{html.escape(label)}</div><div class="value" data-metric="{html.escape(label)}">{html.escape(str(value))}</div></div>"""


def render_html(refresh: int) -> bytes:
    snapshot = monitor_snapshot()
    counts = snapshot["counts"]
    runtime = snapshot["runtime"]
    tokens = snapshot["tokens"]
    logs = snapshot["logs"]
    latest_tail = logs[0]["tail"] if logs else ""
    latest_progress = logs[0]["progress"] if logs else {}
    body = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HfO2-FerroKG 实时监控</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #0f1117; color: #f6f7fb; }}
    main {{ max-width: 1380px; margin: 0 auto; padding: 26px 24px 42px; }}
    h1 {{ margin: 0; font-size: 34px; }}
    h2 {{ margin-top: 24px; font-size: 20px; }}
    .muted {{ color: #a5adbb; margin-top: 8px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin-top: 16px; }}
    .card {{ background: #181c24; border: 1px solid #303746; border-radius: 8px; padding: 14px 16px; }}
    .label {{ color: #a5adbb; font-size: 13px; }}
    .value {{ font-size: 25px; font-weight: 760; margin-top: 4px; word-break: break-word; }}
    .wide {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
    pre {{ white-space: pre-wrap; background: #080a0f; border: 1px solid #303746; border-radius: 8px; padding: 14px; line-height: 1.45; max-height: 520px; overflow: auto; }}
    table {{ width: 100%; border-collapse: collapse; background: #151922; border: 1px solid #303746; border-radius: 8px; overflow: hidden; }}
    th, td {{ text-align: left; padding: 10px; border-bottom: 1px solid #303746; vertical-align: top; }}
    th {{ color: #a5adbb; font-weight: 600; }}
    .ok {{ color: #5bd489; }}
    .warn {{ color: #ffd166; }}
    @media (max-width: 900px) {{ .wide {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
<main>
  <h1>HfO2-FerroKG 实时监控</h1>
  <div class="muted">无闪烁更新 · 每 {refresh} 秒刷新数据 · <span id="active-state"></span></div>

  <h2>时间与速度</h2>
  <div class="grid">
    {_metric("当前步骤", runtime.get("active_step", ""))}
    {_metric("已运行", runtime.get("elapsed", "0s"))}
    {_metric("预计剩余", runtime.get("eta", "估算中"))}
    {_metric("速度", f"{runtime.get('rate_per_min', 0)} chunk/min")}
  </div>

  <h2>Token 消耗</h2>
  <div class="grid">
    {_metric("真实总 token", tokens.get("actual_total_tokens", 0))}
    {_metric("真实输入 token", tokens.get("actual_prompt_tokens", 0))}
    {_metric("真实输出 token", tokens.get("actual_completion_tokens", 0))}
    {_metric("估算总 token", tokens.get("estimated_total_tokens", 0))}
    {_metric("结构化估算 token", tokens.get("estimated_structured_tokens", 0))}
    {_metric("Benchmark 估算 token", tokens.get("estimated_benchmark_tokens", 0))}
  </div>
  <div class="muted" id="token-note">{html.escape(str(tokens.get("note", "")))}</div>

  <h2>数据库进度</h2>
  <div class="grid">
    {_metric("PDF 总数", counts.get("pdf_files", 0))}
    {_metric("重复 PDF", counts.get("duplicate_pdfs", 0))}
    {_metric("已解析 PDF", counts.get("parsed_pdfs", 0))}
    {_metric("表格数", counts.get("pdf_tables", 0))}
    {_metric("图注/图片资产", counts.get("visual_assets", 0))}
    {_metric("chunk 总数", counts.get("document_chunks", 0))}
    {_metric("高价值 chunk", counts.get("high_value_chunks", 0))}
    {_metric("结构化候选", counts.get("extraction_candidates", 0))}
    {_metric("事实数", counts.get("reviewed_facts", 0))}
    {_metric("Benchmark 抽取", counts.get("benchmark_extractions", 0))}
    {_metric("Benchmark 有效", counts.get("benchmark_ok", 0))}
    {_metric("Benchmark 错误", counts.get("benchmark_error", 0))}
  </div>

  <div class="wide">
    <section>
      <h2>当前进程</h2>
      <div id="processes"></div>
    </section>
    <section>
      <h2>最新进度</h2>
      <pre id="progress-json">{html.escape(json.dumps(latest_progress, ensure_ascii=False, indent=2))}</pre>
    </section>
  </div>

  <h2>实时日志</h2>
  <pre id="log-tail">{html.escape(latest_tail[-6000:])}</pre>
</main>
<script>
const refreshMs = {refresh * 1000};
function fmt(v) {{ return v === null || v === undefined ? "" : String(v); }}
function setMetric(label, value) {{
  const el = document.querySelector(`[data-metric="${{label}}"]`);
  if (el) el.textContent = fmt(value);
}}
function renderProcesses(processes) {{
  const root = document.getElementById("processes");
  if (!root) return;
  if (!processes || !processes.length) {{
    root.innerHTML = '<div class="card warn">没有检测到后台任务。</div>';
    return;
  }}
  root.innerHTML = '<table><thead><tr><th>PID</th><th>任务</th><th>命令</th></tr></thead><tbody>' +
    processes.map(p => `<tr><td>${{p.pid}}</td><td>${{p.task}}</td><td>${{p.command}}</td></tr>`).join('') +
    '</tbody></table>';
}}
async function refresh() {{
  try {{
    const r = await fetch('/api', {{cache: 'no-store'}});
    const data = await r.json();
    const c = data.counts || {{}};
    const rt = data.runtime || {{}};
    const tk = data.tokens || {{}};
    const logs = data.logs || [];
    const latest = logs.length ? logs[0] : {{}};
    document.getElementById("active-state").textContent = data.active ? "后台任务运行中" : "当前无后台任务";
    setMetric("当前步骤", rt.active_step || "");
    setMetric("已运行", rt.elapsed || "0s");
    setMetric("预计剩余", rt.eta || "估算中");
    setMetric("速度", `${{rt.rate_per_min || 0}} chunk/min`);
    setMetric("真实总 token", tk.actual_total_tokens || 0);
    setMetric("真实输入 token", tk.actual_prompt_tokens || 0);
    setMetric("真实输出 token", tk.actual_completion_tokens || 0);
    setMetric("估算总 token", tk.estimated_total_tokens || 0);
    setMetric("结构化估算 token", tk.estimated_structured_tokens || 0);
    setMetric("Benchmark 估算 token", tk.estimated_benchmark_tokens || 0);
    document.getElementById("token-note").textContent = tk.note || "";
    setMetric("PDF 总数", c.pdf_files || 0);
    setMetric("重复 PDF", c.duplicate_pdfs || 0);
    setMetric("已解析 PDF", c.parsed_pdfs || 0);
    setMetric("表格数", c.pdf_tables || 0);
    setMetric("图注/图片资产", c.visual_assets || 0);
    setMetric("chunk 总数", c.document_chunks || 0);
    setMetric("高价值 chunk", c.high_value_chunks || 0);
    setMetric("结构化候选", c.extraction_candidates || 0);
    setMetric("事实数", c.reviewed_facts || 0);
    setMetric("Benchmark 抽取", c.benchmark_extractions || 0);
    setMetric("Benchmark 有效", c.benchmark_ok || 0);
    setMetric("Benchmark 错误", c.benchmark_error || 0);
    renderProcesses(data.processes || []);
    document.getElementById("progress-json").textContent = JSON.stringify(latest.progress || {{}}, null, 2);
    document.getElementById("log-tail").textContent = (latest.tail || "").slice(-6000);
  }} catch (e) {{
    console.warn(e);
  }}
}}
setInterval(refresh, refreshMs);
refresh();
</script>
</body>
</html>"""
    return body.encode("utf-8")


class MonitorHandler(BaseHTTPRequestHandler):
    refresh = 5

    def do_GET(self) -> None:
        if self.path.startswith("/api"):
            payload = json.dumps(monitor_snapshot(), ensure_ascii=False).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        else:
            payload = render_html(self.refresh)
            content_type = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8502)
    parser.add_argument("--refresh", type=int, default=5)
    args = parser.parse_args()
    MonitorHandler.refresh = args.refresh
    server = ThreadingHTTPServer((args.host, args.port), MonitorHandler)
    print(f"Monitor dashboard: http://{args.host}:{args.port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

