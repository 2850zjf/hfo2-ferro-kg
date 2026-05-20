from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.pipeline_log import record_pipeline_run


ONTOLOGY_SOURCE = PROJECT_ROOT / "ontology" / "hfo2_ontology.yaml"
DICTIONARY_SOURCES = {
    "property_dictionary": PROJECT_ROOT / "ontology" / "property_dictionary.yaml",
    "material_dictionary": PROJECT_ROOT / "ontology" / "material_dictionary.yaml",
    "unit_dictionary": PROJECT_ROOT / "ontology" / "unit_dictionary.yaml",
}


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Ontology source not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected a YAML mapping in {path}")
    return data


def _stable_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _checksum(data: Any) -> str:
    return hashlib.sha256(_stable_json(data).encode("utf-8")).hexdigest()


def _property_values(ontology: dict[str, Any]) -> list[str]:
    fields = ontology["node_classes"]["FerroelectricProperty"]["fields"]
    values = fields["property_name"]["values"]
    if not isinstance(values, list) or not values:
        raise ValueError("FerroelectricProperty.property_name must define enum values")
    return [str(value) for value in values]


def validate_ontology(ontology: dict[str, Any]) -> dict[str, Any]:
    required_top = [
        "version",
        "name",
        "node_classes",
        "relation_classes",
        "property_normalization",
        "extraction_contract",
        "review_statuses",
        "workflow_order",
    ]
    missing_top = [key for key in required_top if key not in ontology]
    if missing_top:
        raise ValueError(f"Ontology is missing required top-level keys: {missing_top}")

    nodes = ontology["node_classes"]
    relations = ontology["relation_classes"]
    if not isinstance(nodes, dict) or not nodes:
        raise ValueError("node_classes must be a non-empty mapping")
    if not isinstance(relations, dict) or not relations:
        raise ValueError("relation_classes must be a non-empty mapping")

    for required_node in ["Paper", "HafniaMaterial", "ThinFilmSample", "FerroelectricProperty", "Evidence"]:
        if required_node not in nodes:
            raise ValueError(f"Ontology is missing node class: {required_node}")

    for node_name, node in nodes.items():
        fields = node.get("fields")
        if not isinstance(fields, dict) or not fields:
            raise ValueError(f"Node class {node_name} must define fields")
        evidence_required = node_name in {
            "HafniaMaterial",
            "ThinFilmSample",
            "PhaseStructure",
            "FerroelectricProperty",
            "Evidence",
        }
        if evidence_required and "evidence_text" not in fields:
            raise ValueError(f"Node class {node_name} must include evidence_text")

    for relation_name, relation in relations.items():
        from_node = relation.get("from")
        to_node = relation.get("to")
        if from_node not in nodes or to_node not in nodes:
            raise ValueError(
                f"Relation {relation_name} has invalid endpoint: {from_node} -> {to_node}"
            )

    contract = ontology["extraction_contract"]
    required_fact_fields = set(contract.get("required_for_property_candidate", []))
    for field in ["property_name", "evidence_text", "paper_id", "pdf_id", "confidence"]:
        if field not in required_fact_fields:
            raise ValueError(f"extraction_contract must require {field}")

    property_values = _property_values(ontology)
    normalization_names = set(ontology["property_normalization"].keys())
    properties_requiring_normalization = {
        "remanent_polarization_Pr",
        "double_remanent_polarization_2Pr",
        "coercive_field_Ec",
    }
    missing_normalizers = [
        name
        for name in property_values
        if name in properties_requiring_normalization and name not in normalization_names
    ]
    if missing_normalizers:
        raise ValueError(f"Missing property normalization rules: {missing_normalizers}")

    return {
        "version": ontology["version"],
        "entity_count": len(nodes),
        "relation_count": len(relations),
        "property_count": len(property_values),
        "workflow_steps": len(ontology["workflow_order"]),
    }


def _load_dictionaries() -> dict[str, Any]:
    dictionaries: dict[str, Any] = {}
    for name, path in DICTIONARY_SOURCES.items():
        dictionaries[name] = _load_yaml(path) if path.exists() else {}
    return dictionaries


