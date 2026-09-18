from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ParameterSource(StrictModel):
    kind: Literal["ferrox_official_example", "literature", "simulation_assumption"]
    reference: str
    calibrated: bool = False


class HZOMaterialCoefficients(StrictModel):
    epsilon_x: float = Field(default=24.0, gt=0)
    epsilon_z: float = Field(default=24.0, gt=0)
    alpha: float = -2.5e9
    beta: float = 6.0e10
    gamma: float = 1.5e11
    kinetic_coefficient: float = Field(default=100.0, gt=0)
    g11: float = 1.0e-9
    g44: float = 1.0e-9
    g44_p: float = 0.0
    g12: float = 0.0
    alpha_12: float = 0.0
    alpha_112: float = 0.0
    alpha_123: float = 0.0
    source: ParameterSource = Field(
        default_factory=lambda: ParameterSource(
            kind="ferrox_official_example",
            reference="FerroX Exec/Examples/inputs_mfim_Noeb",
            calibrated=False,
        )
    )


class VoltageProtocol(StrictModel):
    mode: Literal["zero_bias", "quasistatic_triangle"] = "quasistatic_triangle"
    voltage_min_v: float = -3.0
    voltage_max_v: float = 3.0
    voltage_step_v: float = Field(default=0.25, gt=0)
    settle_steps: int = Field(default=5000, ge=1)
    max_voltage_points: int | None = Field(default=None, ge=1)
    frequency_hz: float | None = Field(default=None, gt=0)
    source: ParameterSource = Field(
        default_factory=lambda: ParameterSource(
            kind="simulation_assumption", reference="default +/-3 V quasi-static sweep"
        )
    )

    @model_validator(mode="after")
    def validate_range(self):
        if self.mode == "quasistatic_triangle":
            if not self.voltage_min_v < 0 < self.voltage_max_v:
                raise ValueError("triangle sweep must cross zero")
            if abs(abs(self.voltage_min_v) - self.voltage_max_v) > 1e-12:
                raise ValueError("FerroX triangle sweep currently requires symmetric limits")
        return self

    @property
    def voltage_points(self) -> int:
        if self.mode == "zero_bias":
            return 1
        if self.max_voltage_points is not None:
            return self.max_voltage_points
        amplitude = self.voltage_max_v
        return int(round(4.0 * amplitude / self.voltage_step_v)) + 1


class MicrostructureSpec(StrictModel):
    tetragonal_fraction: Literal[0.0, 0.1, 0.25, 0.4] = 0.0
    orientation_spread_deg: Literal[0.0, 15.0, 30.0] = 0.0
    random_seed: int = Field(default=1, ge=1, le=2_147_483_647)
    tile_columns: int = Field(default=5, ge=1)
    tile_rows: int = Field(default=4, ge=1)
    interpretation: Literal["controlled_synthetic_microstructure"] = (
        "controlled_synthetic_microstructure"
    )

    @model_validator(mode="after")
    def validate_fraction_resolution(self):
        cells = self.tile_columns * self.tile_rows
        selected = self.tetragonal_fraction * cells
        if abs(selected - round(selected)) > 1e-9:
            raise ValueError("tetragonal fraction is not representable by the tile grid")
        return self


class GridSpec(StrictModel):
    lateral_x_nm: float = Field(default=16.0, gt=0)
    lateral_y_nm: float = Field(default=16.0, gt=0)
    cell_size_nm: Literal[0.5, 1.0] = 0.5
    max_grid_size: int = Field(default=64, ge=4)

    @model_validator(mode="after")
    def validate_divisibility(self):
        for length in (self.lateral_x_nm, self.lateral_y_nm):
            cells = length / self.cell_size_nm
            if abs(cells - round(cells)) > 1e-9:
                raise ValueError("lateral size must be divisible by cell size")
        return self


class BenchmarkEvidence(StrictModel):
    doi: str
    paper_id: str
    pdf_id: str
    page_number: int = Field(ge=1)
    evidence_text: str = Field(min_length=8)
    thickness_nm: float = Field(gt=0)
    voltage_or_frequency: str = Field(min_length=1)
    pr_or_2pr: str = Field(min_length=1)
    ec: str = Field(min_length=1)


class FerroXExperimentSpec(StrictModel):
    schema_version: Literal["ferrox-experiment-v1"] = "ferrox-experiment-v1"
    name: str = Field(default="HZO MFM baseline", min_length=3, max_length=120)
    preset: Literal["mfm_baseline", "phase_orientation_screen", "mfim_deadlayer_scan"] = "mfm_baseline"
    material_formula: Literal["Hf0.5Zr0.5O2"] = "Hf0.5Zr0.5O2"
    stack: Literal["TiN/HZO/TiN", "TiN/HZO/Al2O3/TiN"] = "TiN/HZO/TiN"
    thickness_nm: float = Field(default=10.0, ge=1.0, le=100.0)
    deadlayer_nm: float = Field(default=0.0, ge=0.0, le=50.0)
    dielectric_epsilon: float = Field(default=10.0, gt=0.0)
    material: HZOMaterialCoefficients = Field(default_factory=HZOMaterialCoefficients)
    voltage: VoltageProtocol = Field(default_factory=VoltageProtocol)
    microstructure: MicrostructureSpec = Field(default_factory=MicrostructureSpec)
    grid: GridSpec = Field(default_factory=GridSpec)
    mpi_ranks: int = Field(default=4, ge=1, le=64)
    omp_threads: int = Field(default=4, ge=1, le=64)
    dt_seconds: float = Field(default=2e-13, gt=0, le=1e-6)
    plot_interval: int = Field(default=100, ge=1)
    benchmark: BenchmarkEvidence | None = None
    quantitative_calibration: bool = False
    scientific_status: Literal["simulation_only_not_experimental_fact"] = (
        "simulation_only_not_experimental_fact"
    )
    kg_writeback: Literal[False] = False

    @model_validator(mode="after")
    def validate_experiment(self):
        z_cells = self.thickness_nm / self.grid.cell_size_nm
        if abs(z_cells - round(z_cells)) > 1e-9:
            raise ValueError("thickness must be divisible by cell size")
        if self.deadlayer_nm > 0.0:
            if self.stack != "TiN/HZO/Al2O3/TiN":
                raise ValueError("deadlayer requires TiN/HZO/Al2O3/TiN stack")
            fe_nm = self.thickness_nm - self.deadlayer_nm
            if fe_nm <= 0:
                raise ValueError("deadlayer thicker than stack")
            for length in (fe_nm, self.deadlayer_nm):
                cells = length / self.grid.cell_size_nm
                if abs(cells - round(cells)) > 1e-9:
                    raise ValueError("deadlayer/FE thickness must be divisible by cell size")
        elif self.stack == "TiN/HZO/Al2O3/TiN":
            raise ValueError("TiN/HZO/Al2O3/TiN stack requires deadlayer_nm > 0")
        if self.quantitative_calibration:
            if self.benchmark is None or not self.benchmark.doi.strip():
                raise ValueError("quantitative calibration requires evidence-complete DOI benchmark")
            if not self.material.source.calibrated:
                raise ValueError("quantitative calibration requires calibrated material coefficients")
        return self

    @property
    def nx(self) -> int:
        return round(self.grid.lateral_x_nm / self.grid.cell_size_nm)

    @property
    def ny(self) -> int:
        return round(self.grid.lateral_y_nm / self.grid.cell_size_nm)

    @property
    def nz(self) -> int:
        return round(self.thickness_nm / self.grid.cell_size_nm)

