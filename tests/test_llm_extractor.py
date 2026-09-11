from __future__ import annotations

from backend.services.llm_extractor import (
    _merge_extraction_results,
    _specialist_repair_targets,
    _table_row_segments,
    build_user_input,
    coerce_llm_result_payload,
    evidence_is_grounded,
    estimate_chunk_cost_units,
    infer_extraction_profiles,
    is_cited_comparison_table,
    llm_status,
    non_evidence_reason,
    normalize_base_url,
)
from backend.schemas.hfo2_extraction_schema import HfO2ExtractionResult


def test_build_user_input_contains_source_identifiers():
    row = {
        "paper_id": "paper_1",
        "pdf_id": "pdf_1",
        "chunk_id": "chunk_1",
        "page_number": 3,
        "text": "The Hf0.5Zr0.5O2 film showed 2Pr of 40 uC/cm2.",
    }

    prompt = build_user_input(row)

    assert "paper_id: paper_1" in prompt
    assert "page_number: 3" in prompt
    assert "2Pr of 40" in prompt
    assert "Extract every condition-bound numeric observation" in prompt


def test_llm_status_has_expected_keys():
    status = llm_status()

    assert "model" in status
    assert "provider" in status
    assert "api_key_configured" in status
    assert "openai_sdk_available" in status


def test_dashscope_api_v1_is_normalized_to_compatible_endpoint():
    base_url, note = normalize_base_url("dashscope", "https://dashscope.aliyuncs.com/api/v1")

    assert base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert note


def test_estimate_chunk_cost_units_is_deterministic():
    estimate = estimate_chunk_cost_units("HZO showed 2Pr of 40 uC/cm2.")

    assert estimate["chars"] > 0
    assert estimate["rough_tokens"] > 0


def test_profiles_and_evidence_grounding():
    text = "DFT calculations show that oxygen vacancies stabilize the orthorhombic HfO2 phase."

    assert "mechanism_computation" in infer_extraction_profiles(text, "results")
    assert evidence_is_grounded("oxygen vacancies stabilize the orthorhombic HfO2 phase", text)
    assert not evidence_is_grounded("The paper reports an unrelated fabricated claim.", text)


def test_non_evidence_gate_detects_biography_and_reference_fragments():
    biography = (
        "A. Researcher received his Ph.D. degree from Example University. "
        "He is currently an assistant professor and his research interests include HZO devices."
    )
    references = (
        "Table 5 on page 6 A. Author et al., Advanced Functional Materials, vol. 26, pp. 1-9, 2016. "
        "B. Author et al., Applied Physics Letters, vol. 112, 2018. C. Author et al., Physical Review, 2020. "
        "D. Author et al., Nanotechnology, 2021."
    )
    citation_title = (
        "Table 2 on page 7 Improved Reliability for Back-End-of-Line Compatible "
        "Ferroelectric Capacitor in IEEE Electron Device Letters 43 2180-2183"
    )

    assert non_evidence_reason(biography) == "author_biography"
    assert non_evidence_reason(references) == "bibliography_fragment"
    assert non_evidence_reason(citation_title) == "citation_title_only"
    assert non_evidence_reason("The 10 nm HZO film retained 2Pr after 10^9 cycles.") is None


def test_cited_comparison_table_forces_secondary_evidence_scope():
    text = (
        "Table 1 on page 3\n"
        "HfO2 | 10 nm | orthorhombic | ALD | 21\n"
        "HZO | 12 nm | tetragonal | sputtering | 22\n"
        "Si:HfO2 | 9 nm | Pca21 | ALD | 23\n"
    )
    row = {
        "paper_id": "paper_1",
        "pdf_id": "pdf_1",
        "chunk_id": "table_1",
        "page_number": 3,
        "section": "table",
        "paper_type": "experimental",
        "is_review": 0,
        "text": text,
    }
    payload = {
        "properties": [
            {
                "material_ref": "HfO2",
                "property_name": "remanent_polarization_Pr",
                "raw_property_name": "Pr",
                "value": 10,
                "unit": "uC/cm2",
                "evidence_scope": "primary_experiment",
                "evidence_text": "HfO2 | 10 nm | orthorhombic | ALD | 21",
            }
        ],
        "evidences": [
            {
                "evidence_scope": "primary_experiment",
                "evidence_text": "HfO2 | 10 nm | orthorhombic | ALD | 21",
            }
        ],
    }

    coerced = coerce_llm_result_payload(payload, row)

    assert is_cited_comparison_table(text, "table")
    assert coerced["properties"][0]["evidence_scope"] == "cited_secondary"
    assert coerced["evidences"][0]["evidence_scope"] == "cited_secondary"


