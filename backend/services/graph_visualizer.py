from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from backend.core.config import PROJECT_ROOT
from backend.services.pipeline_log import record_pipeline_run


TYPE_STYLE = {
    "Paper": {"color": "#62a8ff", "radius": 5},
    "HafniaMaterial": {"color": "#49d17d", "radius": 8},
    "ThinFilmSample": {"color": "#f8c85a", "radius": 5},
    "FabricationProcess": {"color": "#ff9f43", "radius": 5},
    "PhaseStructure": {"color": "#b991ff", "radius": 6},
    "Device": {"color": "#ff78b5", "radius": 6},
    "Electrode": {"color": "#85e7ff", "radius": 4},
    "Substrate": {"color": "#cdd6e5", "radius": 4},
    "Dopant": {"color": "#ff6b6b", "radius": 5},
    "FerroelectricProperty": {"color": "#f45d5d", "radius": 6},
    "Evidence": {"color": "#b9c1d1", "radius": 3},
}

TYPE_X = {
    "Paper": -820,
    "HafniaMaterial": -500,
    "Dopant": -260,
    "ThinFilmSample": -120,
    "FabricationProcess": 220,
    "PhaseStructure": 300,
    "Electrode": 380,
    "Substrate": 460,
    "Device": 540,
    "FerroelectricProperty": 820,
    "Evidence": 1160,
}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _stable_jitter(text: str, scale: float = 18.0) -> float:
    value = sum((index + 1) * ord(char) for index, char in enumerate(text))
    return ((value % 1000) / 1000.0 - 0.5) * scale


def _layout_nodes(nodes: list[dict[str, str]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for node in nodes:
        grouped[node.get("type") or "Unknown"].append(node)

    positioned: list[dict[str, Any]] = []
    for node_type, items in grouped.items():
        x = TYPE_X.get(node_type, 0)
        spacing = 24 if len(items) < 500 else 16
        height = max(1, len(items) - 1) * spacing
        for index, node in enumerate(items):
            y = index * spacing - height / 2
            style = TYPE_STYLE.get(node_type, {"color": "#d7dde8", "radius": 4})
            label = node.get("label") or node.get("id") or ""
            positioned.append(
                {
                    **node,
                    "x": x + _stable_jitter(node.get("id", label), 34),
                    "y": y + _stable_jitter(label, 12),
                    "color": style["color"],
                    "radius": style["radius"],
                }
            )
    return positioned


def _summarize(nodes: list[dict[str, Any]], edges: list[dict[str, str]]) -> dict[str, Any]:
    node_types = Counter(str(node.get("type") or "Unknown") for node in nodes)
    edge_types = Counter(str(edge.get("type") or "Unknown") for edge in edges)
    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "node_types": dict(sorted(node_types.items())),
        "edge_types": dict(sorted(edge_types.items())),
    }


def _html_template(title: str, nodes: list[dict[str, Any]], edges: list[dict[str, str]], summary: dict[str, Any]) -> str:
    data_json = json.dumps({"nodes": nodes, "edges": edges, "summary": summary}, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0f1117;
      --panel: rgba(24, 28, 36, 0.94);
      --border: rgba(255,255,255,0.12);
      --text: #f6f7fb;
      --muted: #a9b2c3;
      --accent: #69a7ff;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; overflow: hidden; }}
    canvas {{ display: block; width: 100vw; height: 100vh; cursor: grab; }}
    canvas.dragging {{ cursor: grabbing; }}
    .panel {{ position: fixed; left: 18px; top: 18px; width: min(440px, calc(100vw - 36px)); max-height: calc(100vh - 36px); overflow: auto; background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 14px; box-shadow: 0 12px 40px rgba(0,0,0,0.35); }}
    h1 {{ margin: 0 0 6px; font-size: 22px; }}
    .muted {{ color: var(--muted); font-size: 13px; line-height: 1.5; }}
    .row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 12px 0; }}
    .metric {{ border: 1px solid var(--border); border-radius: 8px; padding: 9px; background: rgba(255,255,255,0.04); }}
    .metric strong {{ display: block; font-size: 20px; }}
    input, select, button {{ width: 100%; border: 1px solid var(--border); border-radius: 7px; padding: 8px 10px; background: #171b24; color: var(--text); }}
    button {{ cursor: pointer; font-weight: 700; }}
    button:hover {{ border-color: var(--accent); }}
    .legend {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; margin: 10px 0; }}
    label {{ display: flex; gap: 7px; align-items: center; font-size: 13px; color: var(--muted); }}
    .swatch {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; flex: 0 0 auto; }}
    .details {{ margin-top: 12px; border-top: 1px solid var(--border); padding-top: 10px; white-space: pre-wrap; word-break: break-word; font-size: 13px; line-height: 1.55; }}
    .pill {{ display: inline-block; padding: 2px 7px; border: 1px solid var(--border); border-radius: 999px; color: var(--muted); margin: 3px 4px 3px 0; font-size: 12px; }}
  </style>