def _write_report(bundle: dict[str, Any], report_path: Path) -> None:
    ontology = bundle["ontology"]
    validation = bundle["validation"]
    lines = [
        "# HfO2-FerroKG Ontology Build Report",
        "",
        f"- Version: {bundle['version']}",
        f"- Name: {ontology['name']}",
        f"- Checksum: {bundle['checksum']}",
        f"- Entity classes: {validation['entity_count']}",
        f"- Relation classes: {validation['relation_count']}",
        f"- Property enum values: {validation['property_count']}",
        "",
        "## Workflow Order",
        "",
    ]
    lines.extend(f"{index}. {step}" for index, step in enumerate(ontology["workflow_order"], start=1))
    lines.extend(["", "## Core Entity Classes", ""])
    for node_name, node in ontology["node_classes"].items():
        field_count = len(node.get("fields", {}))
        lines.append(f"- {node_name}: {field_count} fields")
    lines.extend(["", "## Relation Classes", ""])
    for relation_name, relation in ontology["relation_classes"].items():
        lines.append(f"- {relation_name}: {relation['from']} -> {relation['to']}")
    lines.extend(["", "## Extraction Contract", ""])
    for item in ontology["extraction_contract"]["required_for_property_candidate"]:
        lines.append(f"- Required: {item}")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _record_ontology_version(bundle: dict[str, Any], db_path: Path | None) -> None:
    validation = bundle["validation"]
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ontology_versions (
                ontology_version TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                bundle_path TEXT NOT NULL,
                report_path TEXT NOT NULL,
                entity_count INTEGER NOT NULL,
                relation_count INTEGER NOT NULL,
                property_count INTEGER NOT NULL,
                checksum TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO ontology_versions (
                ontology_version, name, bundle_path, report_path,
                entity_count, relation_count, property_count, checksum
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ontology_version) DO UPDATE SET
                name = excluded.name,
                bundle_path = excluded.bundle_path,
                report_path = excluded.report_path,
                entity_count = excluded.entity_count,
                relation_count = excluded.relation_count,
                property_count = excluded.property_count,
                checksum = excluded.checksum
            """,
            (
                bundle["version"],
                bundle["ontology"]["name"],
                bundle["bundle_path"],
                bundle["report_path"],
                validation["entity_count"],
                validation["relation_count"],
                validation["property_count"],
                bundle["checksum"],
            ),
        )
        conn.commit()


def build_ontology(
    output_dir: Path | None = None,
    db_path: Path | None = None,
    record_run: bool = True,
) -> dict[str, Any]:
    ontology = _load_yaml(ONTOLOGY_SOURCE)
    dictionaries = _load_dictionaries()
    validation = validate_ontology(ontology)
    version = str(ontology["version"])
    target_dir = output_dir or PROJECT_ROOT / "data" / "ontology" / version
    target_dir.mkdir(parents=True, exist_ok=True)

    bundle: dict[str, Any] = {
        "version": version,
        "ontology": ontology,
        "dictionaries": dictionaries,
        "validation": validation,
        "source_files": {
            "ontology": str(ONTOLOGY_SOURCE),
            **{name: str(path) for name, path in DICTIONARY_SOURCES.items()},
        },
    }
    bundle["checksum"] = _checksum(bundle)
    bundle_path = target_dir / "ontology_bundle.json"
    report_path = target_dir / "ontology_report.md"
    bundle["bundle_path"] = str(bundle_path)
    bundle["report_path"] = str(report_path)

    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_report(bundle, report_path)
    _record_ontology_version(bundle, db_path)

    stats = {
        "version": version,
        "entity_count": validation["entity_count"],
        "relation_count": validation["relation_count"],
        "property_count": validation["property_count"],
        "workflow_steps": validation["workflow_steps"],
        "checksum": bundle["checksum"],
        "bundle_path": str(bundle_path),
        "report_path": str(report_path),
    }
    if record_run:
        record_pipeline_run("00_build_ontology", "ok", stats, db_path=db_path)
    return stats


def load_ontology_bundle(version: str | None = None, build_if_missing: bool = True) -> dict[str, Any]:
    selected_version = version or _load_yaml(ONTOLOGY_SOURCE)["version"]
    bundle_path = PROJECT_ROOT / "data" / "ontology" / str(selected_version) / "ontology_bundle.json"
    if not bundle_path.exists() and build_if_missing:
        build_ontology(record_run=False)
    if not bundle_path.exists():
        raise FileNotFoundError(f"Ontology bundle not found: {bundle_path}")
    return json.loads(bundle_path.read_text(encoding="utf-8"))


def load_ontology_prompt_context(bundle: dict[str, Any] | None = None) -> str:
    bundle = bundle or load_ontology_bundle(build_if_missing=True)
    ontology = bundle["ontology"]
    property_names = ontology["node_classes"]["FerroelectricProperty"]["fields"]["property_name"]["values"]
    relation_lines = [
        f"- {name}: {relation['from']} -> {relation['to']}"
        for name, relation in ontology["relation_classes"].items()
    ]
    required = ontology["extraction_contract"]["required_for_property_candidate"]
    rules = ontology["extraction_contract"]["strict_rules"]
    return "\n".join(
        [
            "# HfO2-FerroKG ontology contract",
            f"ontology_version: {bundle['version']}",
            "Allowed property_name values:",
            ", ".join(property_names),
            "Required fields for each property candidate:",
            ", ".join(required),
            "Core relations:",
            *relation_lines,
            "Strict extraction rules:",
            *[f"- {rule}" for rule in rules],
        ]
    )