def test_coerce_v2_mechanism_and_reliability_payload():
    row = {
        "paper_id": "paper_1",
        "pdf_id": "pdf_1",
        "chunk_id": "chunk_1",
        "page_number": 3,
        "text": "After 10^8 cycles, fatigue reduced 2Pr. Oxygen-vacancy accumulation was proposed as the cause.",
    }
    payload = {
        "mechanisms": [
            {
                "mechanism": "oxygen-vacancy accumulation",
                "effect": "fatigue reduced 2Pr",
                "claim_type": "hypothesized",
                "evidence_text": "Oxygen-vacancy accumulation was proposed as the cause.",
            }
        ],
        "reliability_events": [
            {
                "phenomenon": "fatigue",
                "cycles": 1e8,
                "trend": "degraded",
                "evidence_text": "After 10^8 cycles, fatigue reduced 2Pr.",
            }
        ],
    }

    coerced = coerce_llm_result_payload(payload, row)

    assert coerced["mechanisms"][0]["mechanism_type"] == "oxygen-vacancy accumulation"
    assert coerced["reliability_events"][0]["cycle_count"] == 1e8


def test_coerce_qwen_style_payload_to_hfo2_schema():
    row = {
        "paper_id": "paper_1",
        "pdf_id": "pdf_1",
        "chunk_id": "chunk_1",
        "page_number": 3,
        "text": "The 10 nm HZO capacitor showed a 2Pr value of 40 uC/cm2 after wake-up.",
    }
    payload = {
        "materials": [
            {
                "material_name": "HZO",
                "thickness_nm": 10,
                "device_stack": "TiN/HZO/TiN",
                "device_type": "capacitor",
            }
        ],
        "facts": [
            {
                "property": "2Pr",
                "value": "40",
                "unit": "uC/cm2",
                "confidence": 0.8,
            }
        ],
    }

    coerced = coerce_llm_result_payload(payload, row)

    assert coerced["materials"][0]["canonical_name"] == "HZO"
    assert coerced["materials"][0]["material_family"] == "HZO"
    assert coerced["samples"][0]["film_thickness_nm"] == 10
    assert coerced["properties"][0]["property_name"] == "double_remanent_polarization_2Pr"
    assert coerced["properties"][0]["unit"] == "uC/cm2"
    assert coerced["evidences"]


def test_coerce_nonpositive_size_sentinels_to_missing():
    row = {
        "paper_id": "paper_1",
        "pdf_id": "pdf_1",
        "chunk_id": "chunk_1",
        "page_number": 3,
        "text": "The HZO sample thickness and grain size were not reported.",
    }
    payload = {
        "materials": [{"material_name": "HZO"}],
        "samples": [
            {
                "material_ref": "HZO",
                "film_thickness_nm": 0,
                "grain_size_nm": -1,
                "deposition_method": "not reported",
            }
        ],
    }

    coerced = coerce_llm_result_payload(payload, row)
    validated = HfO2ExtractionResult(**coerced)

    assert validated.samples[0].film_thickness_nm is None
    assert validated.samples[0].grain_size_nm is None


def test_table_row_segments_preserve_table_identity_and_cover_rows():
    row = {
        "paper_id": "paper_1",
        "pdf_id": "pdf_1",
        "chunk_id": "table_1",
        "page_number": 2,
        "section": "table",
        "text": (
            "Table 1 on page 2\n"
            "Material | Thickness | Phase | Ref.\n"
            "HfO2 | 10 nm | monoclinic | 1\n"
            "HZO | 8 nm | orthorhombic | 2\n"
            "Si:HfO2 | 12 nm | tetragonal | 3\n"
            "Al:HfO2 | 9 nm | orthorhombic | 4\n"
            "Gd:HfO2 | 11 nm | monoclinic | 5\n"
        ),
    }

    segments = _table_row_segments(row, rows_per_segment=2)

    assert len(segments) == 3
    assert all(segment["chunk_id"] == "table_1" for segment in segments)
    assert all("Material | Thickness | Phase | Ref." in segment["text"] for segment in segments)
    assert sum(segment["text"].count(" nm |") for segment in segments) == 5


def test_merge_extraction_results_deduplicates_items():
    base = HfO2ExtractionResult(
        paper_id="paper_1",
        pdf_id="pdf_1",
        chunk_id="table_1",
        page_number=2,
        warnings=["segment_a"],
    )
    duplicate = HfO2ExtractionResult(**base.model_dump(mode="json"))

    merged = _merge_extraction_results([base, duplicate])

    assert merged.warnings == ["segment_a"]


def test_coerce_preserves_ranges_and_deduplicates_evidence():
    evidence = "The device switched from 118 to 92 pF and formed 8-9 capacitive states."
    row = {
        "paper_id": "paper_1",
        "pdf_id": "pdf_1",
        "chunk_id": "chunk_1",
        "page_number": 4,
        "text": evidence,
    }
    payload = {
        "properties": [
            {
                "property": "capacitance_range",
                "evidence_text": evidence,
            }
        ],
        "evidences": [
            {
                "evidence_text": evidence,
                "evidence_scope": "primary_experiment",
            }
        ],
    }

    coerced = coerce_llm_result_payload(payload, row)

    prop = coerced["properties"][0]
    assert prop["value_min"] == 92
    assert prop["value_max"] == 118
    assert prop["unit"].lower() == "pf"
    assert prop["comparison_operator"] == "range"
    assert len(coerced["evidences"]) == 1


