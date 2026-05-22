from __future__ import annotations

import csv
import json
from pathlib import Path

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.ontology_context import build_ontology_context
from backend.services.pipeline_log import record_pipeline_run


GRAPH_STATUSES = {"approved", "preapproved_machine", "needs_human_review"}


def _node_id(kind: str, value: str) -> str:
    clean = value.replace(" ", "_").replace("/", "_")[:80]
    return f"{kind}:{clean}"


def build_graph(output_dir: Path | None = None, db_path: Path | None = None) -> dict[str, int]:
    out = output_dir or PROJECT_ROOT / "data" / "graph"
    out.mkdir(parents=True, exist_ok=True)
    nodes: dict[str, dict[str, str]] = {}
    edges: list[dict[str, str]] = []

    with connect(db_path) as conn:
        placeholders = ",".join("?" for _ in GRAPH_STATUSES)
        rows = conn.execute(
            f"""
            SELECT rf.*, p.title, p.doi, p.year
            FROM reviewed_facts rf
            LEFT JOIN papers p ON p.paper_id = rf.paper_id
            WHERE rf.review_status IN ({placeholders})
            """,
            tuple(sorted(GRAPH_STATUSES)),
        ).fetchall()

        for row in rows:
            payload = json.loads(row["payload_json"])
            prop = payload["property"]
            material = payload.get("material") or {"canonical_name": prop.get("material_ref", "HfO2"), "material_family": "unknown_hafnia"}
            sample = payload.get("sample") or {}
            phases = payload.get("phases", [])
            devices = payload.get("devices", [])
            ontology_context = payload.get("ontology_context") or build_ontology_context(
                material,
                sample,
                prop,
                phases,
                devices,
            )
            process_context = ontology_context["process_context"]
            device_context = ontology_context["device_context"]
            paper_id = _node_id("Paper", row["paper_id"])
            material_id = _node_id("HafniaMaterial", material["canonical_name"])
            sample_label = sample.get("device_stack") or f"{material['canonical_name']} sample"
            sample_id = _node_id("ThinFilmSample", f"{row['fact_id']}_{sample_label}")
            prop_label = f"{prop['property_name']}={prop.get('normalized_value') or prop.get('value')} {prop.get('normalized_unit') or prop.get('unit')}"
            prop_id = _node_id("FerroelectricProperty", row["fact_id"])
            evidence_id = _node_id("Evidence", row["fact_id"])

            nodes[paper_id] = {
                "id": paper_id,
                "label": row["title"] or row["paper_id"],
                "type": "Paper",
                "doi": row["doi"] or "",
                "year": str(row["year"] or ""),
            }
            nodes[material_id] = {
                "id": material_id,
                "label": material["canonical_name"],
                "type": "HafniaMaterial",
                "family": str(material.get("material_family", "")),
            }
            nodes[sample_id] = {
                "id": sample_id,
                "label": sample_label,
                "type": "ThinFilmSample",
                "film_thickness_nm": str(sample.get("film_thickness_nm") or ""),
                "annealing_temperature_c": str(sample.get("annealing_temperature_c") or ""),
            }
            nodes[prop_id] = {
                "id": prop_id,
                "label": prop_label,
                "type": "FerroelectricProperty",
                "property_name": prop["property_name"],
                "value": str(prop.get("normalized_value") or prop.get("value") or ""),
                "unit": prop.get("normalized_unit") or prop.get("unit") or "",
                "review_status": row["review_status"],
                "context_quality": str(ontology_context.get("context_quality", "")),
                "context_score": str(ontology_context.get("context_score", "")),
            }
            nodes[evidence_id] = {
                "id": evidence_id,
                "label": prop.get("evidence_text", "")[:120],
                "type": "Evidence",
                "page_number": str(row["page_number"] or ""),
                "evidence_text": prop.get("evidence_text", ""),
            }

            edges.extend(
                [
                    {"source": paper_id, "target": material_id, "type": "REPORTS"},
                    {"source": material_id, "target": sample_id, "type": "HAS_SAMPLE"},
                    {"source": sample_id, "target": prop_id, "type": "HAS_PROPERTY"},
                    {"source": prop_id, "target": evidence_id, "type": "SUPPORTED_BY"},
                    {"source": evidence_id, "target": paper_id, "type": "FROM_PAPER"},
                ]
            )
            if sample.get("top_electrode"):
                elec_id = _node_id("Electrode", sample["top_electrode"])
                nodes[elec_id] = {"id": elec_id, "label": sample["top_electrode"], "type": "Electrode"}
                edges.append({"source": sample_id, "target": elec_id, "type": "HAS_TOP_ELECTRODE"})
            if sample.get("bottom_electrode"):
                elec_id = _node_id("Electrode", sample["bottom_electrode"])
                nodes[elec_id] = {"id": elec_id, "label": sample["bottom_electrode"], "type": "Electrode"}
                edges.append({"source": sample_id, "target": elec_id, "type": "HAS_BOTTOM_ELECTRODE"})
            if sample.get("substrate"):
                substrate_id = _node_id("Substrate", sample["substrate"])
                nodes[substrate_id] = {
                    "id": substrate_id,
                    "label": sample["substrate"],
                    "type": "Substrate",
                }
                edges.append({"source": sample_id, "target": substrate_id, "type": "ON_SUBSTRATE"})
            process_summary = process_context.get("summary")
            if process_summary:
                process_id = _node_id("FabricationProcess", f"{row['fact_id']}_{process_summary}")
                nodes[process_id] = {
                    "id": process_id,
                    "label": process_summary,
                    "type": "FabricationProcess",
                    "deposition_method": str(process_context.get("deposition_method") or ""),
                    "annealing_temperature_c": str(process_context.get("annealing_temperature_c") or ""),
                    "annealing_time_s": str(process_context.get("annealing_time_s") or ""),
                    "annealing_atmosphere": str(process_context.get("annealing_atmosphere") or ""),
                }
                edges.append({"source": sample_id, "target": process_id, "type": "FABRICATED_BY"})
            for device_type in device_context.get("device_types", []):
                device_id = _node_id("Device", device_type)
                nodes[device_id] = {"id": device_id, "label": device_type, "type": "Device"}
                edges.append({"source": sample_id, "target": device_id, "type": "USED_IN"})
            for dopant in material.get("dopant_elements") or []:
                dopant_id = _node_id("Dopant", dopant)
                nodes[dopant_id] = {"id": dopant_id, "label": dopant, "type": "Dopant"}
                edges.append({"source": material_id, "target": dopant_id, "type": "HAS_DOPANT"})
            for phase in phases:
                phase_id = _node_id("PhaseStructure", phase["phase_name"])
                nodes[phase_id] = {
                    "id": phase_id,
                    "label": phase["phase_name"],
                    "type": "PhaseStructure",
                    "space_group": phase.get("space_group") or "",
                }
                edges.append({"source": sample_id, "target": phase_id, "type": "HAS_PHASE"})

    node_fields = sorted({key for node in nodes.values() for key in node})
    with (out / "nodes.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=node_fields)
        writer.writeheader()
        writer.writerows(nodes.values())

    edge_fields = ["source", "target", "type"]
    with (out / "edges.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=edge_fields)
        writer.writeheader()
        writer.writerows(edges)

    with (out / "triples.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["subject", "predicate", "object"])
        writer.writeheader()
        writer.writerows(
            {"subject": edge["source"], "predicate": edge["type"], "object": edge["target"]}
            for edge in edges
        )

    stats = {"nodes": len(nodes), "edges": len(edges)}
    record_pipeline_run("07_build_graph", "ok", stats, db_path=db_path)
    return stats
