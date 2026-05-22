from __future__ import annotations

from backend.services.llm_extractor import (
    build_user_input,
    coerce_llm_result_payload,
    estimate_chunk_cost_units,
    llm_status,
    normalize_base_url,
)


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
