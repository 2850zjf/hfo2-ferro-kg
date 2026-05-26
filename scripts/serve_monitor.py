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
    return (
        '<div class="card">'
        f'<div class="label">{html.escape(label)}</div>'
        f'<div class="value" data-metric="{html.escape(label)}">{html.escape(str(value))}</div>'
        "</div>"
    )


def _pause_html(pause: object) -> str:
    if not isinstance(pause, dict):
        return '<div id="pause-banner"></div>'
    reason = html.escape(str(pause.get("reason", "")))
    hint = html.escape(str(pause.get("resume_hint", "额度恢复后重新运行同一条 pipeline，会从未完成位置继续。")))
    return f'<div id="pause-banner" class="pause"><strong>LLM 已自动暂停保护。</strong><br>{reason}<br>{hint}</div>'


def render_html(refresh: int) -> bytes:
    snapshot = monitor_snapshot()
    counts = snapshot["counts"]
    runtime = snapshot["runtime"]
    tokens = snapshot["tokens"]
    logs = snapshot["logs"]
    latest_tail = logs[0]["tail"] if logs else ""
    progress_log = next(
        (item for item in logs if (item.get("progress") or {}).get("type") != "unknown"),
        logs[0] if logs else {},
    )
    latest_progress = progress_log.get("progress", {}) if progress_log else {}
    currency = tokens.get("currency", "CNY")
    body = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HfO2-FerroKG 实时监控</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0f1117;
      --panel: #181c24;
      --panel-2: #121620;
      --border: #303746;
      --text: #f6f7fb;
      --muted: #a5adbb;
      --good: #5bd489;
      --warn: #ffd166;
      --danger: #ff6b7a;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); }}
    main {{ max-width: 1440px; margin: 0 auto; padding: 28px 24px 44px; }}
    h1 {{ margin: 0; font-size: 36px; line-height: 1.15; }}
    h2 {{ margin: 26px 0 12px; font-size: 20px; }}
    .muted {{ color: var(--muted); margin-top: 8px; }}
    .topline {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; }}
    .status {{ color: var(--good); font-weight: 700; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(175px, 1fr)); gap: 12px; margin-top: 12px; }}
    .card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; min-height: 86px; }}
    .label {{ color: var(--muted); font-size: 13px; }}
    .value {{ font-size: 25px; font-weight: 760; margin-top: 5px; word-break: break-word; }}
    .wide {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 14px; }}
    pre {{ white-space: pre-wrap; background: #080a0f; border: 1px solid var(--border); border-radius: 8px; padding: 14px; line-height: 1.45; max-height: 520px; overflow: auto; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--panel-2); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }}
    th, td {{ text-align: left; padding: 10px; border-bottom: 1px solid var(--border); vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 600; }}
    td.command {{ color: var(--muted); font-size: 12px; word-break: break-all; }}
    .warn {{ color: var(--warn); }}
    .pause {{ margin: 0 0 18px; padding: 14px 16px; border-radius: 8px; background: #3b1d24; border: 1px solid var(--danger); color: #ffd8dd; }}
    .note {{ color: var(--muted); margin-top: 10px; font-size: 13px; }}
    @media (max-width: 900px) {{ .wide {{ grid-template-columns: 1fr; }} .topline {{ align-items: flex-start; flex-direction: column; }} }}
  </style>
</head>
<body>
<main>
  {_pause_html(snapshot.get("llm_pause"))}
  <div class="topline">
    <div>
      <h1>HfO2-FerroKG 实时监控</h1>
      <div class="muted">页面不整体刷新，只更新数字；每 {refresh} 秒读取一次本地日志和数据库。</div>
    </div>
    <div id="active-state" class="status"></div>
  </div>

  <h2>时间、速度、剩余</h2>
  <div class="grid">
    {_metric("当前步骤", runtime.get("active_step", ""))}
    {_metric("总运行时间", runtime.get("elapsed", "0s"))}
    {_metric("当前步骤运行", runtime.get("active_elapsed", "0s"))}
    {_metric("预计剩余", runtime.get("eta", "估算中"))}
    {_metric("速度", f"{runtime.get('rate_per_min', 0)} item/min")}
  </div>

  <h2>Token 与费用</h2>
  <div class="grid">
    {_metric("真实总 token", tokens.get("actual_total_tokens", 0))}
    {_metric("真实输入 token", tokens.get("actual_prompt_tokens", 0))}
    {_metric("真实输出 token", tokens.get("actual_completion_tokens", 0))}
    {_metric("估算总 token", tokens.get("estimated_total_tokens", 0))}
    {_metric("当前批次 token", tokens.get("estimated_inflight_tokens", 0))}
    {_metric("实时估算总 token", tokens.get("estimated_live_total_tokens", 0))}
    {_metric("输入单价", f"{tokens.get('input_price_per_token_display', '0')} {currency}/token")}
    {_metric("输出单价", f"{tokens.get('output_price_per_token_display', '0')} {currency}/token")}
    {_metric("输入费用", f"{tokens.get('actual_input_cost', 0)} {currency}")}
    {_metric("输出费用", f"{tokens.get('actual_output_cost', 0)} {currency}")}
    {_metric("真实消费金额", f"{tokens.get('actual_cost', 0)} {currency}")}
    {_metric("估算消费金额", f"{tokens.get('estimated_cost', 0)} {currency}")}
    {_metric("实时估算金额", f"{tokens.get('estimated_live_cost', 0)} {currency}")}
  </div>
  <div class="note" id="token-note">{html.escape(str(tokens.get("note", "")))}</div>

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
    {_metric("样品级关联", counts.get("sample_property_links", 0))}
    {_metric("文献卡片", counts.get("literature_cards", 0))}
    {_metric("chunk 语义标签", counts.get("chunk_semantic_labels", 0))}
    {_metric("AI 二次审核", counts.get("ai_fact_audits", 0))}
  </div>

  <div class="wide">
    <section>
      <h2>当前后台任务</h2>
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
function esc(value) {{
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}}
function setMetric(label, value) {{
  const el = document.querySelector(`[data-metric="${{label}}"]`);
  if (el) el.textContent = value === null || value === undefined ? "" : String(value);
}}
function renderProcesses(processes) {{
  const root = document.getElementById("processes");
  if (!root) return;
  if (!processes || !processes.length) {{
    root.innerHTML = '<div class="card warn">没有检测到后台任务。</div>';
    return;
  }}
  root.innerHTML = '<table><thead><tr><th>PID</th><th>任务</th><th>命令</th></tr></thead><tbody>' +
    processes.map(p => `<tr><td>${{esc(p.pid)}}</td><td>${{esc(p.task)}}</td><td class="command">${{esc(p.command)}}</td></tr>`).join("") +
    '</tbody></table>';
}}
function renderPause(pause) {{
  const old = document.getElementById("pause-banner");
  if (!old) return;
  if (!pause) {{
    old.outerHTML = '<div id="pause-banner"></div>';
    return;
  }}
  old.outerHTML = `<div id="pause-banner" class="pause"><strong>LLM 已自动暂停保护。</strong><br>${{esc(pause.reason || "")}}<br>${{esc(pause.resume_hint || "额度恢复后重新运行同一条 pipeline，会从未完成位置继续。")}}</div>`;
}}
async function refresh() {{
  try {{
    const response = await fetch('/api', {{cache: 'no-store'}});
    const data = await response.json();
    const c = data.counts || {{}};
    const rt = data.runtime || {{}};
    const tk = data.tokens || {{}};
    const logs = data.logs || [];
    const latest = logs.length ? logs[0] : {{}};
    const progressLog = logs.find(item => item.progress && item.progress.type !== "unknown") || latest;
    const currency = tk.currency || "CNY";

    renderPause(data.llm_pause || null);
    document.getElementById("active-state").textContent = data.active ? "后台任务运行中" : "当前无后台任务";

    setMetric("当前步骤", rt.active_step || "");
    setMetric("总运行时间", rt.elapsed || "0s");
    setMetric("当前步骤运行", rt.active_elapsed || "0s");
    setMetric("预计剩余", rt.eta || "估算中");
    setMetric("速度", `${{rt.rate_per_min || 0}} item/min`);

    setMetric("真实总 token", tk.actual_total_tokens || 0);
    setMetric("真实输入 token", tk.actual_prompt_tokens || 0);
    setMetric("真实输出 token", tk.actual_completion_tokens || 0);
    setMetric("估算总 token", tk.estimated_total_tokens || 0);
    setMetric("当前批次 token", tk.estimated_inflight_tokens || 0);
    setMetric("实时估算总 token", tk.estimated_live_total_tokens || 0);
    setMetric("输入单价", `${{tk.input_price_per_token_display || 0}} ${{currency}}/token`);
    setMetric("输出单价", `${{tk.output_price_per_token_display || 0}} ${{currency}}/token`);
    setMetric("输入费用", `${{tk.actual_input_cost || 0}} ${{currency}}`);
    setMetric("输出费用", `${{tk.actual_output_cost || 0}} ${{currency}}`);
    setMetric("真实消费金额", `${{tk.actual_cost || 0}} ${{currency}}`);
    setMetric("估算消费金额", `${{tk.estimated_cost || 0}} ${{currency}}`);
    setMetric("实时估算金额", `${{tk.estimated_live_cost || 0}} ${{currency}}`);
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
    setMetric("样品级关联", c.sample_property_links || 0);
    setMetric("文献卡片", c.literature_cards || 0);
    setMetric("chunk 语义标签", c.chunk_semantic_labels || 0);
    setMetric("AI 二次审核", c.ai_fact_audits || 0);

    renderProcesses(data.processes || []);
    document.getElementById("progress-json").textContent = JSON.stringify(progressLog.progress || {{}}, null, 2);
    document.getElementById("log-tail").textContent = (latest.tail || "").slice(-6000);
  }} catch (error) {{
    console.warn(error);
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
    parser = argparse.ArgumentParser(description="Serve a stable local progress monitor.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8502)
    parser.add_argument("--refresh", type=int, default=5)
    args = parser.parse_args()
    MonitorHandler.refresh = max(1, args.refresh)
    server = ThreadingHTTPServer((args.host, args.port), MonitorHandler)
    print(f"Serving monitor at http://{args.host}:{args.port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
