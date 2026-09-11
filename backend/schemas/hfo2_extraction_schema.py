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
    imprint_voltage = "imprint_voltage"
    dielectric_loss = "dielectric_loss"
    negative_capacitance = "negative_capacitance"
    switched_polarization_fraction = "switched_polarization_fraction"
    polarization_change_DeltaP = "polarization_change_DeltaP"
    switching_time = "switching_time"
    on_off_ratio = "on_off_ratio"
    threshold_voltage_shift = "threshold_voltage_shift"
    subthreshold_swing = "subthreshold_swing"
    grain_size = "grain_size"
    phase_fraction = "phase_fraction"
    oxygen_vacancy_concentration = "oxygen_vacancy_concentration"
    vacancy_formation_energy = "vacancy_formation_energy"
    vacancy_migration_barrier = "vacancy_migration_barrier"
    phase_energy_difference = "phase_energy_difference"
    switching_energy_barrier = "switching_energy_barrier"
    computed_polarization = "computed_polarization"
    interface_energy = "interface_energy"
    other_hafnia_property = "other_hafnia_property"


class EvidenceExtraction(StrictBase):
    paper_id: str
    pdf_id: str
    chunk_id: str | None = None
    page_number: int | None = None
    evidence_text: str = Field(min_length=8)
    source_type: Literal["text", "table", "figure_caption", "abstract", "metadata", "manual"] = "text"
    evidence_scope: Literal[
        "primary_experiment",
        "primary_computation",
        "review_secondary",
        "cited_secondary",
        "unknown",
    ] = "unknown"
    evidence_role: Literal[
        "material",
        "process",
        "phase",
        "measurement",
        "reliability",
        "mechanism",
        "computation",
        "application",
        "other",
    ] = "other"
    figure_or_table_id: str | None = None


class MaterialExtraction(StrictBase):
    raw_name: str
    canonical_name: str
    formula: str | None = None
    material_family: MaterialFamily
    base_material: str = "HfO2"
    dopant_elements: list[str] = Field(default_factory=list)
    dopant_concentration: str | None = None
    zr_fraction: float | None = Field(default=None, ge=0, le=1)
    composition_descriptor: str | None = None
    layer_sequence: str | None = None
    superlattice_period: str | None = None
    evidence_text: str = Field(min_length=8)


class SampleExtraction(StrictBase):
    material_ref: str
    sample_ref: str | None = None
    sample_name: str | None = None
    film_thickness_nm: float | None = Field(default=None, gt=0)
    deposition_method: str | None = None
    top_electrode: str | None = None
    bottom_electrode: str | None = None
    substrate: str | None = None
    annealing_temperature_c: float | None = None
    annealing_time_s: float | None = None
    annealing_atmosphere: str | None = None
    annealing_method: str | None = None
    oxygen_partial_pressure: str | None = None
    oxygen_vacancy_context: str | None = None
    oxygen_reservoir: str | None = None
    interface_layer: str | None = None
    interface_termination: str | None = None
    device_stack: str | None = None
    sample_form: str | None = None
    growth_orientation: str | None = None
    strain_state: str | None = None
    grain_size_nm: float | None = Field(default=None, gt=0)
    electrode_area: str | None = None
    evidence_text: str = Field(min_length=8)


class PhaseExtraction(StrictBase):
    material_ref: str
    phase_name: PhaseName
    space_group: str | None = None
    phase_fraction: str | None = None
    crystal_orientation: str | None = None
    domain_orientation: str | None = None
    characterization_method: str | None = None
    phase_fraction_value: float | None = Field(default=None, ge=0, le=1)
    lattice_parameters: str | None = None
    grain_size_nm: float | None = Field(default=None, gt=0)
    evidence_text: str = Field(min_length=8)


class PropertyExtraction(StrictBase):
    material_ref: str
    property_name: PropertyName
    raw_property_name: str
    raw_value_text: str | None = None
    value: float | None = None
    value_min: float | None = None
    value_max: float | None = None
    comparison_operator: Literal["eq", "lt", "le", "gt", "ge", "approx", "range", "unknown"] = "unknown"
    unit: str | None = None
    normalized_value: float | None = None
    normalized_unit: str | None = None
    measurement_temperature: str | None = None
    measurement_frequency: str | None = None
    measurement_method: str | None = None
    waveform: str | None = None
    pulse_width: str | None = None
    sweep_rate: str | None = None
    read_voltage: str | None = None
    stress_voltage: str | None = None
    electric_field: str | None = None
    measurement_state: str | None = None
    cycle_number: float | None = None
    device_type: str | None = None
    uncertainty: str | None = None
    statistical_scope: str | None = None
    evidence_scope: Literal[
        "primary_experiment",
        "primary_computation",
        "review_secondary",
        "cited_secondary",
        "unknown",
    ] = "unknown"
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
    channel_material: str | None = None
    gate_stack: str | None = None
    area: str | None = None
    evidence_text: str = Field(min_length=8)


