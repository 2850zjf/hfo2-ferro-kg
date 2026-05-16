from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictBase(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class MaterialFamily(str, Enum):
    HfO2 = "HfO2"
    HZO = "HZO"
    Si_HfO2 = "Si:HfO2"
    Al_HfO2 = "Al:HfO2"
    La_HfO2 = "La:HfO2"
    Y_HfO2 = "Y:HfO2"
    Gd_HfO2 = "Gd:HfO2"
    Sr_HfO2 = "Sr:HfO2"
    mixed_doped_HfO2 = "mixed_doped_HfO2"
    unknown_hafnia = "unknown_hafnia"


class PhaseName(str, Enum):
    monoclinic = "monoclinic"
    orthorhombic = "orthorhombic"
    tetragonal = "tetragonal"
    cubic = "cubic"
    rhombohedral = "rhombohedral"
    amorphous = "amorphous"
    mixed = "mixed"
    unknown = "unknown"


class PropertyName(str, Enum):
    remanent_polarization_Pr = "remanent_polarization_Pr"
    double_remanent_polarization_2Pr = "double_remanent_polarization_2Pr"
    coercive_field_Ec = "coercive_field_Ec"
    saturation_polarization_Ps = "saturation_polarization_Ps"
    dielectric_constant = "dielectric_constant"
    leakage_current_density = "leakage_current_density"
    endurance_cycles = "endurance_cycles"
    retention_time = "retention_time"
    memory_window = "memory_window"
    wake_up = "wake_up"
    fatigue = "fatigue"
    breakdown_field = "breakdown_field"
    band_gap = "band_gap"


class EvidenceExtraction(StrictBase):
    paper_id: str
    pdf_id: str
    chunk_id: str | None = None
    page_number: int | None = None
    evidence_text: str = Field(min_length=8)
    source_type: Literal["text", "table", "figure_caption", "abstract", "metadata", "manual"] = "text"


class MaterialExtraction(StrictBase):
    raw_name: str
    canonical_name: str
    formula: str | None = None
    material_family: MaterialFamily
    base_material: str = "HfO2"
    dopant_elements: list[str] = Field(default_factory=list)
    dopant_concentration: str | None = None
    zr_fraction: float | None = Field(default=None, ge=0, le=1)
    evidence_text: str = Field(min_length=8)


class SampleExtraction(StrictBase):
    material_ref: str
    film_thickness_nm: float | None = Field(default=None, gt=0)
    deposition_method: str | None = None
    top_electrode: str | None = None
    bottom_electrode: str | None = None
    substrate: str | None = None
    annealing_temperature_c: float | None = None
    annealing_time_s: float | None = None
    annealing_atmosphere: str | None = None
    device_stack: str | None = None
    sample_form: str | None = None
    evidence_text: str = Field(min_length=8)


class PhaseExtraction(StrictBase):
    material_ref: str
    phase_name: PhaseName
    space_group: str | None = None
    characterization_method: str | None = None
    evidence_text: str = Field(min_length=8)


class PropertyExtraction(StrictBase):
    material_ref: str
    property_name: PropertyName
    raw_property_name: str
    value: float | None = None
    unit: str | None = None
    normalized_value: float | None = None
    normalized_unit: str | None = None
    measurement_temperature: str | None = None
    measurement_frequency: str | None = None
    electric_field: str | None = None
    device_type: str | None = None
    confidence: float = Field(ge=0, le=1)
    evidence_text: str = Field(min_length=8)
    is_reported_value: bool = True
    is_derived_value: bool = False
    derivation_rule: str | None = None
    review_status: str = "pending"

    @field_validator("unit")
    @classmethod
    def require_unit_for_numeric_values(cls, unit: str | None, info):
        if info.data.get("value") is not None and not unit:
            raise ValueError("unit is required when value is present")
        return unit


class DeviceExtraction(StrictBase):
    device_type: str
    device_stack: str | None = None
    evidence_text: str = Field(min_length=8)


class HfO2ExtractionResult(StrictBase):
    paper_id: str
    pdf_id: str
    chunk_id: str | None = None
    page_number: int | None = None
    materials: list[MaterialExtraction] = Field(default_factory=list)
    samples: list[SampleExtraction] = Field(default_factory=list)
    phases: list[PhaseExtraction] = Field(default_factory=list)
    properties: list[PropertyExtraction] = Field(default_factory=list)
    devices: list[DeviceExtraction] = Field(default_factory=list)
    evidences: list[EvidenceExtraction] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("properties")
    @classmethod
    def properties_must_have_evidence(cls, properties: list[PropertyExtraction]):
        for prop in properties:
            if not prop.evidence_text:
                raise ValueError("each property must include evidence_text")
        return properties
