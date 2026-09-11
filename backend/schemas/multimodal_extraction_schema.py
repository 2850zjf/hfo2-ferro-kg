from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.schemas.hfo2_extraction_schema import PropertyName


class StrictMultimodalBase(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


EvidenceScope = Literal[
    "primary_experiment",
    "primary_computation",
    "cited_secondary",
    "review_summary",
    "unclear",
]


class MultimodalObservation(StrictMultimodalBase):
    observation_type: Literal[
        "numeric_value",
        "trend",
        "comparison",
        "phase_evidence",
        "morphology",
        "device_stack",
        "process_condition",
        "mechanism",
        "computational_result",
        "application",
        "other",
    ]
    description: str = Field(min_length=8)
    property_name: PropertyName | None = None
    raw_value_text: str | None = None
    value: float | None = None
    value_min: float | None = None
    value_max: float | None = None
    unit: str | None = None
    material_ref: str | None = None
    sample_ref: str | None = None
    series_or_panel: str | None = None
    condition: str | None = None
    value_origin: Literal[
        "table_cell",
        "axis_label",
        "caption_text",
        "body_text",
        "visual_estimate",
        "equation_symbol",
        "derived",
        "not_applicable",
        "unclear",
    ] = "unclear"
    evidence_scope: EvidenceScope = "unclear"
    confidence: float = Field(ge=0, le=1)
    evidence_text: str = Field(min_length=4)

    @model_validator(mode="after")
    def validate_numeric_observation(self):
        has_numeric = any(value is not None for value in (self.value, self.value_min, self.value_max))
        if has_numeric and not self.unit:
            raise ValueError("unit is required when a numeric value is reported")
        return self


class EquationSemantics(StrictMultimodalBase):
    is_valid_equation: bool
    latex: str | None = None
    plain_text: str | None = None
    equation_role: Literal[
        "free_energy",
        "constitutive_relation",
        "kinetics",
        "transport",
        "reliability",
        "device_model",
        "normalization",
        "computational_descriptor",
        "other",
        "unknown",
    ] = "unknown"
    variables: list[str] = Field(default_factory=list)
    assumptions_or_domain: str | None = None


class MultimodalExtractionResult(StrictMultimodalBase):
    source_type: Literal["figure", "table", "equation"]
    source_id: str
    paper_id: str
    pdf_id: str
    page_number: int = Field(ge=1)
    is_hafnia_relevant: bool
    is_primary_evidence: bool
    semantic_summary: str = Field(min_length=4)
    visual_type: Literal[
        "plot",
        "hysteresis_loop",
        "diffraction_pattern",
        "micrograph",
        "phase_map",
        "device_schematic",
        "process_schematic",
        "calculation_plot",
        "table_image",
        "equation",
        "other",
        "unknown",
    ] = "unknown"
    observations: list[MultimodalObservation] = Field(default_factory=list)
    equation: EquationSemantics | None = None
    confidence: float = Field(ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_equation_payload(self):
        if self.source_type == "equation" and self.equation is None:
            raise ValueError("equation semantics are required for equation assets")
        return self