def test_coerce_adds_explicit_application_and_primary_scope():
    text = "The gradual response enables synaptic weight modulation for neuromorphic computing."
    row = {
        "paper_id": "paper_1",
        "pdf_id": "pdf_1",
        "chunk_id": "chunk_1",
        "page_number": 4,
        "section": "results",
        "paper_type": "experimental",
        "text": text,
    }
    payload = {
        "properties": [
            {
                "property": "response_voltage",
                "value": 1.0,
                "unit": "V",
                "evidence_text": text,
            }
        ]
    }

    coerced = coerce_llm_result_payload(payload, row)

    assert coerced["properties"][0]["evidence_scope"] == "primary_experiment"
    assert {item["application"] for item in coerced["applications"]} == {
        "synaptic weight modulation",
        "neuromorphic computing",
    }


def test_specialist_repair_targets_missing_reliability_and_mechanism_layers():
    row = {
        "section": "results",
        "text": (
            "After 10 years, retention testing showed that 2Pr decayed by 51%. We propose controlling oxygen vacancies, "
            "which leads to enhanced switching stochasticity."
        ),
    }
    result = HfO2ExtractionResult(
        paper_id="paper_1",
        pdf_id="pdf_1",
        chunk_id="chunk_1",
        page_number=1,
    )

    targets = _specialist_repair_targets(result, row)

    assert "reliability_events" in targets
    assert "mechanisms" in targets
    assert "relations" in targets


def test_run_extraction_can_disable_llm(tmp_path):
    from backend.db.init_db import init_database
    from backend.db.session import connect
    from backend.services.hfo2_extractor import run_extraction

    db_path = tmp_path / "test.sqlite3"
    init_database(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO document_chunks (
                chunk_id, paper_id, pdf_id, page_number, section, chunk_index, text,
                char_count, token_count, contains_hfo2_keyword, contains_property_keyword,
                is_high_value
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "chunk_1",
                "paper_1",
                "pdf_1",
                1,
                "results",
                0,
                "The Hf0.5Zr0.5O2 film showed 2Pr of 40 uC/cm2 after wake-up.",
                68,
                10,
                1,
                1,
                1,
            ),
        )
        conn.commit()

    stats = run_extraction(db_path=db_path, use_llm=False)

    assert stats["rules_used"] == 1
    assert stats["candidates"] == 1


def test_run_extraction_dry_run_does_not_write(tmp_path):
    from backend.db.init_db import init_database
    from backend.db.session import connect
    from backend.services.hfo2_extractor import run_extraction

    db_path = tmp_path / "test.sqlite3"
    init_database(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO document_chunks (
                chunk_id, paper_id, pdf_id, page_number, section, chunk_index, text,
                char_count, token_count, contains_hfo2_keyword, contains_property_keyword,
                is_high_value
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "chunk_1",
                "paper_1",
                "pdf_1",
                1,
                "results",
                0,
                "The Hf0.5Zr0.5O2 film showed 2Pr of 40 uC/cm2 after wake-up.",
                68,
                10,
                1,
                1,
                1,
            ),
        )
        conn.commit()

    stats = run_extraction(db_path=db_path, use_llm=False, dry_run=True)

    with connect(db_path) as conn:
        candidate_count = conn.execute("SELECT COUNT(*) FROM extraction_candidates").fetchone()[0]
    assert stats["candidates"] == 1
    assert candidate_count == 0


def test_run_extraction_records_empty_results_for_incremental_resume(tmp_path):
    from backend.db.init_db import init_database
    from backend.db.session import connect
    from backend.services.hfo2_extractor import run_extraction

    db_path = tmp_path / "test.sqlite3"
    init_database(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO document_chunks (
                chunk_id, paper_id, pdf_id, page_number, section, chunk_index, text,
                char_count, token_count, contains_hfo2_keyword, contains_property_keyword,
                is_high_value
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "chunk_empty",
                "paper_1",
                "pdf_1",
                2,
                "introduction",
                0,
                "This paragraph discusses reliability trends but reports no sample or measured value.",
                75,
                11,
                1,
                1,
                1,
            ),
        )
        conn.commit()

    stats = run_extraction(db_path=db_path, use_llm=False)

    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT status, confidence, payload_json
            FROM extraction_candidates
            WHERE chunk_id = ?
            """,
            ("chunk_empty",),
        ).fetchone()
        reviewed_count = conn.execute("SELECT COUNT(*) FROM reviewed_facts").fetchone()[0]

    assert stats["empty"] == 1
    assert stats["empty_recorded"] == 1
    assert row["status"] == "empty_result"
    assert row["confidence"] == 0
    assert "prevents repeated LLM calls" in row["payload_json"]
    assert reviewed_count == 0

    second_stats = run_extraction(db_path=db_path, use_llm=False, reset_existing=False)

    assert second_stats["chunks"] == 0
    assert second_stats["skipped_existing"] == 1
