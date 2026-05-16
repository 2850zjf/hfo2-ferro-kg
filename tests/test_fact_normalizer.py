from __future__ import annotations

from backend.schemas.hfo2_extraction_schema import PropertyExtraction
from backend.services.fact_normalizer import normalize_property, normalize_property_alias, normalize_unit


def test_unit_standardization_and_kv_conversion():
    prop = PropertyExtraction(
        material_ref="HZO",
        property_name="coercive_field_Ec",
        raw_property_name="Ec",
        value=1200,
        unit="kV/cm",
        confidence=0.8,
        evidence_text="The coercive field Ec was 1200 kV/cm for the HZO capacitor.",
    )

    normalized, warnings = normalize_property(prop)

    assert normalized.normalized_unit == "MV/cm"
    assert normalized.normalized_value == 1.2
    assert warnings == []


def test_2pr_alias_does_not_become_pr():
    assert normalize_property_alias("2Pr") == "double_remanent_polarization_2Pr"
    assert normalize_property_alias("Pr") == "remanent_polarization_Pr"


def test_unusual_polarization_marked_for_check():
    prop = PropertyExtraction(
        material_ref="HZO",
        property_name="double_remanent_polarization_2Pr",
        raw_property_name="2Pr",
        value=250,
        unit="uC/cm2",
        confidence=0.8,
        evidence_text="The reported 2Pr was 250 uC/cm2 in the measured capacitor.",
    )

    normalized, warnings = normalize_property(prop)

    assert normalized.normalized_unit == "μC/cm²"
    assert normalized.review_status == "needs_human_review"
    assert any("unusually high" in warning for warning in warnings)


def test_unit_aliases():
    assert normalize_unit("uC/cm2") == "μC/cm²"
    assert normalize_unit("MV cm−1") == "MV/cm"