class ProcessStepExtraction(StrictBase):
    material_ref: str
    sample_ref: str | None = None
    step_order: int | None = Field(default=None, ge=0)
    step_type: Literal[
        "deposition",
        "annealing",
        "electrode_deposition",
        "patterning",
        "interface_treatment",
        "measurement",
        "other",
    ]
    method: str | None = None
    precursors: list[str] = Field(default_factory=list)
    temperature_c: float | None = None
    time_s: float | None = None
    atmosphere: str | None = None
    pressure: str | None = None
    cycle_count: float | None = None
    condition: str | None = None
    evidence_text: str = Field(min_length=8)


class ReliabilityExtraction(StrictBase):
    material_ref: str
    sample_ref: str | None = None
    phenomenon: Literal[
        "wake_up",
        "fatigue",
        "endurance",
        "retention",
        "imprint",
        "breakdown",
        "leakage_degradation",
        "variability",
        "recovery",
        "other",
    ]
    metric_name: str | None = None
    initial_value: float | None = None
    final_value: float | None = None
    unit: str | None = None
    cycle_count: float | None = None
    elapsed_time_s: float | None = None
    electric_field: str | None = None
    stress_voltage: str | None = None
    pulse_width: str | None = None
    frequency: str | None = None
    temperature: str | None = None
    trend: Literal["improved", "degraded", "stable", "non_monotonic", "unknown"] = "unknown"
    failure_mode: str | None = None
    device_type: str | None = None
    confidence: float = Field(default=0.5, ge=0, le=1)
    evidence_scope: Literal[
        "primary_experiment",
        "primary_computation",
        "review_secondary",
        "cited_secondary",
        "unknown",
    ] = "unknown"
    evidence_text: str = Field(min_length=8)


class MechanismExtraction(StrictBase):
    material_ref: str
    sample_ref: str | None = None
    mechanism_type: str
    driver: str | None = None
    outcome: str
    affected_entity: str | None = None
    relation_nature: Literal[
        "causal",
        "correlational",
        "hypothesized",
        "computed",
        "review_summary",
        "unknown",
    ] = "unknown"
    direction: Literal["increase", "decrease", "stabilize", "destabilize", "mixed", "unknown"] = "unknown"
    confidence: float = Field(default=0.5, ge=0, le=1)
    evidence_scope: Literal[
        "primary_experiment",
        "primary_computation",
        "review_secondary",
        "cited_secondary",
        "unknown",
    ] = "unknown"
    evidence_text: str = Field(min_length=8)


class ComputationExtraction(StrictBase):
    material_ref: str
    method_family: Literal[
        "DFT",
        "DFPT",
        "NEB",
        "AIMD",
        "MD",
        "ML_potential",
        "phase_field",
        "Landau",
        "TCAD",
        "compact_model",
        "kinetic_model",
        "other",
    ]
    code_or_model: str | None = None
    functional: str | None = None
    pseudopotential: str | None = None
    structure_or_phase: str | None = None
    supercell: str | None = None
    kpoint_mesh: str | None = None
    defect_type: str | None = None
    charge_state: str | None = None
    strain: str | None = None
    electric_field: str | None = None
    temperature: str | None = None
    descriptor_name: str
    value: float | None = None
    unit: str | None = None
    reference_state: str | None = None
    conclusion: str | None = None
    confidence: float = Field(default=0.5, ge=0, le=1)
    evidence_text: str = Field(min_length=8)


class ApplicationExtraction(StrictBase):
    material_ref: str
    device_type: str | None = None
    application: str
    target_metric: str | None = None
    value: float | None = None
    unit: str | None = None
    operating_condition: str | None = None
    confidence: float = Field(default=0.5, ge=0, le=1)
    evidence_text: str = Field(min_length=8)


class RelationExtraction(StrictBase):
    subject_ref: str
    predicate: Literal[
        "INCREASES",
        "DECREASES",
        "STABILIZES",
        "DESTABILIZES",
        "TRANSFORMS_TO",
        "CAUSES",
        "CORRELATES_WITH",
        "SUPPRESSES",
        "PROMOTES",
        "LIMITS",
        "COMPARED_WITH",
    ]
    object_ref: str
    condition: str | None = None
    confidence: float = Field(default=0.5, ge=0, le=1)
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
    process_steps: list[ProcessStepExtraction] = Field(default_factory=list)
    reliability_events: list[ReliabilityExtraction] = Field(default_factory=list)
    mechanisms: list[MechanismExtraction] = Field(default_factory=list)
    computations: list[ComputationExtraction] = Field(default_factory=list)
    applications: list[ApplicationExtraction] = Field(default_factory=list)
    relations: list[RelationExtraction] = Field(default_factory=list)
    evidences: list[EvidenceExtraction] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("properties")
    @classmethod
    def properties_must_have_evidence(cls, properties: list[PropertyExtraction]):
        for prop in properties:
            if not prop.evidence_text:
                raise ValueError("each property must include evidence_text")
        return properties
