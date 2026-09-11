from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from backend.core.config import PROJECT_ROOT
from backend.schemas.ferrox_experiment_schema import FerroXExperimentSpec


RUNS_ROOT = PROJECT_ROOT / "data" / "ferrox_runs"
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
FERROX_COMMIT = "ac4606e83318bde632ab1e7d9a18f48f64e48f57"
AMREX_VERSION = "25.07-28-g3b5f539e008b"


@dataclass(frozen=True)
class FerroXConfig:
    distro: str = "Ubuntu-24.04"
    executable: str = "/opt/ferrox/bin/ferrox"
    timeout_seconds: int = 86400

    @classmethod
    def from_env(cls) -> "FerroXConfig":
        return cls(
            distro=os.getenv("HFO2_FERROKG_FERROX_DISTRO", "Ubuntu-24.04"),
            executable=os.getenv("HFO2_FERROKG_FERROX_EXECUTABLE", "/opt/ferrox/bin/ferrox"),
            timeout_seconds=int(os.getenv("HFO2_FERROKG_FERROX_TIMEOUT_SECONDS", "86400")),
        )

    def validate(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", self.distro):
            raise ValueError("invalid_wsl_distro")
        if not self.executable.startswith("/") or any(c in self.executable for c in "\r\n\0"):
            raise ValueError("invalid_ferrox_executable")
        if self.timeout_seconds <= 0:
            raise ValueError("invalid_ferrox_timeout")


def windows_to_wsl_path(
    path: str | Path,
    config: FerroXConfig | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    config = config or FerroXConfig.from_env()
    config.validate()
    resolved = str(Path(path).expanduser().resolve())
    result = runner(
        ["wsl.exe", "-d", config.distro, "--", "wslpath", "-a", resolved],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip().startswith("/"):
        raise RuntimeError("wsl_path_conversion_failed")
    return result.stdout.strip()


def build_command(
    config: FerroXConfig,
    input_path_wsl: str,
    mpi_ranks: int = 4,
    omp_threads: int = 4,
) -> list[str]:
    config.validate()
    if not input_path_wsl.startswith("/") or any(c in input_path_wsl for c in "\r\n\0"):
        raise ValueError("invalid_wsl_input_path")
    if not 1 <= mpi_ranks <= 64 or not 1 <= omp_threads <= 64:
        raise ValueError("invalid_parallelism")
    return [
        "wsl.exe", "-d", config.distro, "--", "env",
        f"OMP_NUM_THREADS={omp_threads}", "mpirun", "-n", str(mpi_ranks),
        config.executable, input_path_wsl,
    ]


def _legacy_spec(parameters: dict[str, Any]) -> FerroXExperimentSpec:
    cell = float(parameters.get("cell_size_nm", 0.5))
    nx = int(parameters.get("nx", 64))
    ny = int(parameters.get("ny", 64))
    nz = int(parameters.get("nz", 20))
    thickness = float(parameters.get("thickness_nm", nz * cell))
    return FerroXExperimentSpec.model_validate(
        {
            "thickness_nm": thickness,
            "grid": {
                "lateral_x_nm": nx * cell,
                "lateral_y_nm": ny * cell,
                "cell_size_nm": cell,
            },
            "dt_seconds": float(parameters.get("dt", 2e-13)),
            "plot_interval": int(parameters.get("plot_interval", min(int(parameters.get("steps", 1000)), 100))),
            "mpi_ranks": int(parameters.get("mpi_ranks", 4)),
            "omp_threads": int(parameters.get("omp_threads", 4)),
            "microstructure": {
                "tetragonal_fraction": float(parameters.get("tetragonal_fraction", 0.0)),
                "orientation_spread_deg": float(parameters.get("orientation_spread_deg", 0.0)),
                "random_seed": int(parameters.get("random_seed", 1)),
            },
            "voltage": {
                "mode": parameters.get("voltage_mode", "zero_bias"),
                "voltage_min_v": float(parameters.get("voltage_min_v", -3.0)),
                "voltage_max_v": float(parameters.get("voltage_max_v", 3.0)),
                "voltage_step_v": float(parameters.get("voltage_step_v", 0.25)),
                "settle_steps": int(parameters.get("steps", 1000)),
            },
        }
    )


def coerce_spec(value: FerroXExperimentSpec | dict[str, Any]) -> FerroXExperimentSpec:
    if isinstance(value, FerroXExperimentSpec):
        return value
    if "schema_version" in value or "grid" in value:
        return FerroXExperimentSpec.model_validate(value)
    return _legacy_spec(value)


def tile_assignments(spec: FerroXExperimentSpec) -> list[dict[str, float | int | bool]]:
    micro = spec.microstructure
    count = micro.tile_columns * micro.tile_rows
    rng = random.Random(micro.random_seed)
    tetragonal = set(rng.sample(range(count), round(count * micro.tetragonal_fraction)))
    angles = [0.0] if micro.orientation_spread_deg == 0 else [
        -micro.orientation_spread_deg, 0.0, micro.orientation_spread_deg
    ]
    return [
        {
            "tile": index,
            "column": index % micro.tile_columns,
            "row": index // micro.tile_columns,
            "tetragonal": index in tetragonal,
            "theta_deg": angles[rng.randrange(len(angles))],
        }
        for index in range(count)
    ]


def _tile_expression(spec: FerroXExperimentSpec, field: str) -> str:
    x0 = -spec.grid.lateral_x_nm / 2.0
    y0 = -spec.grid.lateral_y_nm / 2.0
    dx = spec.grid.lateral_x_nm / spec.microstructure.tile_columns
    dy = spec.grid.lateral_y_nm / spec.microstructure.tile_rows
    pieces: list[str] = []
    for tile in tile_assignments(spec):
        value = 1.0 if field == "tetragonal" and tile["tetragonal"] else (
            float(tile["theta_deg"]) if field == "theta" else 0.0
        )
        col, row = int(tile["column"]), int(tile["row"])
        xl, xh = (x0 + col * dx) * 1e-9, (x0 + (col + 1) * dx) * 1e-9
        yl, yh = (y0 + row * dy) * 1e-9, (y0 + (row + 1) * dy) * 1e-9
        pieces.append(f"({value:.8g})*(x>={xl:.12g})*(x<{xh:.12g})*(y>={yl:.12g})*(y<{yh:.12g})")
    return " + ".join(pieces)


def render_inputs(parameters: FerroXExperimentSpec | dict[str, Any]) -> str:
    spec = coerce_spec(parameters)
    material, voltage = spec.material, spec.voltage
    half_x = spec.grid.lateral_x_nm / 2.0 * 1e-9
    half_y = spec.grid.lateral_y_nm / 2.0 * 1e-9
    height = spec.thickness_nm * 1e-9
    voltage_sweep = int(voltage.mode == "quasistatic_triangle")
    initial_voltage = voltage.voltage_min_v if voltage_sweep else 0.0
    total_steps = voltage.settle_steps * voltage.voltage_points
    return f"""# HfO2-FerroKG FerroX input: {spec.material_formula}, {spec.stack}
# Simulation output is not experimental evidence.
# scientific_status={spec.scientific_status}; kg_writeback=false
# coefficients_source={material.source.kind}: {material.source.reference}
domain.prob_lo = {-half_x:.12g} {-half_y:.12g} 0.0
domain.prob_hi = {half_x:.12g} {half_y:.12g} {height:.12g}
domain.n_cell = {spec.nx} {spec.ny} {spec.nz}
domain.max_grid_size = {min(spec.nx, spec.grid.max_grid_size)} {min(spec.ny, spec.grid.max_grid_size)} {min(spec.nz, spec.grid.max_grid_size)}
domain.coord_sys = cartesian
prob_type = 2
TimeIntegratorOrder = 1
nsteps = {total_steps}
plot_int = {spec.plot_interval}
dt = {spec.dt_seconds:.12g}
random_seed = {spec.microstructure.random_seed}
Remnant_P = 0.0 0.0 0.002
P_BC_flag_lo = 3 3 0
P_BC_flag_hi = 3 3 1
lambda = 3.0e-9
domain.is_periodic = 1 1 0
boundary.hi = per per dir(0.0)
boundary.lo = per per dir(0.0)
voltage_sweep = {voltage_sweep}
Phi_Bc_lo = 0.0
Phi_Bc_hi = {initial_voltage:.12g}
inc_step = {voltage.settle_steps}
Phi_Bc_inc = {voltage.voltage_step_v if voltage_sweep else 0.0:.12g}
Phi_Bc_hi_max = {voltage.voltage_max_v if voltage_sweep else 0.0:.12g}
num_Vapp_max = {voltage.voltage_points}
phi_tolerance = 1.0e-5
SC_lo = -1.0 -1.0 -1.0
SC_hi = -1.0 -1.0 -1.0
DE_lo = -1.0 -1.0 -1.0
DE_hi = -1.0 -1.0 -1.0
FE_lo = {-half_x:.12g} {-half_y:.12g} 0.0
FE_hi = {half_x:.12g} {half_y:.12g} {height:.12g}
Coordinate_Transformation = 1
use_Euler_angles = 1
tphase_geom.tphase_geom_function(x,y,z) = "{_tile_expression(spec, 'tetragonal')}"
angle_alpha.alpha_function(x,y,z) = "0.0"
angle_beta.beta_function(x,y,z) = "0.0"
angle_theta.theta_function(x,y,z) = "{_tile_expression(spec, 'theta')}"
epsilon_0 = 8.85e-12
epsilonX_fe = {material.epsilon_x:.12g}
epsilonZ_fe = {material.epsilon_z:.12g}
epsilonX_fe_tphase = 40.0
epsilon_de = 10.0
epsilon_si = 11.7
alpha = {material.alpha:.12g}
beta = {material.beta:.12g}
gamma = {material.gamma:.12g}
BigGamma = {material.kinetic_coefficient:.12g}
g11 = {material.g11:.12g}
g44 = {material.g44:.12g}
g44_p = {material.g44_p:.12g}
g12 = {material.g12:.12g}
alpha_12 = {material.alpha_12:.12g}
alpha_112 = {material.alpha_112:.12g}
alpha_123 = {material.alpha_123:.12g}
"""


def prepare_run(
    run_id: str,
    parameters: FerroXExperimentSpec | dict[str, Any],
    runs_root: Path | None = None,
) -> dict[str, str]:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("invalid_run_id")
    spec = coerce_spec(parameters)
    root = (runs_root or RUNS_ROOT).resolve()
    run_dir = (root / run_id).resolve()
    if root != run_dir and root not in run_dir.parents:
        raise ValueError("run_directory_escape")
    run_dir.mkdir(parents=True, exist_ok=False)
    input_text = render_inputs(spec)
    input_path = run_dir / "inputs.hzo"
    input_path.write_text(input_text, encoding="utf-8")
    payload = spec.model_dump(mode="json")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    parameter_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    (run_dir / "experiment.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    state = {
        "run_id": run_id,
        "status": "prepared",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "parameter_sha256": parameter_hash,
        "scientific_status": spec.scientific_status,
        "kg_writeback": False,
        "ferrox_commit": FERROX_COMMIT,
        "amrex_version": AMREX_VERSION,
        "parameters": payload,
    }
    state_path = run_dir / "run.json"
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return {"run_dir": str(run_dir), "input_path": str(input_path), "state_path": str(state_path)}


def launch_run(
    run: dict[str, str],
    config: FerroXConfig | None = None,
    popen: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
) -> dict[str, Any]:
    config = config or FerroXConfig.from_env()
    state_path = Path(run["state_path"])
    state = json.loads(state_path.read_text(encoding="utf-8"))
    spec = FerroXExperimentSpec.model_validate(state["parameters"])
    wsl_input = windows_to_wsl_path(run["input_path"], config=config)
    command = build_command(config, wsl_input, spec.mpi_ranks, spec.omp_threads)
    run_dir = Path(run["run_dir"])
    stdout = (run_dir / "stdout.log").open("ab")
    try:
        process = popen(command, cwd=run_dir, stdout=stdout, stderr=subprocess.STDOUT)
    except Exception:
        stdout.close()
        state.update({"status": "failed_to_start", "finished_at": datetime.now(timezone.utc).isoformat()})
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        raise
    state.update({"status": "running", "pid": process.pid, "command": command})
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def read_run_state(run_dir: str | Path) -> dict[str, Any]:
    return json.loads((Path(run_dir) / "run.json").read_text(encoding="utf-8"))


def stop_run(
    run_dir: str | Path,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    state_path = Path(run_dir) / "run.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    pid = int(state.get("pid") or 0)
    if pid <= 0 or state.get("status") != "running":
        return False
    result = runner(
        ["taskkill.exe", "/PID", str(pid), "/T"], check=False, capture_output=True, text=True
    )
    if result.returncode != 0:
        return False
    state.update({"status": "stopped", "stopped_at": datetime.now(timezone.utc).isoformat()})
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return True


def create_screening_specs(base: FerroXExperimentSpec) -> list[FerroXExperimentSpec]:
    specs: list[FerroXExperimentSpec] = []
    for fraction in (0.0, 0.1, 0.25, 0.4):
        for spread in (0.0, 15.0, 30.0):
            payload = base.model_dump()
            payload["preset"] = "phase_orientation_screen"
            payload["name"] = f"HZO t={fraction:.0%} orientation={spread:.0f}deg"
            payload["microstructure"].update(
                {"tetragonal_fraction": fraction, "orientation_spread_deg": spread}
            )
            specs.append(FerroXExperimentSpec.model_validate(payload))
    return specs


def create_replicate_specs(
    base: FerroXExperimentSpec, seeds: Sequence[int] = (1, 2, 3)
) -> list[FerroXExperimentSpec]:
    specs: list[FerroXExperimentSpec] = []
    for seed in seeds:
        payload = base.model_dump()
        payload["microstructure"]["random_seed"] = int(seed)
        payload["name"] = f"{base.name} seed={seed}"
        specs.append(FerroXExperimentSpec.model_validate(payload))
    return specs


def create_grid_convergence_specs(base: FerroXExperimentSpec) -> list[FerroXExperimentSpec]:
    specs: list[FerroXExperimentSpec] = []
    for cell_size in (1.0, 0.5):
        payload = base.model_dump()
        payload["grid"]["cell_size_nm"] = cell_size
        payload["name"] = f"{base.name} grid={cell_size:.1f}nm"
        specs.append(FerroXExperimentSpec.model_validate(payload))
    return specs


def load_curve_csv(path: str | Path) -> list[dict[str, float]]:
    allowed = {"step", "time", "voltage_v", "polarization_uc_cm2", "electric_field_mv_cm", "energy"}
    rows: list[dict[str, float]] = []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            rows.append({k: float(v) for k, v in raw.items() if k in allowed and v not in {None, ""}})
    return rows
