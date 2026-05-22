from __future__ import annotations

import json

from backend.db.init_db import init_database
from backend.db.session import connect
from backend.services.fact_relation_enricher import enrich_fact_relations


def test_enrich_fact_relations_completes_context_from_candidate_and_chunk(tmp_path):
    db_path = tmp_path / "facts.sqlite3"
    init_database(db_path)
    candidate_payload = {
        "materials": [
            {
                "canonical_name": "Hf0.5Zr0.5O2",
                "raw_name": "HZO",
                "material_family": "HZO",
                "dopant_elements": ["Zr"],
            }
        ],
        "samples": [
            {
                "material_ref": "Hf0.5Zr0.5O2",
                "film_thickness_nm": 10,
                "device_stack": "TiN/HZO/TiN",
                "deposition_method": "ALD",
                "annealing_temperature_c": 500,
                "top_electrode": "TiN",
                "bottom_electrode": "TiN",
            }
        ],
        "phases": [],
        "devices": [],
    }
    fact_payload = {
        "material": None,
        "sample": None,
        "property": {
            "property_name": "double_remanent_polarization_2Pr",
            "raw_property_name": "2Pr",
            "value": 40,
            "unit": "uC/cm2",
            "normalized_value": 40,
            "normalized_unit": "μC/cm²",
            "evidence_text": "The HZO FeCAP showed 2Pr of 40 uC/cm2 after wake-up.",
        },
        "phases": [],
        "devices": [],
    }
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO document_chunks (
                chunk_id, paper_id, pdf_id, page_number, section, chunk_index, text,
                char_count, token_count, is_high_value
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "chunk_1",
                "paper_1",
                "pdf_1",
                3,
                "results",
                0,
                "The 10 nm TiN/HZO/TiN FeCAP was deposited by ALD and showed orthorhombic Pca21 phase.",
                96,
                15,
                1,
            ),
        )
        conn.execute(
            """
            INSERT INTO extraction_candidates (
                candidate_id, paper_id, pdf_id, chunk_id, page_number, payload_json,
                extractor_version, ontology_version, confidence, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "cand_1",
                "paper_1",
                "pdf_1",
                "chunk_1",
                3,
                json.dumps(candidate_payload),
                "test",
                "hfo2-ferrokg-v1",
                0.8,
                "preapproved_machine",
            ),
        )
        conn.execute(
            """
            INSERT INTO reviewed_facts (
                fact_id, candidate_id, paper_id, pdf_id, chunk_id, page_number,
                fact_type, payload_json, review_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "fact_1",
                "cand_1",
                "paper_1",
                "pdf_1",
                "chunk_1",
                3,
                "ferroelectric_property",
                json.dumps(fact_payload),
                "needs_human_review",
            ),
        )
        conn.commit()

    stats = enrich_fact_relations(db_path=db_path)

    with connect(db_path) as conn:
        payload = json.loads(
            conn.execute(
                "SELECT payload_json FROM reviewed_facts WHERE fact_id = 'fact_1'"
            ).fetchone()[0]
        )

    assert stats["facts_updated"] == 1
    assert payload["material"]["canonical_name"] == "Hf0.5Zr0.5O2"
    assert payload["sample"]["film_thickness_nm"] == 10
    assert payload["ontology_context"]["device_context"]["electrode_stack"] == "TiN/HZO/TiN"
    assert payload["relation_completion"]["relations"]
    assert payload["relation_completion"]["completion_quality"] in {"partial", "strong"}
