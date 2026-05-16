from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.schemas.hfo2_extraction_schema import (
    HfO2ExtractionResult,
    PropertyExtraction,
    PropertyName,
)


def test_example_extraction_json_validates():
    result = HfO2ExtractionResult(
        paper_id="paper_1",
        pdf_id="pdf_1",
        chunk_id="chunk_1",
        page_number=1,
        materials=[
            {
                "raw_name": "Hf0.5Zr0.5O2",
                "canonical_name": "Hf0.5Zr0.5O2",
                "formula": "Hf0.5Zr0.5O2",
                "material_family": "HZO",
                "base_material": "HfO2",
                "dopant_elements": ["Zr"],
                "zr_fraction": 0.5,
                "evidence_text": "The 10 nm Hf0.5Zr0.5O2 films were deposited by ALD.",
            }
        ],
        properties=[
            {
                "material_ref": "Hf0.5Zr0.5O2",
                "property_name": "double_remanent_polarization_2Pr",
                "raw_property_name": "2Pr",
                "value": 40,
                "unit": "μC/cm²",
                "confidence": 0.85,
                "evidence_text": "A 2Pr value of 40 μC/cm2 was obtained after wake-up cycling.",
            }
        ],
    )

    assert result.materials[0].material_family == "HZO"
    assert result.properties[0].property_name == PropertyName.double_remanent_polarization_2Pr


def test_property_requires_evidence_text():
    with pytest.raises(ValidationError):
        PropertyExtraction(
            material_ref="HZO",
            property_name="remanent_polarization_Pr",
            raw_property_name="Pr",
            value=20,
            unit="μC/cm²",
            confidence=0.7,
            evidence_text="",
        )


def test_pr_and_2pr_are_distinct_enum_values():
    assert PropertyName.remanent_polarization_Pr != PropertyName.double_remanent_polarization_2Pr
