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


def test_v2_schema_supports_reliability_mechanism_and_computation():
    result = HfO2ExtractionResult(
        paper_id="paper_1",
        pdf_id="pdf_1",
        chunk_id="chunk_1",
        page_number=2,
        reliability_events=[
            {
                "material_ref": "HZO",
                "phenomenon": "fatigue",
                "cycle_count": 1e9,
                "trend": "degraded",
                "confidence": 0.8,
                "evidence_text": "The switchable polarization degraded after 10^9 cycles.",
            }
        ],
        mechanisms=[
            {
                "material_ref": "HZO",
                "mechanism_type": "interface oxygen reservoir",
                "outcome": "suppressed vacancy accumulation",
                "relation_nature": "hypothesized",
                "confidence": 0.7,
                "evidence_text": "The interface acts as an oxygen reservoir and suppresses vacancy accumulation.",
            }
        ],
        computations=[
            {
                "material_ref": "HfO2",
                "method_family": "DFT",
                "descriptor_name": "phase_energy_difference",
                "value": 84.3,
                "unit": "meV/f.u.",
                "reference_state": "monoclinic HfO2",
                "evidence_text": "The orthorhombic phase is 84.3 meV/f.u. above the monoclinic phase.",
            }
        ],
    )

    assert result.reliability_events[0].phenomenon == "fatigue"
    assert result.mechanisms[0].relation_nature == "hypothesized"
    assert result.computations[0].method_family == "DFT"


def test_v22_schema_preserves_numeric_ranges():
    prop = PropertyExtraction(
        material_ref="HZO",
        property_name="other_hafnia_property",
        raw_property_name="capacitance_range",
        raw_value_text="118 to 92 pF",
        value_min=92,
        value_max=118,
        comparison_operator="range",
        unit="pF",
        confidence=0.8,
        evidence_text="The device switched from 118 to 92 pF under sequential voltage sweeps.",
    )

    assert prop.value is None
    assert prop.value_min == 92
    assert prop.value_max == 118
