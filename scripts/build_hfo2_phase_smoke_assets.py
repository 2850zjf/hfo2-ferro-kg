from __future__ import annotations

import math
import re
import zipfile
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_CSV = (
    PROJECT_ROOT
    / "data"
    / "computation"
    / "tefs_hfo2_phase_smoke_20260622"
    / "runs"
    / "hfo2_phase_smoke"
    / "phase_energy_summary.csv"
)
NORMALIZED_CSV = (
    PROJECT_ROOT
    / "data"
    / "computation"
    / "tefs_hfo2_phase_smoke_20260622"
    / "hfo2_phase_smoke_results_normalized.csv"
)
DESCRIPTOR_CSV = PROJECT_ROOT / "data" / "computation" / "computed_descriptors.csv"
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "phase_smoke"
VESTA_DIR = OUTPUT_DIR / "vesta_structures"
VESTA_ZIP = OUTPUT_DIR / "hfo2_phase_vesta_cif_bundle.zip"
RUN_ROOT = (
    PROJECT_ROOT
    / "data"
    / "computation"
    / "tefs_hfo2_phase_smoke_20260622"
    / "runs"
    / "hfo2_phase_smoke"
)

PHASE_ORDER = ["monoclinic", "orthorhombic", "tetragonal", "cubic"]
PHASE_LABELS = {
    "monoclinic": "monoclinic\nnon-polar",
    "orthorhombic": "orthorhombic\nferroelectric",
    "tetragonal": "tetragonal\nnon-polar",
    "cubic": "cubic\nhigh-symmetry",
}
PHASE_COLORS = {
    "monoclinic": "#A1A0A5",
    "orthorhombic": "#8BACDD",
    "tetragonal": "#B5D6EA",
    "cubic": "#B2A4CF",
}
FIGURE_FONT = "Times New Roman"

FORTRAN_FLOAT = r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[Ee][-+]?\d+)?"


