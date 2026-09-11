from __future__ import annotations

import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

from build_hfo2_phase_smoke_assets import PHASE_ORDER, PROJECT_ROOT, RUN_ROOT, _lattice_metrics, _read_poscar_structure


OUT_DIR = PROJECT_ROOT / "outputs" / "phase_smoke" / "publication"
REPORT_CUT_DIR = PROJECT_ROOT / "outputs" / "phase_smoke" / "report_cuts"
STRUCTURAL_CSV = (
    PROJECT_ROOT
    / "data"
    / "computation"
    / "tefs_hfo2_phase_smoke_20260622"
    / "hfo2_phase_smoke_structural_results.csv"
)
CONVERGENCE_CSV = (
    PROJECT_ROOT
    / "data"
    / "computation"
    / "tefs_hfo2_phase_smoke_20260622"
    / "hfo2_phase_smoke_relax_convergence.csv"
)

PHASE_SHORT = {
    "monoclinic": "m-HfO$_2$",
    "orthorhombic": "o-HfO$_2$",
    "tetragonal": "t-HfO$_2$",
    "cubic": "c-HfO$_2$",
}
PHASE_TICK = {
    "monoclinic": "m",
    "orthorhombic": "o",
    "tetragonal": "t",
    "cubic": "c",
}
ATOM_STYLE = {
    "Hf": {"color": "#4C72B0", "edge": "#243B5A", "size": 56},
    "O": {"color": "#E8B07D", "edge": "#8A5A44", "size": 20},
}
BOND_COLOR = "#B8B8B8"
TOP_JOURNAL_PHASE_COLORS = {
    "monoclinic": "#7A7A7A",
    "orthorhombic": "#4C72B0",
    "tetragonal": "#55A3B1",
    "cubic": "#C98A4A",
}


def set_publication_style() -> None:
    plt.rcParams.update(
        {
            "figure.dpi": 160,
            "savefig.dpi": 600,
            "font.family": "Times New Roman",
            "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
            "mathtext.fontset": "stix",
            "axes.linewidth": 0.6,
            "axes.edgecolor": "#4B5563",
            "axes.labelcolor": "#1F2937",
            "xtick.color": "#374151",
            "ytick.color": "#374151",
            "xtick.major.width": 0.55,
            "ytick.major.width": 0.55,
            "xtick.major.size": 2.8,
            "ytick.major.size": 2.8,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "none",
        }
    )


