from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.graph_visualizer import export_graph_html
from backend.services.pipeline_log import record_pipeline_run


def _node_id(kind: str, value: Any) -> str:
    clean = str(value or "unknown").replace(" ", "_").replace("/", "_").replace(":", "_")[:96]
    return f"{kind}:{clean}"


def _load_json(value: str | None) -> dict[str, Any]:
    try:
        data = json.loads(value or "{}")
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _add_node(nodes: dict[str, dict[str, str]], node_id: str, label: str, node_type: str, **attrs: Any) -> None:
    current = nodes.get(node_id, {"id": node_id, "label": label, "type": node_type})
    for key, value in attrs.items():
        if value not in (None, "", []):
            current[key] = str(value)
    nodes[node_id] = current


def _edge(edges: list[dict[str, str]], source: str, target: str, edge_type: str, **attrs: Any) -> None:
    row = {"source": source, "target": target, "type": edge_type}
    for key, value in attrs.items():
        if value not in (None, "", []):
            row[key] = str(value)
    edges.append(row)


def build_design_graph(output_dir: Path | None = None, db_path: Path | None = None) -> dict[str, Any]:
    out = output_dir or PROJECT_ROOT / "data" / "design_graph"
    out.mkdir(parents=True, exist_ok=True)
    nodes: dict[str, dict[str, str]] = {}
    edges: list[dict[str, str]] = []

    with connect(db_path) as conn:
        try:
            rows = conn.execute(
                """
                SELECT spl.*, p.title, p.doi, p.year
                FROM sample_property_links spl
                LEFT JOIN papers p ON p.paper_id = spl.paper_id
                WHERE spl.status = 'linked'
                """
            ).fetchall()
        except Exception:
            rows = []

    for row in rows:
        material = _load_json(row["material_json"])
        sample = _load_json(row["sample_json"])
        phase = _load_json(row["phase_json"])
        prop = _load_json(row["property_json"])
        roles = _load_json(row["variable_roles_json"])

        paper_id = _node_id("Paper", row["paper_id"])
        sample_id = _node_id("DesignSample", row["sample_id"])
        material_id = _node_id(
            "MaterialSystem",
            material.get("canonical_name") or material.get("raw_name") or material.get("material_family"),
        )
        property_id = _node_id("TargetProperty", row["link_id"])
        evidence_id = _node_id("Evidence", row["link_id"])

        _add_node(nodes, paper_id, row["title"] or row["paper_id"], "Paper", doi=row["doi"], year=row["year"])
        _add_node(
            nodes,
            material_id,
            material.get("canonical_name") or material.get("raw_name") or material.get("material_family") or "Material",
            "MaterialSystem",
            material_family=material.get("material_family"),
            formula=material.get("formula"),
            dopant_elements=json.dumps(material.get("dopant_elements") or [], ensure_ascii=False),
            zr_fraction=material.get("zr_fraction"),
            role="controllable_variable",
        )
        _add_node(
            nodes,
            sample_id,
            sample.get("device_stack") or f"{material.get('canonical_name') or 'HfO2'} sample",
            "DesignSample",
            sample_id=row["sample_id"],
            context_quality=row["context_quality"],
            context_score=row["context_score"],
            film_thickness_nm=sample.get("film_thickness_nm"),
            annealing_temperature_c=sample.get("annealing_temperature_c"),
            annealing_time_s=sample.get("annealing_time_s"),
            annealing_atmosphere=sample.get("annealing_atmosphere"),
            device_stack=sample.get("device_stack"),
        )
        value = prop.get("normalized_value") or prop.get("value")
        unit = prop.get("normalized_unit") or prop.get("unit") or ""
        _add_node(
            nodes,
            property_id,
            f"{prop.get('property_name') or 'property'}={value} {unit}".strip(),
            "TargetProperty",
            property_name=prop.get("property_name"),
            value=value,
            unit=unit,
            role="target",
        )
        _add_node(
            nodes,
            evidence_id,
            str(row["evidence_text"] or prop.get("evidence_text") or "")[:140],
            "Evidence",
            page_number=row["page_number"],
            evidence_text=row["evidence_text"] or prop.get("evidence_text"),
        )

        _edge(edges, paper_id, material_id, "REPORTS")
        _edge(edges, material_id, sample_id, "HAS_DESIGN_SAMPLE")
        _edge(edges, sample_id, property_id, "HAS_TARGET")
        _edge(edges, property_id, evidence_id, "SUPPORTED_BY")
        _edge(edges, evidence_id, paper_id, "FROM_PAPER")

        for key in roles.get("controllable_variables") or []:
            value = sample.get(key) if key in sample else material.get(key)
            if value in (None, "", []):
                continue
            var_id = _node_id("ControllableVariable", f"{key}:{value}")
            _add_node(nodes, var_id, f"{key}={value}", "ControllableVariable", variable=key, value=value, role="controllable")
            _edge(edges, sample_id, var_id, "CONTROLLED_BY")
        for key in roles.get("constraint_variables") or []:
            var_id = _node_id("ConstraintVariable", key)
            _add_node(nodes, var_id, str(key), "ConstraintVariable", role="constraint")
            _edge(edges, sample_id, var_id, "HAS_CONSTRAINT")
        for key in roles.get("mechanism_variables") or []:
            mechanism_label = phase.get("phase_name") or phase.get("space_group") or key
            var_id = _node_id("MechanismVariable", f"{key}:{mechanism_label}")
            _add_node(
                nodes,
                var_id,
                str(mechanism_label),
                "MechanismVariable",
                variable=key,
                phase_name=phase.get("phase_name"),
                space_group=phase.get("space_group"),
                role="mechanism",
            )
            _edge(edges, sample_id, var_id, "HAS_MECHANISM")
            _edge(edges, var_id, property_id, "INFLUENCES")

    node_fields = sorted({key for node in nodes.values() for key in node})
    edge_fields = sorted({key for edge in edges for key in edge})
    nodes_path = out / "nodes.csv"
    edges_path = out / "edges.csv"
    with nodes_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=node_fields)
        writer.writeheader()
        writer.writerows(nodes.values())
    with edges_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=edge_fields)
        writer.writeheader()
        writer.writerows(edges)

    html_path = PROJECT_ROOT / "data" / "exports" / "hfo2_design_graph.html"
    html_stats = export_graph_html(
        nodes_path=nodes_path,
        edges_path=edges_path,
        output_path=html_path,
        title="HfO2-FerroKG Design Graph",
        db_path=db_path,
    )
    stats = {
        "nodes": len(nodes),
        "edges": len(edges),
        "nodes_path": str(nodes_path),
        "edges_path": str(edges_path),
        "html_path": str(html_path),
        "html_nodes": html_stats.get("node_count", 0),
        "html_edges": html_stats.get("edge_count", 0),
    }
    record_pipeline_run("24_build_design_graph", "ok", stats, db_path=db_path)
    return stats