</head>
<body>
  <canvas id="graph"></canvas>
  <section class="panel">
    <h1>{title}</h1>
    <div class="muted">滚轮缩放，拖动画布平移；点击节点查看 DOI、证据、性能值等详情。</div>
    <div class="row">
      <div class="metric"><span class="muted">节点</span><strong id="nodeCount"></strong></div>
      <div class="metric"><span class="muted">关系</span><strong id="edgeCount"></strong></div>
    </div>
    <input id="search" placeholder="搜索材料 / DOI / 论文 / 性能 / evidence">
    <div class="row">
      <button id="fit">适配全图</button>
      <button id="neighbors">只看选中邻域</button>
    </div>
    <select id="relationFilter">
      <option value="all">全部关系</option>
    </select>
    <div class="legend" id="legend"></div>
    <div class="details" id="details">点击一个节点查看详情。</div>
  </section>
  <script>
    const DATA = {data_json};
    const canvas = document.getElementById("graph");
    const ctx = canvas.getContext("2d");
    const DPR = window.devicePixelRatio || 1;
    const nodes = DATA.nodes;
    const edges = DATA.edges;
    const nodeById = new Map(nodes.map(n => [n.id, n]));
    const nodeTypes = [...new Set(nodes.map(n => n.type || "Unknown"))].sort();
    const edgeTypes = [...new Set(edges.map(e => e.type || "Unknown"))].sort();
    let selected = null;
    let neighborOnly = false;
    let relationFilter = "all";
    let enabledTypes = new Set(nodeTypes);
    let searchText = "";
    let transform = {{ x: window.innerWidth / 2, y: window.innerHeight / 2, scale: 0.62 }};
    let dragging = false;
    let last = {{ x: 0, y: 0 }};

    document.getElementById("nodeCount").textContent = DATA.summary.node_count;
    document.getElementById("edgeCount").textContent = DATA.summary.edge_count;

    const relationSelect = document.getElementById("relationFilter");
    for (const type of edgeTypes) {{
      const option = document.createElement("option");
      option.value = type;
      option.textContent = type;
      relationSelect.appendChild(option);
    }}

    const legend = document.getElementById("legend");
    const typeColors = new Map(nodes.map(n => [n.type || "Unknown", n.color || "#d7dde8"]));
    for (const type of nodeTypes) {{
      const label = document.createElement("label");
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = true;
      box.addEventListener("change", () => {{
        if (box.checked) enabledTypes.add(type); else enabledTypes.delete(type);
        draw();
      }});
      const swatch = document.createElement("span");
      swatch.className = "swatch";
      swatch.style.background = typeColors.get(type);
      const text = document.createElement("span");
      text.textContent = `${{type}} (${{DATA.summary.node_types[type] || 0}})`;
      label.append(box, swatch, text);
      legend.appendChild(label);
    }}

    function resize() {{
      canvas.width = Math.floor(window.innerWidth * DPR);
      canvas.height = Math.floor(window.innerHeight * DPR);
      canvas.style.width = window.innerWidth + "px";
      canvas.style.height = window.innerHeight + "px";
      draw();
    }}
    window.addEventListener("resize", resize);

    function worldToScreen(x, y) {{
      return {{ x: (x * transform.scale + transform.x) * DPR, y: (y * transform.scale + transform.y) * DPR }};
    }}
    function screenToWorld(x, y) {{
      return {{ x: (x / DPR - transform.x) / transform.scale, y: (y / DPR - transform.y) / transform.scale }};
    }}
    function visibleNode(n) {{
      if (!enabledTypes.has(n.type || "Unknown")) return false;
      if (searchText) {{
        const haystack = Object.values(n).join(" ").toLowerCase();
        if (!haystack.includes(searchText)) return false;
      }}
      if (neighborOnly && selected) {{
        return n.id === selected.id || edges.some(e =>
          (e.source === selected.id && e.target === n.id) ||
          (e.target === selected.id && e.source === n.id)
        );
      }}
      return true;
    }}
    function visibleEdge(e) {{
      if (relationFilter !== "all" && e.type !== relationFilter) return false;
      const s = nodeById.get(e.source);
      const t = nodeById.get(e.target);
      return s && t && visibleNode(s) && visibleNode(t);
    }}

    function draw() {{
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = "#0f1117";
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      ctx.lineWidth = Math.max(0.35, transform.scale * 0.9) * DPR;
      for (const e of edges) {{
        if (!visibleEdge(e)) continue;
        const s = nodeById.get(e.source);
        const t = nodeById.get(e.target);
        const a = worldToScreen(s.x, s.y);
        const b = worldToScreen(t.x, t.y);
        ctx.strokeStyle = selected && (e.source === selected.id || e.target === selected.id)
          ? "rgba(255, 230, 128, 0.75)" : "rgba(180, 190, 210, 0.14)";
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }}

      for (const n of nodes) {{
        if (!visibleNode(n)) continue;
        const p = worldToScreen(n.x, n.y);
        const r = Math.max(2.2, (Number(n.radius) || 4) * transform.scale) * DPR;
        ctx.fillStyle = n.color || "#d7dde8";
        ctx.globalAlpha = selected && n.id !== selected.id ? 0.72 : 1;
        ctx.beginPath();
        ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
        ctx.fill();
        if (selected && n.id === selected.id) {{
          ctx.strokeStyle = "#fff0a6";
          ctx.lineWidth = 3 * DPR;
          ctx.stroke();
        }}
        if (transform.scale > 0.95 && n.type !== "Evidence") {{
          ctx.fillStyle = "#f6f7fb";
          ctx.font = `${{11 * DPR}}px Segoe UI, sans-serif`;
          ctx.fillText(String(n.label || n.id).slice(0, 42), p.x + r + 3 * DPR, p.y + 4 * DPR);
        }}
      }}
      ctx.globalAlpha = 1;
    }}

    function pickNode(clientX, clientY) {{
      const world = screenToWorld(clientX, clientY);
      let best = null;
      let bestDist = Infinity;
      for (const n of nodes) {{
        if (!visibleNode(n)) continue;
        const dx = n.x - world.x;
        const dy = n.y - world.y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        const threshold = Math.max(12, 10 / transform.scale);
        if (dist < threshold && dist < bestDist) {{
          best = n;
          bestDist = dist;
        }}
      }}
      return best;
    }}

    function showDetails(n) {{
      const details = document.getElementById("details");
      if (!n) {{
        details.textContent = "点击一个节点查看详情。";
        return;
      }}
      const linked = edges.filter(e => e.source === n.id || e.target === n.id).slice(0, 16);
      const lines = [
        `类型: ${{n.type || ""}}`,
        `标签: ${{n.label || ""}}`,
        `ID: ${{n.id || ""}}`,
        ""
      ];
      for (const [k, v] of Object.entries(n)) {{
        if (["x", "y", "color", "radius", "id", "label", "type"].includes(k)) continue;
        if (v !== undefined && v !== null && String(v).trim()) lines.push(`${{k}}: ${{String(v).slice(0, 1000)}}`);
      }}
      lines.push("", "相邻关系:");
      for (const e of linked) {{
        const other = e.source === n.id ? nodeById.get(e.target) : nodeById.get(e.source);
        lines.push(`${{e.type}} -> ${{other?.label || other?.id || ""}}`);
      }}
      details.textContent = lines.join("\\n");
    }}

    canvas.addEventListener("mousedown", e => {{
      dragging = true;
      canvas.classList.add("dragging");
      last = {{ x: e.clientX, y: e.clientY }};
    }});
    window.addEventListener("mouseup", () => {{
      dragging = false;
      canvas.classList.remove("dragging");
    }});
    window.addEventListener("mousemove", e => {{
      if (!dragging) return;
      transform.x += e.clientX - last.x;
      transform.y += e.clientY - last.y;
      last = {{ x: e.clientX, y: e.clientY }};
      draw();
    }});
    canvas.addEventListener("click", e => {{
      if (Math.abs(e.clientX - last.x) > 4 || Math.abs(e.clientY - last.y) > 4) return;
      selected = pickNode(e.clientX, e.clientY);
      showDetails(selected);
      draw();
    }});
    canvas.addEventListener("wheel", e => {{
      e.preventDefault();
      const before = screenToWorld(e.clientX, e.clientY);
      const factor = e.deltaY < 0 ? 1.12 : 0.89;
      transform.scale = Math.min(8, Math.max(0.06, transform.scale * factor));
      const after = screenToWorld(e.clientX, e.clientY);
      transform.x += (after.x - before.x) * transform.scale;
      transform.y += (after.y - before.y) * transform.scale;
      draw();
    }}, {{ passive: false }});

    document.getElementById("search").addEventListener("input", e => {{
      searchText = e.target.value.trim().toLowerCase();
      neighborOnly = false;
      draw();
    }});
    relationSelect.addEventListener("change", e => {{
      relationFilter = e.target.value;
      draw();
    }});
    document.getElementById("neighbors").addEventListener("click", () => {{
      neighborOnly = !neighborOnly;
      draw();
    }});
    document.getElementById("fit").addEventListener("click", fit);

    function fit() {{
      const visible = nodes.filter(visibleNode);
      if (!visible.length) return;
      const minX = Math.min(...visible.map(n => n.x));
      const maxX = Math.max(...visible.map(n => n.x));
      const minY = Math.min(...visible.map(n => n.y));
      const maxY = Math.max(...visible.map(n => n.y));
      const scaleX = window.innerWidth / Math.max(1, maxX - minX + 260);
      const scaleY = window.innerHeight / Math.max(1, maxY - minY + 260);
      transform.scale = Math.min(1.4, Math.max(0.05, Math.min(scaleX, scaleY)));
      transform.x = window.innerWidth / 2 - ((minX + maxX) / 2) * transform.scale;
      transform.y = window.innerHeight / 2 - ((minY + maxY) / 2) * transform.scale;
      draw();
    }}
    resize();
    fit();
  </script>
</body>
</html>
"""


def export_graph_html(
    nodes_path: Path | None = None,
    edges_path: Path | None = None,
    output_path: Path | None = None,
    title: str = "HfO2-FerroKG Knowledge Graph",
    db_path: Path | None = None,
) -> dict[str, Any]:
    nodes_file = nodes_path or PROJECT_ROOT / "data" / "graph" / "nodes.csv"
    edges_file = edges_path or PROJECT_ROOT / "data" / "graph" / "edges.csv"
    target = output_path or PROJECT_ROOT / "data" / "exports" / "hfo2_knowledge_graph.html"
    target.parent.mkdir(parents=True, exist_ok=True)

    nodes = _layout_nodes(_read_csv(nodes_file))
    edges = _read_csv(edges_file)
    summary = _summarize(nodes, edges)
    html = _html_template(title, nodes, edges, summary)
    target.write_text(html, encoding="utf-8")
    stats = {**summary, "output_path": str(target)}
    record_pipeline_run("17_export_graph_html", "ok", stats, db_path=db_path)
    return stats
