from __future__ import annotations

import json

import pandas as pd

from backend.db.init_db import init_database
from backend.db.session import connect
from backend.services.ai_fact_auditor import (
    init_ai_audit_tables,
    normalize_ai_audit_payload,
)
from backend.services.chunk_semantic_labeler import normalize_chunk_label_payload
from backend.services.design_dataset import build_benchmark_tier_datasets, build_design_dataset
from backend.services.literature_card import normalize_literature_card_payload
from backend.services.sample_linker import init_sample_link_tables


def test_literature_card_payload_keeps_traceable_fields():
    context = {
        "paper_id": "paper_1",
        "pdf_id": "pdf_1",
        "title": "Fallback title",
        "doi": "10.1/fallback",
        "year": 2025,
        "paper_type": "experimental",
    }
    payload = normalize_literature_card_payload(
        {
            "title": "Original HZO paper",
            "doi": "10.1/hzo",
            "research_type": "experimental",
            "material_systems": ["Hf0.5Zr0.5O2"],
            "main_properties": [{"property_name": "double_remanent_polarization_2Pr"}],
            "evidence_text": "Hf0.5Zr0.5O2 films were measured.",
            "confidence": 0.8,
        },
        context,
    )

    assert payload["title"] == "Original HZO paper"
    assert payload["doi"] == "10.1/hzo"
    assert payload["material_systems"] == ["Hf0.5Zr0.5O2"]
    assert payload["confidence"] == 0.8


def test_chunk_label_normalizer_marks_secondary_citation():
    row = {
        "chunk_id": "chunk_1",
        "paper_id": "paper_1",
        "pdf_id": "pdf_1",
        "page_number": 2,
        "section": "results",
    }
    payload = normalize_chunk_label_payload(
        {
            "semantic_section": "review_context",
            "semantic_roles": ["review_secondary_literature"],
            "contains_secondary_citation": True,
            "risk_flags": ["review_secondary_value"],
            "evidence_text": "Previous work reported 2Pr.",
            "confidence": 0.7,
        },
        row,
    )

    assert payload["semantic_section"] == "review_context"
    assert payload["contains_secondary_citation"] is True
    assert "review_secondary_value" in payload["risk_flags"]


def test_ai_audit_payload_prevents_risky_model_rows():
    row = {
        "link_id": "link_1",
        "source_kind": "benchmark_record",
        "source_id": "bench_1",
        "paper_id": "paper_1",
        "sample_id": "sample_1",
        "context_quality": "partial",
        "evidence_text": "The cited 2Pr was 40 uC/cm2.",
    }
    payload = normalize_ai_audit_payload(
        {
            "ai_review_status": "usable_for_rag_only",
            "risk_flags": ["review_secondary_value"],
            "usable_for_model": False,
            "usable_for_paper_claims": True,
            "repair_suggestion": "Verify whether this is original data.",
        },
        row,
    )

    assert payload["ai_review_status"] == "usable_for_rag_only"
    assert payload["usable_for_model"] is False
    assert payload["usable_for_paper_claims"] is True


def test_benchmark_tiers_use_sample_quality_and_ai_audit(tmp_path):
    db_path = tmp_path / "tiers.sqlite3"
    output_dir = tmp_path / "design"
    init_database(db_path)
    init_sample_link_tables(db_path)
    init_ai_audit_tables(db_path)

    material = {"canonical_name": "Hf0.5Zr0.5O2", "material_family": "HZO", "dopant_elements": ["Zr"]}
    sample = {
        "film_thickness_nm": 10,
        "deposition_method": "ALD",
        "annealing_temperature_c": 500,
        "annealing_time_s": 30,
        "annealing_atmosphere": "N2",
        "device_stack": "TiN/HZO/TiN",
    }
    phase = {"phase_name": "orthorhombic", "space_group": "Pca21"}
    prop = {
        "property_name": "double_remanent_polarization_2Pr",
        "value": 40,
        "unit": "uC/cm2",
        "evidence_text": "The 2Pr value was 40 uC/cm2.",
    }
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO papers (paper_id, title, doi, year, paper_type) VALUES (?, ?, ?, ?, ?)",
            ("paper_1", "HZO paper", "10.1/hzo", 2025, "experimental"),
        )
        conn.execute(
            """
            INSERT INTO sample_property_links (
                link_id, source_kind, source_id, paper_id, pdf_id, chunk_id,
                page_number, sample_id, material_json, sample_json, phase_json,
                property_json, evidence_text, variable_roles_json, context_quality,
                context_score, linkage_method, linker_version, llm_usage_json, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "link_1",
                "reviewed_fact",
                "fact_1",
                "paper_1",
                "pdf_1",
                "chunk_1",
                4,
                "sample_1",
                json.dumps(material),
                json.dumps(sample),
                json.dumps(phase),
                json.dumps(prop),
                "The 2Pr value was 40 uC/cm2.",
                "{}",
                "strong",
                0.9,
                "test",
                "test",
                "{}",
                "linked",
            ),
        )
        conn.execute(
            """
            INSERT INTO ai_fact_audits (
                audit_id, link_id, source_kind, source_id, paper_id, sample_id,
                ai_review_status, risk_flags_json, repair_suggestion,
                usable_for_model, usable_for_paper_claims, reasoning_summary,
                evidence_text, audit_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "audit_1",
                "link_1",
                "reviewed_fact",
                "fact_1",
                "paper_1",
                "sample_1",
                "usable_for_model",
                "[]",
                "",
                1,
                1,
                "Traceable original value.",
                "The 2Pr value was 40 uC/cm2.",
                "test",
            ),
        )
        conn.commit()

    stats = build_design_dataset(output_path=output_dir / "all.csv", db_path=db_path)
    tier_stats = build_benchmark_tier_datasets(output_dir=output_dir, db_path=db_path)
    strong = pd.read_csv(output_dir / "hfo2_design_dataset_strong_only.csv")

    assert stats["strong_only_rows"] == 1
    assert tier_stats["tiers"]["strong_only"]["rows"] == 1
    assert strong.iloc[0]["ai_review_status"] == "usable_for_model"