def _cell_edges(vectors: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
    corners: dict[tuple[int, int, int], np.ndarray] = {}
    for i in (0, 1):
        for j in (0, 1):
            for k in (0, 1):
                corners[(i, j, k)] = i * vectors[0] + j * vectors[1] + k * vectors[2]
    edges: list[tuple[np.ndarray, np.ndarray]] = []
    for i in (0, 1):
        for j in (0, 1):
            for k in (0, 1):
                start = corners[(i, j, k)]
                for di, dj, dk in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
                    end_key = (i + di, j + dj, k + dk)
                    if all(value <= 1 for value in end_key):
                        edges.append((start, corners[end_key]))
    return edges


def _set_3d_equal_bounds(ax: plt.Axes, vectors: np.ndarray) -> None:
    corners = np.array(
        [
            i * vectors[0] + j * vectors[1] + k * vectors[2]
            for i in (0, 1)
            for j in (0, 1)
            for k in (0, 1)
        ]
    )
    mins = corners.min(axis=0)
    maxs = corners.max(axis=0)
    centers = (mins + maxs) / 2.0
    span = float((maxs - mins).max()) * 0.56
    ax.set_xlim(centers[0] - span, centers[0] + span)
    ax.set_ylim(centers[1] - span, centers[1] + span)
    ax.set_zlim(centers[2] - span, centers[2] + span)
    ax.set_box_aspect((1, 1, 1))


def draw_phase_structure(ax: plt.Axes, phase: str) -> None:
    structure = _read_poscar_structure(RUN_ROOT / phase / "CONTCAR")
    vectors = structure["vectors"]
    frac = structure["frac"]
    cart = structure["cart"]
    species = structure["species"]

    for start, end in _cell_edges(vectors):
        ax.plot(
            [start[0], end[0]],
            [start[1], end[1]],
            [start[2], end[2]],
            color="#B8C2D0",
            linewidth=0.55,
            alpha=0.95,
            zorder=1,
        )

    hf_indices = [i for i, element in enumerate(species) if element == "Hf"]
    o_indices = [i for i, element in enumerate(species) if element == "O"]
    for i in hf_indices:
        for j in o_indices:
            diff = frac[j] - frac[i]
            diff -= np.round(diff)
            displacement = diff @ vectors
            distance = float(np.linalg.norm(displacement))
            if distance <= 2.35:
                start = cart[i]
                end = start + displacement
                ax.plot(
                    [start[0], end[0]],
                    [start[1], end[1]],
                    [start[2], end[2]],
                    color=BOND_COLOR,
                    linewidth=0.55,
                    alpha=0.78,
                    zorder=2,
                )

    for element in ("O", "Hf"):
        indices = [i for i, atom in enumerate(species) if atom == element]
        style = ATOM_STYLE[element]
        ax.scatter(
            cart[indices, 0],
            cart[indices, 1],
            cart[indices, 2],
            s=style["size"],
            color=style["color"],
            edgecolor=style["edge"],
            linewidth=0.35,
            depthshade=False,
            zorder=4 if element == "Hf" else 3,
        )

    _set_3d_equal_bounds(ax, vectors)
    ax.view_init(elev=19, azim=-48)
    ax.set_axis_off()
    ax.set_title(PHASE_SHORT[phase], fontsize=8, pad=0)


def _panel_label(ax: plt.Axes, label: str, x: float = -0.12, y: float = 1.06) -> None:
    kwargs = {
        "transform": ax.transAxes,
        "fontsize": 10,
        "fontweight": "bold",
        "va": "top",
        "ha": "left",
    }
    if hasattr(ax, "text2D"):
        ax.text2D(x, y, label, **kwargs)
    else:
        ax.text(x, y, label, **kwargs)


def build_publication_figure() -> Path:
    set_publication_style()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    structure = pd.read_csv(STRUCTURAL_CSV).set_index("phase").loc[PHASE_ORDER].reset_index()
    convergence = pd.read_csv(CONVERGENCE_CSV)
    colors = [TOP_JOURNAL_PHASE_COLORS[phase] for phase in PHASE_ORDER]

    fig = plt.figure(figsize=(7.2, 5.7))
    gs = fig.add_gridspec(
        3,
        4,
        height_ratios=[1.28, 1.34, 1.58],
        left=0.075,
        right=0.985,
        top=0.965,
        bottom=0.085,
        wspace=0.55,
        hspace=0.42,
    )

    structure_axes: list[plt.Axes] = []
    for col, phase in enumerate(PHASE_ORDER):
        ax = fig.add_subplot(gs[0, col], projection="3d")
        draw_phase_structure(ax, phase)
        structure_axes.append(ax)
    _panel_label(structure_axes[0], "a", x=-0.18, y=1.08)
    structure_axes[0].text2D(
        0.05,
        -0.08,
        "Hf",
        transform=structure_axes[0].transAxes,
        color=ATOM_STYLE["Hf"]["edge"],
        fontsize=7,
    )
    structure_axes[0].text2D(
        0.26,
        -0.08,
        "O",
        transform=structure_axes[0].transAxes,
        color=ATOM_STYLE["O"]["edge"],
        fontsize=7,
    )

    ax_energy = fig.add_subplot(gs[1, 0:2])
    x = np.arange(len(PHASE_ORDER))
    relative = structure["relative_energy_meV_per_HfO2"].to_numpy(dtype=float)
    ax_energy.vlines(x, 0, relative, color="#B8C2D0", linewidth=0.9, zorder=1)
    ax_energy.scatter(x, relative, s=56, c=colors, edgecolor="#334155", linewidth=0.45, zorder=3)
    for xi, yi in zip(x, relative):
        ax_energy.text(xi, yi + 9, f"{yi:.1f}", ha="center", va="bottom", fontsize=7)
    ax_energy.set_xticks(x)
    ax_energy.set_xticklabels([PHASE_TICK[p] for p in PHASE_ORDER], fontsize=7)
    ax_energy.set_ylabel(r"$\Delta E$ (meV / HfO$_2$)", fontsize=8)
    ax_energy.set_xlabel("Phase", fontsize=8, labelpad=2)
    ax_energy.set_title("Relative phase energy", fontsize=8, pad=3)
    ax_energy.set_ylim(-8, max(relative) + 45)
    ax_energy.grid(axis="y", color="#E5E7EB", linewidth=0.45)
    ax_energy.set_axisbelow(True)
    _panel_label(ax_energy, "b")

    ax_ev = fig.add_subplot(gs[1, 2:4])
    volumes = structure["volume_A3_per_HfO2"].to_numpy(dtype=float)
    ax_ev.scatter(volumes, relative, s=62, c=colors, edgecolor="#334155", linewidth=0.45, zorder=3)
    offsets = {
        "monoclinic": (-0.17, 13),
        "orthorhombic": (0.06, 10),
        "tetragonal": (0.05, 12),
        "cubic": (0.05, -14),
    }
    for _, row in structure.iterrows():
        dx, dy = offsets[row["phase"]]
        ax_ev.text(
            float(row["volume_A3_per_HfO2"]) + dx,
            float(row["relative_energy_meV_per_HfO2"]) + dy,
            row["phase"][0],
            fontsize=7,
            fontweight="bold",
        )
    ax_ev.set_xlabel(r"Volume ($\AA^3$ / HfO$_2$)", fontsize=8)
    ax_ev.set_ylabel(r"$\Delta E$ (meV / HfO$_2$)", fontsize=8)
    ax_ev.set_title("Energy-volume descriptor", fontsize=8, pad=3)
    ax_ev.grid(color="#E5E7EB", linewidth=0.45)
    ax_ev.set_axisbelow(True)
    ax_ev.set_xlim(volumes.min() - 0.2, volumes.max() + 0.25)
    ax_ev.set_ylim(-12, max(relative) + 36)
    _panel_label(ax_ev, "c")

    ax_conv = fig.add_subplot(gs[2, :])
    line_styles = {
        "monoclinic": ("-", "o"),
        "orthorhombic": ("-", "s"),
        "tetragonal": ("--", "^"),
        "cubic": ("--", "D"),
    }
    for phase in PHASE_ORDER:
        subset = convergence[convergence["phase"] == phase].copy()
        residual = subset["delta_to_final_meV_per_HfO2"].abs().clip(lower=0.01)
        linestyle, marker = line_styles[phase]
        ax_conv.plot(
            subset["ionic_step"],
            residual,
            linestyle=linestyle,
            marker=marker,
            markersize=3.0,
            linewidth=0.95,
            color=TOP_JOURNAL_PHASE_COLORS[phase],
            markeredgecolor="#334155",
            markeredgewidth=0.25,
            label=PHASE_TICK[phase],
        )
    ax_conv.axhline(1.0, color="#94A3B8", linestyle=":", linewidth=0.8)
    ax_conv.text(0.98, 0.58, "1 meV / HfO$_2$", transform=ax_conv.transAxes, ha="right", fontsize=7, color="#64748B")
    ax_conv.set_yscale("log")
    ax_conv.set_xlabel("Ionic relaxation step", fontsize=8)
    ax_conv.set_ylabel(r"$|F_i-F_{final}|$ (meV / HfO$_2$)", fontsize=8)
    ax_conv.set_title("Ionic relaxation convergence", fontsize=8, pad=3)
    ax_conv.grid(axis="y", color="#E5E7EB", linewidth=0.45, which="major")
    ax_conv.grid(axis="y", color="#F1F5F9", linewidth=0.28, which="minor")
    ax_conv.legend(frameon=False, ncols=4, loc="upper right", fontsize=7, handlelength=2.0, title="Phase", title_fontsize=7)
    ax_conv.set_xlim(0.5, convergence["ionic_step"].max() + 0.8)
    _panel_label(ax_conv, "d", x=-0.055, y=1.1)

    for ax in [ax_energy, ax_ev, ax_conv]:
        ax.tick_params(labelsize=7)
        for spine in ax.spines.values():
            spine.set_linewidth(0.55)

    basename = OUT_DIR / "figure1_hfo2_phase_validation"
    for ext in ("png", "pdf", "svg"):
        fig.savefig(basename.with_suffix(f".{ext}"), bbox_inches="tight")

    fig_gray = fig
    fig_gray.savefig(OUT_DIR / "figure1_hfo2_phase_validation_preview.png", bbox_inches="tight")
    plt.close(fig_gray)

    png = plt.imread(OUT_DIR / "figure1_hfo2_phase_validation_preview.png")
    rgb = png[..., :3]
    gray = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    plt.imsave(OUT_DIR / "figure1_hfo2_phase_validation_grayscale.png", gray, cmap="gray")

    caption = OUT_DIR / "figure1_hfo2_phase_validation_caption.md"
    caption.write_text(
        "\n".join(
            [
                "# Figure 1. TEFS/VASP validation descriptors for HfO2 polymorphs",
                "",
                "**a**, Relaxed HfO2 polymorph structures parsed from CONTCAR files. Hf and O atoms are shown with low-saturation blue and pale-blue markers; Hf-O bonds are drawn using a 2.35 A cutoff for visual guidance.",
                "**b**, Relative phase energies normalized per HfO2 formula unit, using monoclinic HfO2 as the zero-energy reference.",
                "**c**, Energy-volume descriptor map derived from the final CONTCAR volumes and static energies.",
                "**d**, Ionic relaxation residuals extracted from relax.output, plotted relative to each phase's final relaxation energy.",
                "",
                "Source data: TEFS Cloud / VASP 6.3.0 smoke test, PBE PAW Hf_pv/O, relax + static. The figure validates the computational workflow and descriptor writeback path; publication-grade DFT conclusions still require convergence and phase-retention checks.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return basename.with_suffix(".pdf")


def _export(fig: plt.Figure, basename: Path) -> None:
    basename.parent.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "pdf", "svg"):
        fig.savefig(basename.with_suffix(f".{ext}"), bbox_inches="tight")
    plt.close(fig)


def build_report_cut_figures() -> None:
    set_publication_style()
    REPORT_CUT_DIR.mkdir(parents=True, exist_ok=True)

    structure = pd.read_csv(STRUCTURAL_CSV).set_index("phase").loc[PHASE_ORDER].reset_index()
    convergence = pd.read_csv(CONVERGENCE_CSV)
    colors = [TOP_JOURNAL_PHASE_COLORS[phase] for phase in PHASE_ORDER]

    fig = plt.figure(figsize=(7.2, 2.15))
    gs = fig.add_gridspec(1, 4, left=0.02, right=0.99, top=0.88, bottom=0.07, wspace=0.08)
    axes: list[plt.Axes] = []
    for col, phase in enumerate(PHASE_ORDER):
        ax = fig.add_subplot(gs[0, col], projection="3d")
        draw_phase_structure(ax, phase)
        axes.append(ax)
    axes[0].text2D(-0.09, 1.02, "a", transform=axes[0].transAxes, fontsize=10, fontweight="bold")
    axes[0].text2D(0.10, -0.04, "Hf", transform=axes[0].transAxes, fontsize=7, color=ATOM_STYLE["Hf"]["edge"])
    axes[0].text2D(0.29, -0.04, "O", transform=axes[0].transAxes, fontsize=7, color=ATOM_STYLE["O"]["edge"])
    fig.suptitle("Relaxed HfO$_2$ polymorph structures", fontsize=9, y=0.98)
    _export(fig, REPORT_CUT_DIR / "01_relaxed_structures")

    fig, axes2 = plt.subplots(1, 2, figsize=(7.2, 2.65), gridspec_kw={"wspace": 0.38})
    relative = structure["relative_energy_meV_per_HfO2"].to_numpy(dtype=float)
    x = np.arange(len(PHASE_ORDER))
    ax_energy = axes2[0]
    ax_energy.vlines(x, 0, relative, color="#B8C2D0", linewidth=0.9, zorder=1)
    ax_energy.scatter(x, relative, s=52, c=colors, edgecolor="#334155", linewidth=0.45, zorder=3)
    for xi, yi in zip(x, relative):
        ax_energy.text(xi, yi + 9, f"{yi:.1f}", ha="center", va="bottom", fontsize=7)
    ax_energy.set_xticks(x)
    ax_energy.set_xticklabels([PHASE_TICK[p] for p in PHASE_ORDER], fontsize=7)
    ax_energy.set_xlabel("Phase", fontsize=8, labelpad=2)
    ax_energy.set_ylabel(r"$\Delta E$ (meV / HfO$_2$)", fontsize=8)
    ax_energy.set_title("Relative phase energy", fontsize=8, pad=3)
    ax_energy.set_ylim(-8, max(relative) + 45)
    ax_energy.grid(axis="y", color="#E5E7EB", linewidth=0.45)
    ax_energy.set_axisbelow(True)
    _panel_label(ax_energy, "b")

    ax_ev = axes2[1]
    volumes = structure["volume_A3_per_HfO2"].to_numpy(dtype=float)
    ax_ev.scatter(volumes, relative, s=58, c=colors, edgecolor="#334155", linewidth=0.45, zorder=3)
    offsets = {
        "monoclinic": (-0.17, 13),
        "orthorhombic": (0.06, 10),
        "tetragonal": (0.05, 12),
        "cubic": (0.05, -14),
    }
    for _, row in structure.iterrows():
        dx, dy = offsets[row["phase"]]
        ax_ev.text(
            float(row["volume_A3_per_HfO2"]) + dx,
            float(row["relative_energy_meV_per_HfO2"]) + dy,
            row["phase"][0],
            fontsize=7,
            fontweight="bold",
        )
    ax_ev.set_xlabel(r"Volume ($\AA^3$ / HfO$_2$)", fontsize=8)
    ax_ev.set_ylabel(r"$\Delta E$ (meV / HfO$_2$)", fontsize=8)
    ax_ev.set_title("Energy-volume descriptor", fontsize=8, pad=3)
    ax_ev.grid(color="#E5E7EB", linewidth=0.45)
    ax_ev.set_axisbelow(True)
    ax_ev.set_xlim(volumes.min() - 0.2, volumes.max() + 0.25)
    ax_ev.set_ylim(-12, max(relative) + 36)
    _panel_label(ax_ev, "c")
    for ax in axes2:
        ax.tick_params(labelsize=7)
    _export(fig, REPORT_CUT_DIR / "02_phase_energy_descriptors")

    fig, ax_conv = plt.subplots(figsize=(7.2, 2.55))
    line_styles = {
        "monoclinic": ("-", "o"),
        "orthorhombic": ("-", "s"),
        "tetragonal": ("--", "^"),
        "cubic": ("--", "D"),
    }
    for phase in PHASE_ORDER:
        subset = convergence[convergence["phase"] == phase].copy()
        residual = subset["delta_to_final_meV_per_HfO2"].abs().clip(lower=0.01)
        linestyle, marker = line_styles[phase]
        ax_conv.plot(
            subset["ionic_step"],
            residual,
            linestyle=linestyle,
            marker=marker,
            markersize=3.0,
            linewidth=0.95,
            color=TOP_JOURNAL_PHASE_COLORS[phase],
            markeredgecolor="#334155",
            markeredgewidth=0.25,
            label=PHASE_TICK[phase],
        )
    ax_conv.axhline(1.0, color="#94A3B8", linestyle=":", linewidth=0.8)
    ax_conv.text(0.98, 0.58, "1 meV / HfO$_2$", transform=ax_conv.transAxes, ha="right", fontsize=7, color="#64748B")
    ax_conv.set_yscale("log")
    ax_conv.set_xlabel("Ionic relaxation step", fontsize=8)
    ax_conv.set_ylabel(r"$|F_i-F_{final}|$ (meV / HfO$_2$)", fontsize=8)
    ax_conv.set_title("Ionic relaxation convergence", fontsize=8, pad=3)
    ax_conv.grid(axis="y", color="#E5E7EB", linewidth=0.45, which="major")
    ax_conv.grid(axis="y", color="#F1F5F9", linewidth=0.28, which="minor")
    ax_conv.legend(frameon=False, ncols=4, loc="upper right", fontsize=7, handlelength=2.0, title="Phase", title_fontsize=7)
    ax_conv.set_xlim(0.5, convergence["ionic_step"].max() + 0.8)
    ax_conv.tick_params(labelsize=7)
    _panel_label(ax_conv, "d", x=-0.055, y=1.1)
    _export(fig, REPORT_CUT_DIR / "03_relaxation_convergence")

    readme = REPORT_CUT_DIR / "README_report_cuts.md"
    readme.write_text(
        "\n".join(
            [
                "# HfO2-FerroKG report cut figures",
                "",
                "These figures are split by presentation narrative while preserving the same source data and top-journal style palette.",
                "",
                "- 01_relaxed_structures: structure source and phase comparison.",
                "- 02_phase_energy_descriptors: phase stability and energy-volume descriptor.",
                "- 03_relaxation_convergence: VASP ionic relaxation trace.",
                "",
                "Use SVG/PDF in Inkscape for final layout polishing; do not manually alter data values.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    pdf = build_publication_figure()
    build_report_cut_figures()
    print(f"publication_figure={pdf}")
    print(f"report_cut_dir={REPORT_CUT_DIR}")
    print(f"output_dir={OUT_DIR}")


if __name__ == "__main__":
    main()
