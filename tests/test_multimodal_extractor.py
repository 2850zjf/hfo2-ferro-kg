from __future__ import annotations

from backend.db.init_db import init_database
from backend.db.session import connect
from backend.core.config import get_settings
from backend.schemas.multimodal_extraction_schema import MultimodalExtractionResult
from backend.services.multimodal_extractor import (
    MultimodalOutcome,
    model_for_source,
    multimodal_benchmark_tier,
    store_multimodal_outcome,
)


def test_model_for_source_uses_vision_specialist_for_images(monkeypatch):
    monkeypatch.delenv("HFO2_FERROKG_VISION_MODEL", raising=False)
    assert model_for_source("figure") == "qwen3-vl-plus"
    assert model_for_source("equation") == "qwen3-vl-plus"
    assert model_for_source("table") == get_settings().llm_model


def test_multimodal_benchmark_tier_keeps_visual_estimates_out_of_strong_tiers():
    strong_table = type("Observation", (), {"value_origin": "table_cell", "confidence": 0.9})()
    partial_caption = type("Observation", (), {"value_origin": "caption_text", "confidence": 0.8})()
    visual_estimate = type("Observation", (), {"value_origin": "visual_estimate", "confidence": 0.99})()

    assert multimodal_benchmark_tier(strong_table) == "strong_only"
    assert multimodal_benchmark_tier(partial_caption) == "strong_partial"
    assert multimodal_benchmark_tier(visual_estimate) == "all_traceable"


def test_store_multimodal_equation_result_updates_equation_and_evidence(tmp_path):
    db_path = tmp_path / "multimodal.sqlite3"
    init_database(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO papers (paper_id, title, paper_type, is_review) VALUES ('paper_1', 'HZO model', 'computational', 0)"
        )
        conn.execute(
            """
            INSERT INTO pdf_files (pdf_id, paper_id, file_name, file_path, sha256, file_size, parse_status)
            VALUES ('pdf_1', 'paper_1', 'paper.pdf', 'paper.pdf', 'sha', 1, 'parsed')
            """
        )
        conn.execute(
            """
            INSERT INTO pdf_equations (
                equation_id, paper_id, pdf_id, page_number, equation_index, raw_text,
                normalized_text, candidate_kind, extraction_method, extraction_status
            ) VALUES ('eq_1', 'paper_1', 'pdf_1', 2, 1, 'F = U - TS',
                      'F = U - TS', 'display_equation', 'test', 'candidate')
            """
        )
        conn.execute(
            """
            INSERT INTO multimodal_asset_queue (
                queue_id, source_type, source_id, paper_id, pdf_id, page_number,
                context_text, priority_score, priority_tier, queue_status
            ) VALUES ('queue_1', 'equation', 'eq_1', 'paper_1', 'pdf_1', 2,
                      'HZO free energy equation', 8, 'P0', 'queued')
            """
        )
        row = conn.execute(
            "SELECT * FROM multimodal_asset_queue WHERE source_id='eq_1'"
        ).fetchone()
        conn.commit()

    result = MultimodalExtractionResult(
        source_type="equation",
        source_id="eq_1",
        paper_id="paper_1",
        pdf_id="pdf_1",
        page_number=2,
        is_hafnia_relevant=True,
        is_primary_evidence=True,
        semantic_summary="Landau free-energy relation for the HZO model.",
        visual_type="equation",
        observations=[
            {
                "observation_type": "computational_result",
                "description": "The equation defines the free-energy descriptor used in the model.",
                "material_ref": "HZO",
                "value_origin": "equation_symbol",
                "evidence_scope": "primary_computation",
                "confidence": 0.9,
                "evidence_text": "F = U - TS",
            }
        ],
        equation={
            "is_valid_equation": True,
            "latex": "F = U - TS",
            "plain_text": "F equals U minus T S",
            "equation_role": "free_energy",
            "variables": ["F", "U", "T", "S"],
        },
        confidence=0.9,
    )
    outcome = MultimodalOutcome(result=result, model="qwen-test", usage={"total_tokens": 10})
    store_multimodal_outcome(outcome, row, db_path=db_path)

    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM multimodal_extractions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM multimodal_extraction_runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM multimodal_evidence").fetchone()[0] == 1
        assert conn.execute(
            "SELECT benchmark_tier FROM multimodal_evidence"
        ).fetchone()[0] == "strong_only"
        equation = conn.execute(
            "SELECT latex_text, equation_role, extraction_status FROM pdf_equations WHERE equation_id='eq_1'"
        ).fetchone()
        assert tuple(equation) == ("F = U - TS", "free_energy", "semantic_candidate")
