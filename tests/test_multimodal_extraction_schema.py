from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.schemas.multimodal_extraction_schema import MultimodalExtractionResult


def test_multimodal_equation_requires_semantics():
    with pytest.raises(ValidationError):
        MultimodalExtractionResult(
            source_type="equation",
            source_id="eq_1",
            paper_id="paper_1",
            pdf_id="pdf_1",
            page_number=1,
            is_hafnia_relevant=True,
            is_primary_evidence=True,
            semantic_summary="A displayed equation.",
            visual_type="equation",
            confidence=0.8,
        )


def test_multimodal_numeric_observation_requires_unit():
    payload = {
        "source_type": "figure",
        "source_id": "fig_1",
        "paper_id": "paper_1",
        "pdf_id": "pdf_1",
        "page_number": 2,
        "is_hafnia_relevant": True,
        "is_primary_evidence": True,
        "semantic_summary": "A polarization comparison plot.",
        "visual_type": "plot",
        "confidence": 0.9,
        "observations": [
            {
                "observation_type": "numeric_value",
                "description": "The plotted value is reported for the HZO sample.",
                "value": 20.0,
                "material_ref": "HZO",
                "evidence_scope": "primary_experiment",
                "confidence": 0.8,
                "evidence_text": "Pr = 20",
            }
        ],
    }
    with pytest.raises(ValidationError):
        MultimodalExtractionResult(**payload)

    payload["observations"][0]["unit"] = "μC/cm²"
    result = MultimodalExtractionResult(**payload)
    assert result.observations[0].unit == "μC/cm²"
