from __future__ import annotations

import json

from backend.db.init_db import init_database, list_tables
from backend.services.ontology_builder import (
    build_ontology,
    load_ontology_prompt_context,
    validate_ontology,
)


def test_build_ontology_writes_bundle_and_database_record(tmp_path):
    db_path = tmp_path / "hfo2.sqlite3"
    output_dir = tmp_path / "ontology"
    init_database(db_path)

    stats = build_ontology(output_dir=output_dir, db_path=db_path)

    assert stats["version"] == "hfo2-ferrokg-v1"
    assert stats["entity_count"] >= 10
    assert stats["relation_count"] >= 10
    assert stats["property_count"] >= 10
    assert (output_dir / "ontology_bundle.json").exists()
    assert (output_dir / "ontology_report.md").exists()
    assert "ontology_versions" in set(list_tables(db_path))

    bundle = json.loads((output_dir / "ontology_bundle.json").read_text(encoding="utf-8"))
    assert bundle["ontology"]["workflow_order"][0] == "00_build_ontology"
    assert bundle["checksum"] == stats["checksum"]


def test_ontology_requires_evidence_and_trace_fields(tmp_path):
    db_path = tmp_path / "hfo2.sqlite3"
    output_dir = tmp_path / "ontology"
    init_database(db_path)

    stats = build_ontology(output_dir=output_dir, db_path=db_path, record_run=False)
    assert stats["version"] == "hfo2-ferrokg-v1"

    bundle = json.loads((output_dir / "ontology_bundle.json").read_text(encoding="utf-8"))
    context = load_ontology_prompt_context(bundle=bundle)

    assert "ontology_version: hfo2-ferrokg-v1" in context
    assert "evidence_text" in context
    assert "double_remanent_polarization_2Pr" in context
    assert "Never convert 2Pr into Pr" in context


def test_validate_ontology_rejects_missing_required_top_level_key():
    bad = {"version": "broken"}

    try:
        validate_ontology(bad)
    except ValueError as exc:
        assert "missing required top-level keys" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("validate_ontology should reject incomplete ontology")