def _style() -> None:
    plt.rcParams.update(
        {
            "figure.dpi": 150,
            "savefig.dpi": 300,
            "font.family": FIGURE_FONT,
            "font.serif": [FIGURE_FONT, "Times", "DejaVu Serif"],
            "font.size": 9,
            "axes.titlesize": 11,
            "axes.labelsize": 9,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.edgecolor": "#C5C3C6",
            "axes.labelcolor": "#2A2D39",
            "xtick.color": "#4D5260",
            "ytick.color": "#4D5260",
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )


def build_tables() -> pd.DataFrame:
    if not SOURCE_CSV.exists():
        raise FileNotFoundError(f"Missing TEFS phase summary: {SOURCE_CSV}")

    raw = pd.read_csv(SOURCE_CSV)
    raw["phase"] = raw["phase"].astype(str).str.strip()
    raw["phase_order"] = raw["phase"].map({phase: i for i, phase in enumerate(PHASE_ORDER)})
    raw = raw.sort_values("phase_order").drop(columns=["phase_order"])

    raw["n_formula_units"] = pd.to_numeric(raw["hfo2_units"], errors="coerce")
    raw["total_energy_eV"] = pd.to_numeric(raw["total_energy_eV"], errors="coerce")
    raw["energy_eV_per_fu"] = raw["total_energy_eV"] / raw["n_formula_units"]
    min_energy = raw["energy_eV_per_fu"].min()
    raw["relative_energy_meV_per_fu"] = (raw["energy_eV_per_fu"] - min_energy) * 1000.0
    raw["relative_energy_meV_per_fu"] = raw["relative_energy_meV_per_fu"].round(1)
    raw["energy_eV_per_fu"] = raw["energy_eV_per_fu"].round(6)
    raw["quality_status"] = raw["status"].map({"converged": "ok"}).fillna("needs_review")
    raw["job_id"] = "hfo2_phase_smoke_tefs_20260622"
    raw["engine"] = "VASP 6.3.0"
    raw["platform"] = "TEFS Cloud / Tencent Cloud"
    raw["method"] = "PBE PAW, Hf_pv + O, relax + static"
    raw["result_scope"] = "HfO2 polymorph phase-stability smoke test"
    raw["claim_boundary"] = (
        "Environment and workflow validation only; not a final publication-grade DFT benchmark."
    )

    normalized = raw[
        [
            "job_id",
            "phase",
            "atoms",
            "n_formula_units",
            "total_energy_eV",
            "energy_eV_per_fu",
            "relative_energy_meV_per_fu",
            "quality_status",
            "engine",
            "platform",
            "method",
            "result_scope",
            "claim_boundary",
        ]
    ].copy()
    NORMALIZED_CSV.parent.mkdir(parents=True, exist_ok=True)
    normalized.to_csv(NORMALIZED_CSV, index=False, encoding="utf-8-sig")

    descriptors = normalized[
        [
            "job_id",
            "phase",
            "energy_eV_per_fu",
            "relative_energy_meV_per_fu",
            "quality_status",
            "engine",
            "platform",
            "method",
            "claim_boundary",
        ]
    ].copy()
    descriptors.insert(0, "descriptor_id", [f"hfo2_phase_delta_{phase}" for phase in descriptors["phase"]])
    descriptors.insert(2, "formula", "HfO2")
    descriptors.insert(4, "descriptor_name", "phase_energy_delta_meV_per_fu")

    if DESCRIPTOR_CSV.exists():
        old = pd.read_csv(DESCRIPTOR_CSV)
        combined = pd.concat([old, descriptors], ignore_index=True)
        combined = combined.drop_duplicates(subset=["descriptor_id"], keep="last")
    else:
        combined = descriptors
    DESCRIPTOR_CSV.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(DESCRIPTOR_CSV, index=False, encoding="utf-8-sig")
    return normalized


def _lattice_metrics(vectors: np.ndarray) -> dict[str, float]:
    a_vec, b_vec, c_vec = vectors
    lengths = [float(np.linalg.norm(v)) for v in vectors]

    def angle(u: np.ndarray, v: np.ndarray) -> float:
        denom = np.linalg.norm(u) * np.linalg.norm(v)
        cosang = float(np.dot(u, v) / denom)
        cosang = max(min(cosang, 1.0), -1.0)
        return math.degrees(math.acos(cosang))

    return {
        "a_A": lengths[0],
        "b_A": lengths[1],
        "c_A": lengths[2],
        "alpha_deg": angle(b_vec, c_vec),
        "beta_deg": angle(a_vec, c_vec),
        "gamma_deg": angle(a_vec, b_vec),
        "volume_A3": abs(float(np.linalg.det(vectors))),
    }


def _parse_poscar(path: Path) -> dict[str, object]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    scale = float(lines[1].split()[0])
    vectors = np.array([[float(x) for x in lines[i].split()[:3]] for i in range(2, 5)], dtype=float) * scale
    species = lines[5].split()
    counts = [int(float(x)) for x in lines[6].split()]
    composition = dict(zip(species, counts))
    metrics = _lattice_metrics(vectors)
    metrics["atoms"] = int(sum(counts))
    metrics["hfo2_units"] = int(composition.get("Hf", 0))
    metrics["composition"] = " ".join(f"{element}{count}" for element, count in composition.items())
    return metrics


def _read_poscar_structure(path: Path) -> dict[str, object]:
    lines = [line.rstrip() for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    scale = float(lines[1].split()[0])
    vectors = np.array([[float(x) for x in lines[i].split()[:3]] for i in range(2, 5)], dtype=float) * scale
    species = lines[5].split()
    counts = [int(float(x)) for x in lines[6].split()]
    coord_mode_index = 7
    mode = lines[coord_mode_index].strip().lower()
    if mode.startswith("s"):
        coord_mode_index += 1
        mode = lines[coord_mode_index].strip().lower()
    coord_start = coord_mode_index + 1
    n_atoms = sum(counts)
    coords = np.array(
        [[float(x) for x in lines[coord_start + i].split()[:3]] for i in range(n_atoms)],
        dtype=float,
    )
    if mode.startswith("d"):
        frac = coords
        cart = coords @ vectors
    else:
        cart = coords * scale
        frac = cart @ np.linalg.inv(vectors)
    atom_species: list[str] = []
    for element, count in zip(species, counts):
        atom_species.extend([element] * count)
    return {
        "vectors": vectors,
        "frac": frac,
        "cart": cart,
        "species": atom_species,
        "counts": dict(zip(species, counts)),
    }


def _parse_outcar(path: Path) -> dict[str, float | None]:
    text = path.read_text(encoding="utf-8", errors="replace")
    out: dict[str, float | None] = {
        "outcar_volume_A3": None,
        "external_pressure_kB": None,
        "max_force_eV_A": None,
        "fermi_eV": None,
    }
    volume_matches = re.findall(r"volume of cell\s*:\s*(" + FORTRAN_FLOAT + r")", text)
    if volume_matches:
        out["outcar_volume_A3"] = float(volume_matches[-1])
    pressure_matches = re.findall(r"external pressure\s*=\s*(" + FORTRAN_FLOAT + r")\s*kB", text)
    if pressure_matches:
        out["external_pressure_kB"] = float(pressure_matches[-1])
    fermi_matches = re.findall(r"E-fermi\s*:\s*(" + FORTRAN_FLOAT + r")", text)
    if fermi_matches:
        out["fermi_eV"] = float(fermi_matches[-1])

    marker = "POSITION                                       TOTAL-FORCE"
    if marker in text:
        block = text.rsplit(marker, 1)[-1]
        forces: list[float] = []
        for line in block.splitlines():
            parts = line.split()
            if len(parts) == 6:
                try:
                    fx, fy, fz = (float(parts[3]), float(parts[4]), float(parts[5]))
                except ValueError:
                    continue
                forces.append(math.sqrt(fx * fx + fy * fy + fz * fz))
        if forces:
            out["max_force_eV_A"] = max(forces)
    return out


def _parse_relax_output(path: Path, phase: str, formula_units: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    pattern = re.compile(r"^\s*(\d+)\s+F=\s*(" + FORTRAN_FLOAT + r")\s+E0=\s*(" + FORTRAN_FLOAT + r")", re.MULTILINE)
    for match in pattern.finditer(path.read_text(encoding="utf-8", errors="replace")):
        step = int(match.group(1))
        energy = float(match.group(2))
        rows.append(
            {
                "phase": phase,
                "ionic_step": step,
                "free_energy_eV": energy,
                "free_energy_eV_per_HfO2": energy / formula_units,
            }
        )
    return rows


def build_real_result_tables(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    structure_rows: list[dict[str, object]] = []
    convergence_rows: list[dict[str, object]] = []
    for _, row in df.iterrows():
        phase = str(row["phase"])
        phase_dir = RUN_ROOT / phase
        initial = _parse_poscar(phase_dir / "POSCAR")
        final = _parse_poscar(phase_dir / "CONTCAR")
        outcar = _parse_outcar(phase_dir / "OUTCAR")
        formula_units = int(row["n_formula_units"])
        structure_rows.append(
            {
                "phase": phase,
                "composition": final["composition"],
                "atoms": int(final["atoms"]),
                "hfo2_units": formula_units,
                "total_energy_eV": float(row["total_energy_eV"]),
                "energy_eV_per_HfO2": float(row["energy_eV_per_fu"]),
                "relative_energy_meV_per_HfO2": float(row["relative_energy_meV_per_fu"]),
                "a_A": final["a_A"],
                "b_A": final["b_A"],
                "c_A": final["c_A"],
                "alpha_deg": final["alpha_deg"],
                "beta_deg": final["beta_deg"],
                "gamma_deg": final["gamma_deg"],
                "volume_A3": final["volume_A3"],
                "volume_A3_per_HfO2": final["volume_A3"] / formula_units,
                "initial_volume_A3": initial["volume_A3"],
                "volume_change_percent": (final["volume_A3"] - initial["volume_A3"]) / initial["volume_A3"] * 100.0,
                "external_pressure_kB": outcar["external_pressure_kB"],
                "max_force_eV_A": outcar["max_force_eV_A"],
                "fermi_eV": outcar["fermi_eV"],
                "quality_status": row["quality_status"],
            }
        )
        convergence_rows.extend(_parse_relax_output(phase_dir / "relax.output", phase, formula_units))

    structure = pd.DataFrame(structure_rows)
    convergence = pd.DataFrame(convergence_rows)
    if not convergence.empty:
        final_by_phase = convergence.groupby("phase")["free_energy_eV_per_HfO2"].last()
        convergence["delta_to_final_meV_per_HfO2"] = convergence.apply(
            lambda x: (float(x["free_energy_eV_per_HfO2"]) - float(final_by_phase[x["phase"]])) * 1000.0,
            axis=1,
        )

    structure_path = RUN_ROOT.parent.parent / "hfo2_phase_smoke_structural_results.csv"
    convergence_path = RUN_ROOT.parent.parent / "hfo2_phase_smoke_relax_convergence.csv"
    structure.to_csv(structure_path, index=False, encoding="utf-8-sig")
    convergence.to_csv(convergence_path, index=False, encoding="utf-8-sig")
    return structure, convergence


def save_figures(df: pd.DataFrame) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _style()

    colors = [PHASE_COLORS[phase] for phase in df["phase"]]
    labels = [PHASE_LABELS[phase] for phase in df["phase"]]

    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    ax.bar(
        labels,
        df["relative_energy_meV_per_fu"],
        color=colors,
        edgecolor="#5E6470",
        linewidth=0.8,
        width=0.62,
    )
    ax.set_ylabel("Relative energy (meV / HfO2)")
    ax.set_title("HfO2 polymorph stability smoke test")
    ax.set_ylim(0, max(df["relative_energy_meV_per_fu"]) * 1.18)
    ax.yaxis.grid(True, color="#D7DCEA", linewidth=0.8)
    ax.set_axisbelow(True)
    for i, value in enumerate(df["relative_energy_meV_per_fu"]):
        ax.text(i, value + 8, f"{value:.1f}", ha="center", va="bottom", color="#2A2D39", fontweight="bold")
    ax.text(
        0.01,
        -0.23,
        "Source: TEFS VASP smoke test, PBE PAW Hf_pv/O. Values are workflow validation descriptors, not final DFT benchmarks.",
        transform=ax.transAxes,
        ha="left",
        va="top",
        color="#6A7080",
        fontsize=7.5,
    )
    fig.tight_layout()
    for ext in ["png", "pdf", "svg"]:
        fig.savefig(OUTPUT_DIR / f"hfo2_phase_relative_energy.{ext}", bbox_inches="tight")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    x = range(len(df))
    ax.plot(x, df["relative_energy_meV_per_fu"], color="#64748B", linewidth=1.2, alpha=0.8)
    ax.scatter(x, df["relative_energy_meV_per_fu"], s=180, c=colors, edgecolor="#4D5260", linewidth=0.9, zorder=3)
    for i, row in df.iterrows():
        ax.vlines(i, 0, row["relative_energy_meV_per_fu"], color="#C1D1E4", linewidth=2, alpha=0.75)
        ax.text(
            i,
            row["relative_energy_meV_per_fu"] + 11,
            f"{row['relative_energy_meV_per_fu']:.1f}",
            ha="center",
            va="bottom",
            color="#2A2D39",
            fontweight="bold",
            fontsize=8,
        )
    ax.set_xticks(list(x), labels)
    ax.set_ylabel("Relative energy (meV / HfO2)")
    ax.set_title("Energy landscape from monoclinic baseline")
    ax.set_ylim(0, max(df["relative_energy_meV_per_fu"]) * 1.2)
    ax.yaxis.grid(True, color="#E2E4EF", linewidth=0.8)
    ax.set_axisbelow(True)
    fig.tight_layout()
    for ext in ["png", "pdf", "svg"]:
        fig.savefig(OUTPUT_DIR / f"hfo2_phase_energy_landscape.{ext}", bbox_inches="tight")
    plt.close(fig)

    report = OUTPUT_DIR / "hfo2_phase_smoke_summary.md"
    rows = [
        "# HfO2 Phase-Stability Smoke Test",
        "",
        "This is the first TEFS/VASP computation validation result imported into HfO2-FerroKG.",
        "",
        "## Result",
        "",
        "| phase | eV/HfO2 | relative meV/HfO2 | status |",
        "|---|---:|---:|---|",
    ]
    for _, row in df.iterrows():
        rows.append(
            f"| {row['phase']} | {row['energy_eV_per_fu']:.6f} | {row['relative_energy_meV_per_fu']:.1f} | {row['quality_status']} |"
        )
    rows.extend(
        [
            "",
            "## Interpretation",
            "",
            "- The workflow reproduces the expected qualitative stability order for bulk HfO2: monoclinic is lowest, orthorhombic is metastable, tetragonal/cubic are higher.",
            "- This validates the environment, packaging, TEFS submission, result collection, and descriptor writeback path.",
            "- The result is a smoke test. Publication-grade DFT still needs convergence checks, symmetry/phase-retention checks, and controlled HZO/dopant/defect models.",
        ]
    )
    report.write_text("\n".join(rows) + "\n", encoding="utf-8")


def save_real_result_figures(structure: pd.DataFrame, convergence: pd.DataFrame) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _style()

    ordered = structure.set_index("phase").loc[PHASE_ORDER].reset_index()
    colors = [PHASE_COLORS[phase] for phase in ordered["phase"]]
    labels = [PHASE_LABELS[phase] for phase in ordered["phase"]]

    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    ax.bar(
        labels,
        ordered["volume_A3_per_HfO2"],
        color=colors,
        edgecolor="#5E6470",
        linewidth=0.8,
        width=0.62,
    )
    ax.set_ylabel("Final volume (A3 / HfO2)")
    ax.set_title("Final relaxed cell volume from CONTCAR")
    ax.yaxis.grid(True, color="#DDE3EE", linewidth=0.8)
    ax.set_axisbelow(True)
    ymin = max(0, ordered["volume_A3_per_HfO2"].min() - 3)
    ymax = ordered["volume_A3_per_HfO2"].max() + 3
    ax.set_ylim(ymin, ymax)
    for i, value in enumerate(ordered["volume_A3_per_HfO2"]):
        ax.text(i, value + 0.35, f"{value:.2f}", ha="center", va="bottom", color="#2A2D39", fontweight="bold")
    fig.tight_layout()
    for ext in ["png", "pdf", "svg"]:
        fig.savefig(OUTPUT_DIR / f"hfo2_phase_final_volume.{ext}", bbox_inches="tight")
    plt.close(fig)

    if not convergence.empty:
        fig, ax = plt.subplots(figsize=(7.2, 4.2))
        for phase in PHASE_ORDER:
            subset = convergence[convergence["phase"] == phase].copy()
            if subset.empty:
                continue
            ax.plot(
                subset["ionic_step"],
                subset["free_energy_eV_per_HfO2"],
                marker="o",
                markersize=4,
                linewidth=1.8,
                color=PHASE_COLORS[phase],
                label=phase,
            )
        ax.set_xlabel("Ionic relaxation step")
        ax.set_ylabel("Free energy (eV / HfO2)")
        ax.set_title("VASP ionic relaxation traces from relax.output")
        ax.yaxis.grid(True, color="#DDE3EE", linewidth=0.8)
        ax.set_axisbelow(True)
        ax.legend(frameon=False, ncols=2, loc="best")
        fig.tight_layout()
        for ext in ["png", "pdf", "svg"]:
            fig.savefig(OUTPUT_DIR / f"hfo2_phase_relax_convergence.{ext}", bbox_inches="tight")
        plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    ax.scatter(
        ordered["volume_A3_per_HfO2"],
        ordered["relative_energy_meV_per_HfO2"],
        s=180,
        c=colors,
        edgecolor="#4D5260",
        linewidth=0.9,
        zorder=3,
    )
    for _, row in ordered.iterrows():
        ax.text(
            row["volume_A3_per_HfO2"] + 0.08,
            row["relative_energy_meV_per_HfO2"] + 6,
            str(row["phase"]),
            fontsize=8,
            color="#2A2D39",
            fontweight="bold",
        )
    ax.set_xlabel("Final volume (A3 / HfO2)")
    ax.set_ylabel("Relative energy (meV / HfO2)")
    ax.set_title("Energy-volume descriptors from TEFS/VASP")
    ax.grid(True, color="#DDE3EE", linewidth=0.8)
    ax.set_axisbelow(True)
    fig.tight_layout()
    for ext in ["png", "pdf", "svg"]:
        fig.savefig(OUTPUT_DIR / f"hfo2_phase_energy_volume.{ext}", bbox_inches="tight")
    plt.close(fig)

    report = OUTPUT_DIR / "hfo2_phase_real_vasp_results.md"
    lines = [
        "# Real VASP Outputs Parsed from TEFS Smoke Test",
        "",
        "## Structural and electronic outputs",
        "",
        "| phase | a (A) | b (A) | c (A) | beta (deg) | V/HfO2 (A3) | max force (eV/A) | pressure (kB) | E-fermi (eV) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in ordered.iterrows():
        lines.append(
            "| {phase} | {a:.4f} | {b:.4f} | {c:.4f} | {beta:.2f} | {vol:.2f} | {force:.4f} | {pressure:.2f} | {fermi:.4f} |".format(
                phase=row["phase"],
                a=row["a_A"],
                b=row["b_A"],
                c=row["c_A"],
                beta=row["beta_deg"],
                vol=row["volume_A3_per_HfO2"],
                force=row["max_force_eV_A"],
                pressure=row["external_pressure_kB"],
                fermi=row["fermi_eV"],
            )
        )
    lines.extend(
        [
            "",
            "These values come from CONTCAR and OUTCAR, not from manually typed summary statistics.",
            "The first publication-grade upgrade should add symmetry/phase-retention checks and convergence tests.",
        ]
    )
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")


def save_structure_figures() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _style()
    atom_style = {
        "Hf": {"color": "#8D9ECF", "edge": "#4D5260", "size": 170},
        "O": {"color": "#DCE6EE", "edge": "#7A8392", "size": 62},
    }

    def draw_cell(ax: plt.Axes, vectors: np.ndarray, dims: tuple[int, int]) -> None:
        corners = []
        for i in [0, 1]:
            for j in [0, 1]:
                for k in [0, 1]:
                    corners.append(i * vectors[0] + j * vectors[1] + k * vectors[2])
        corners = np.array(corners)
        corner_index = {(i, j, k): idx for idx, (i, j, k) in enumerate((i, j, k) for i in [0, 1] for j in [0, 1] for k in [0, 1])}
        for i in [0, 1]:
            for j in [0, 1]:
                for k in [0, 1]:
                    start = corners[corner_index[(i, j, k)]]
                    for delta in [(1, 0, 0), (0, 1, 0), (0, 0, 1)]:
                        end_key = (i + delta[0], j + delta[1], k + delta[2])
                        if all(value <= 1 for value in end_key):
                            end = corners[corner_index[end_key]]
                            ax.plot(
                                [start[dims[0]], end[dims[0]]],
                                [start[dims[1]], end[dims[1]]],
                                color="#AEB7C4",
                                linewidth=0.8,
                                alpha=0.8,
                                zorder=1,
                            )

    projections = [("xy", (0, 1), "xy projection"), ("xz", (0, 2), "xz projection")]
    fig, axes = plt.subplots(2, 4, figsize=(10.8, 5.4))
    for col, phase in enumerate(PHASE_ORDER):
        data = _read_poscar_structure(RUN_ROOT / phase / "CONTCAR")
        cart = data["cart"]
        vectors = data["vectors"]
        species = data["species"]
        for row, (_, dims, row_title) in enumerate(projections):
            ax = axes[row, col]
            draw_cell(ax, vectors, dims)
            for element in ["O", "Hf"]:
                idx = [i for i, atom in enumerate(species) if atom == element]
                if not idx:
                    continue
                style = atom_style[element]
                ax.scatter(
                    cart[idx, dims[0]],
                    cart[idx, dims[1]],
                    s=style["size"],
                    color=style["color"],
                    edgecolors=style["edge"],
                    linewidths=0.6,
                    alpha=0.96,
                    zorder=3 if element == "Hf" else 2,
                    label=element,
                )
            ax.set_aspect("equal", adjustable="box")
            ax.set_xticks([])
            ax.set_yticks([])
            ax.set_facecolor("#FFFFFF")
            for spine in ax.spines.values():
                spine.set_visible(True)
                spine.set_color("#D7DCEA")
                spine.set_linewidth(0.7)
            if row == 0:
                ax.set_title(phase, fontsize=10, fontweight="bold", color="#2A2D39", pad=8)
            if col == 0:
                ax.set_ylabel(row_title, fontsize=9, color="#5E6470")
    handles = [
        Line2D([0], [0], marker="o", color="none", markerfacecolor=atom_style["Hf"]["color"], markeredgecolor=atom_style["Hf"]["edge"], markersize=9, label="Hf"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor=atom_style["O"]["color"], markeredgecolor=atom_style["O"]["edge"], markersize=6, label="O"),
    ]
    fig.legend(handles=handles, loc="upper center", ncol=2, frameon=False, bbox_to_anchor=(0.5, 1.01))
    fig.suptitle("Relaxed HfO2 phase structures parsed from CONTCAR", y=1.06, fontsize=13, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    for ext in ["png", "pdf", "svg"]:
        fig.savefig(OUTPUT_DIR / f"hfo2_phase_relaxed_structures.{ext}", bbox_inches="tight")
    plt.close(fig)


def _cif_text(phase: str, structure: dict[str, object]) -> str:
    vectors = structure["vectors"]
    metrics = _lattice_metrics(vectors)
    species = structure["species"]
    frac = np.mod(structure["frac"], 1.0)
    counts = structure["counts"]
    formula = " ".join(f"{element}{count}" for element, count in counts.items())
    lines = [
        f"data_HfO2_{phase}",
        "_audit_creation_method 'HfO2-FerroKG TEFS VASP CONTCAR export'",
        f"_chemical_formula_sum '{formula}'",
        "_symmetry_space_group_name_H-M 'P 1'",
        "_symmetry_Int_Tables_number 1",
        f"_cell_length_a {metrics['a_A']:.8f}",
        f"_cell_length_b {metrics['b_A']:.8f}",
        f"_cell_length_c {metrics['c_A']:.8f}",
        f"_cell_angle_alpha {metrics['alpha_deg']:.8f}",
        f"_cell_angle_beta {metrics['beta_deg']:.8f}",
        f"_cell_angle_gamma {metrics['gamma_deg']:.8f}",
        "",
        "loop_",
        "_symmetry_equiv_pos_as_xyz",
        "'x, y, z'",
        "",
        "loop_",
        "_atom_site_label",
        "_atom_site_type_symbol",
        "_atom_site_fract_x",
        "_atom_site_fract_y",
        "_atom_site_fract_z",
        "_atom_site_occupancy",
    ]
    element_counter: dict[str, int] = {}
    for element, coord in zip(species, frac):
        element_counter[element] = element_counter.get(element, 0) + 1
        label = f"{element}{element_counter[element]}"
        lines.append(
            f"{label} {element} {coord[0]:.8f} {coord[1]:.8f} {coord[2]:.8f} 1.0"
        )
    return "\n".join(lines) + "\n"


def save_vesta_exports() -> None:
    VESTA_DIR.mkdir(parents=True, exist_ok=True)
    cif_paths: list[Path] = []
    vasp_paths: list[Path] = []
    for phase in PHASE_ORDER:
        structure = _read_poscar_structure(RUN_ROOT / phase / "CONTCAR")
        cif_path = VESTA_DIR / f"hfo2_{phase}_relaxed_CONTCAR.cif"
        cif_path.write_text(_cif_text(phase, structure), encoding="utf-8")
        cif_paths.append(cif_path)
        vasp_path = VESTA_DIR / f"hfo2_{phase}_relaxed_CONTCAR.vasp"
        vasp_path.write_text((RUN_ROOT / phase / "CONTCAR").read_text(encoding="utf-8"), encoding="utf-8")
        vasp_paths.append(vasp_path)

    readme = VESTA_DIR / "README_VESTA.txt"
    readme.write_text(
        "\n".join(
            [
                "HfO2-FerroKG VESTA structure bundle",
                "",
                "Files in this folder are CIF exports generated from the relaxed CONTCAR files in the TEFS/VASP smoke test.",
                "Open each .vasp file with VESTA to inspect the monoclinic, orthorhombic, tetragonal, and cubic HfO2 structures.",
                "CIF files are also included for cross-software compatibility. VASP/POSCAR format is the safer first choice in VESTA.",
                "",
                "Suggested VESTA use:",
                "1. File -> Open -> select one .vasp file.",
                "2. Style -> Boundary to show one unit cell or a 2x2x2 supercell.",
                "3. Edit -> Bonds to add Hf-O bonds if desired.",
                "4. File -> Export Raster Image for a high-resolution figure.",
                "",
                "Do not run 'VESTA -h'. VESTA treats '-h' as a file path and shows an 'Invalid data' dialog.",
                "",
                "Safety note: this bundle contains only CIF structure files and no POTCAR, WAVECAR, CHG, CHGCAR, API key, or cloud credential.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with zipfile.ZipFile(VESTA_ZIP, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(readme, readme.name)
        for path in [*vasp_paths, *cif_paths]:
            archive.write(path, path.name)


def main() -> None:
    df = build_tables()
    structure, convergence = build_real_result_tables(df)
    save_figures(df)
    save_real_result_figures(structure, convergence)
    save_structure_figures()
    save_vesta_exports()
    print(f"normalized_csv={NORMALIZED_CSV}")
    print(f"descriptor_csv={DESCRIPTOR_CSV}")
    print(f"structural_csv={RUN_ROOT.parent.parent / 'hfo2_phase_smoke_structural_results.csv'}")
    print(f"convergence_csv={RUN_ROOT.parent.parent / 'hfo2_phase_smoke_relax_convergence.csv'}")
    print(f"vesta_zip={VESTA_ZIP}")
    print(f"output_dir={OUTPUT_DIR}")


if __name__ == "__main__":
    main()
