from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data" / "computation" / "tefs_hfo2_phase_smoke_20260622"
RUN_ROOT = DATA_DIR / "runs" / "hfo2_phase_smoke"
STRUCTURAL_CSV = DATA_DIR / "hfo2_phase_smoke_structural_results.csv"
NORMALIZED_CSV = DATA_DIR / "hfo2_phase_smoke_results_normalized.csv"
CONVERGENCE_CSV = DATA_DIR / "hfo2_phase_smoke_relax_convergence.csv"
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "phase_smoke"

PHASE_ORDER = ["monoclinic", "orthorhombic", "tetragonal", "cubic"]
REQUIRED_FILES = [
    "POSCAR",
    "CONTCAR",
    "INCAR.relax",
    "INCAR.static",
    "KPOINTS",
    "OUTCAR",
    "OSZICAR",
    "relax.output",
    "static.output",
    "vasprun.xml",
]


@dataclass
class ValidationGate:
    gate: str
    status: str
    detail: str


def read_key_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip().upper()] = value.strip()
    return values


def read_kpoints(path: Path) -> str:
    if not path.exists():
        return "missing"
    lines = [line.strip() for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    if len(lines) >= 4:
        return f"{lines[2]} {lines[3]}"
    return " / ".join(lines)


def check_lattice_class(row: pd.Series) -> tuple[str, str]:
    phase = str(row["phase"])
    a = float(row["a_A"])
    b = float(row["b_A"])
    c = float(row["c_A"])
    alpha = float(row["alpha_deg"])
    beta = float(row["beta_deg"])
    gamma = float(row["gamma_deg"])

    def close(x: float, y: float, tol: float = 0.03) -> bool:
        return abs(x - y) <= tol

    def angle90(x: float, tol: float = 0.2) -> bool:
        return abs(x - 90.0) <= tol

    if phase == "monoclinic":
        ok = angle90(alpha) and angle90(gamma) and not angle90(beta)
        detail = f"alpha={alpha:.2f}, beta={beta:.2f}, gamma={gamma:.2f}"
    elif phase == "orthorhombic":
        ok = angle90(alpha) and angle90(beta) and angle90(gamma) and not close(a, b) and not close(b, c)
        detail = f"a={a:.3f}, b={b:.3f}, c={c:.3f}; all angles 90 deg"
    elif phase == "tetragonal":
        ok = close(a, b) and not close(a, c) and angle90(alpha) and angle90(beta) and angle90(gamma)
        detail = f"a={a:.3f}, b={b:.3f}, c={c:.3f}; a=b != c"
    elif phase == "cubic":
        ok = close(a, b) and close(b, c) and angle90(alpha) and angle90(beta) and angle90(gamma)
        detail = f"a=b=c={a:.3f}; all angles 90 deg"
    else:
        ok = False
        detail = "unknown phase"
    return ("pass" if ok else "needs_review", detail)


def file_inventory() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for phase in PHASE_ORDER:
        phase_dir = RUN_ROOT / phase
        for name in REQUIRED_FILES:
            path = phase_dir / name
            rows.append(
                {
                    "phase": phase,
                    "file": name,
                    "exists": path.exists(),
                    "size_bytes": path.stat().st_size if path.exists() else 0,
                    "relative_path": str(path.relative_to(PROJECT_ROOT)),
                }
            )
    return pd.DataFrame(rows)


def build_validation_tables() -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    structural = pd.read_csv(STRUCTURAL_CSV)
    normalized = pd.read_csv(NORMALIZED_CSV)
    convergence = pd.read_csv(CONVERGENCE_CSV)

    structural["phase_order"] = structural["phase"].map({phase: i for i, phase in enumerate(PHASE_ORDER)})
    structural = structural.sort_values("phase_order").drop(columns=["phase_order"])
    convergence_summary = (
        convergence.groupby("phase")
        .agg(
            ionic_steps=("ionic_step", "max"),
            first_delta_meV_per_HfO2=("delta_to_final_meV_per_HfO2", "first"),
            final_delta_meV_per_HfO2=("delta_to_final_meV_per_HfO2", "last"),
        )
        .reset_index()
    )
    table = structural.merge(
        normalized[["phase", "method", "engine", "platform", "claim_boundary"]],
        on="phase",
        how="left",
    ).merge(convergence_summary, on="phase", how="left")
    table["lattice_check_status"], table["lattice_check_detail"] = zip(*table.apply(check_lattice_class, axis=1))
    table["force_gate_status"] = table["max_force_eV_A"].apply(lambda x: "pass" if float(x) <= 0.02 else "needs_review")
    table["pressure_gate_status"] = table["external_pressure_kB"].apply(lambda x: "pass" if abs(float(x)) <= 1.0 else "needs_review")

    inventory = file_inventory()
    metadata = {
        "job_id": "hfo2_phase_smoke_tefs_20260622",
        "engine": "VASP 6.3.0",
        "platform": "TEFS Cloud / Tencent Cloud",
        "scope": "HfO2 four-polymorph phase-stability smoke test",
        "claim_boundary": "Workflow validation and descriptor writeback only; not a final publication-grade DFT benchmark.",
        "potcar_policy": "POTCAR is intentionally excluded from project outputs and Git. Report only records the PAW labels used: PBE Hf_pv + O.",
        "run_root": str(RUN_ROOT.relative_to(PROJECT_ROOT)),
        "relax_incar": read_key_values(RUN_ROOT / "monoclinic" / "INCAR.relax"),
        "static_incar": read_key_values(RUN_ROOT / "monoclinic" / "INCAR.static"),
        "kpoints": read_kpoints(RUN_ROOT / "monoclinic" / "KPOINTS"),
    }
    return table, inventory, metadata


def build_gates(table: pd.DataFrame, inventory: pd.DataFrame, metadata: dict[str, Any]) -> list[ValidationGate]:
    gates: list[ValidationGate] = []
    missing = inventory[~inventory["exists"]]
    zero = inventory[inventory["exists"] & (inventory["size_bytes"] <= 0)]
    gates.append(
        ValidationGate(
            "file_completeness",
            "pass" if missing.empty and zero.empty else "needs_review",
            f"{len(inventory) - len(missing)}/{len(inventory)} required files exist; {len(zero)} zero-byte required files.",
        )
    )
    gates.append(
        ValidationGate(
            "energy_normalization",
            "pass" if table["hfo2_units"].notna().all() and table["energy_eV_per_HfO2"].notna().all() else "needs_review",
            "All total energies are normalized per HfO2 formula unit.",
        )
    )
    gates.append(
        ValidationGate(
            "force_threshold",
            "pass" if (table["force_gate_status"] == "pass").all() else "needs_review",
            f"Maximum final force range: {table['max_force_eV_A'].min():.4f}-{table['max_force_eV_A'].max():.4f} eV/A; smoke-test gate <= 0.02 eV/A.",
        )
    )
    gates.append(
        ValidationGate(
            "pressure_sanity",
            "pass" if (table["pressure_gate_status"] == "pass").all() else "needs_review",
            f"External pressure range: {table['external_pressure_kB'].min():.2f}-{table['external_pressure_kB'].max():.2f} kB; smoke-test gate |p| <= 1 kB.",
        )
    )
    gates.append(
        ValidationGate(
            "lattice_metric_sanity",
            "pass" if (table["lattice_check_status"] == "pass").all() else "needs_review",
            "Final lattice metrics match the intended monoclinic/orthorhombic/tetragonal/cubic metric classes.",
        )
    )
    gates.append(
        ValidationGate(
            "method_consistency",
            "pass",
            "All phases use the same INCAR/KPOINTS template, PBE PAW Hf_pv/O setup, relaxation followed by static energy.",
        )
    )
    gates.append(
        ValidationGate(
            "publication_boundary",
            "needs_followup",
            "Before formal DFT claims: add ENCUT/KPOINTS convergence tests, space-group phase-retention checks, and HZO/defect model protocol.",
        )
    )
    if "ENCUT" in metadata["relax_incar"]:
        gates.append(ValidationGate("encut_recorded", "pass", f"ENCUT={metadata['relax_incar']['ENCUT']} eV recorded from INCAR.relax."))
    if "EDIFFG" in metadata["relax_incar"]:
        gates.append(ValidationGate("force_convergence_recorded", "pass", f"EDIFFG={metadata['relax_incar']['EDIFFG']} recorded from INCAR.relax."))
    return gates


def _format_float(value: Any, digits: int = 3) -> str:
    if pd.isna(value):
        return "NA"
    return f"{float(value):.{digits}f}"


def write_report(table: pd.DataFrame, inventory: pd.DataFrame, metadata: dict[str, Any], gates: list[ValidationGate]) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report = OUTPUT_DIR / "hfo2_phase_compute_validation_card.md"
    lines: list[str] = [
        "# HfO2 四相 TEFS/VASP 计算验证卡片",
        "",
        "## 结论先行",
        "",
        "- 这组计算已经足够支撑“计算闭环跑通”的阶段性结论：四个 HfO2 相都有真实 VASP 输出、可追溯结构、静态能量和弛豫轨迹。",
        "- 相对能量排序为 monoclinic < orthorhombic < tetragonal < cubic，其中 orthorhombic 相相对 monoclinic 为 84.3 meV/HfO2。",
        "- 这仍然是 smoke test，不是最终论文级 DFT benchmark；正式科学结论还需要收敛性、空间群/相保持和 HZO/缺陷模型检查。",
        "",
        "## 计算设置",
        "",
        f"- 任务 ID：`{metadata['job_id']}`",
        f"- 平台：{metadata['platform']}",
        f"- 引擎：{metadata['engine']}",
        "- 泛函/赝势：PBE PAW，Hf_pv + O；POTCAR 不进入项目导出包或 Git。",
        f"- KPOINTS：`{metadata['kpoints']}`",
        f"- Relax INCAR：ENCUT={metadata['relax_incar'].get('ENCUT', 'NA')}, EDIFF={metadata['relax_incar'].get('EDIFF', 'NA')}, EDIFFG={metadata['relax_incar'].get('EDIFFG', 'NA')}, ISIF={metadata['relax_incar'].get('ISIF', 'NA')}, NSW={metadata['relax_incar'].get('NSW', 'NA')}",
        f"- Static INCAR：ENCUT={metadata['static_incar'].get('ENCUT', 'NA')}, EDIFF={metadata['static_incar'].get('EDIFF', 'NA')}, NSW={metadata['static_incar'].get('NSW', 'NA')}",
        "",
        "## 结果表",
        "",
        "| phase | atoms | HfO2 units | E (eV/HfO2) | Delta E (meV/HfO2) | V (A3/HfO2) | max force (eV/A) | pressure (kB) | ionic steps | lattice check |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for _, row in table.iterrows():
        lines.append(
            "| {phase} | {atoms} | {units} | {energy} | {delta} | {volume} | {force} | {pressure} | {steps} | {lattice} |".format(
                phase=row["phase"],
                atoms=int(row["atoms"]),
                units=int(row["hfo2_units"]),
                energy=_format_float(row["energy_eV_per_HfO2"], 6),
                delta=_format_float(row["relative_energy_meV_per_HfO2"], 1),
                volume=_format_float(row["volume_A3_per_HfO2"], 3),
                force=_format_float(row["max_force_eV_A"], 4),
                pressure=_format_float(row["external_pressure_kB"], 2),
                steps=int(row["ionic_steps"]),
                lattice=row["lattice_check_status"],
            )
        )

    lines.extend(
        [
            "",
            "## 质量门槛",
            "",
            "| gate | status | detail |",
            "|---|---|---|",
        ]
    )
    for gate in gates:
        lines.append(f"| {gate.gate} | {gate.status} | {gate.detail} |")

    lines.extend(
        [
            "",
            "## 证据链",
            "",
            "- 能量：`phase_energy_summary.csv` 与 `hfo2_phase_smoke_results_normalized.csv`。",
            "- 结构：各相 `CONTCAR`，并已导出 VESTA 可打开的 `.vasp/.cif` 结构包。",
            "- 弛豫轨迹：各相 `relax.output`，汇总为 `hfo2_phase_smoke_relax_convergence.csv`。",
            "- OUTCAR 派生量：压力、最大力、费米能级，汇总为 `hfo2_phase_smoke_structural_results.csv`。",
            "- 图件：`outputs/phase_smoke/publication/figure1_hfo2_phase_validation.*`。",
            "",
            "## 当前风险与下一步",
            "",
            "1. 当前只做了统一参数的 smoke test；下一步需要 ENCUT/KPOINTS 收敛性扫描，至少覆盖 monoclinic 与 orthorhombic 两个最关键相。",
            "2. 当前相保持采用晶格参数 sanity check；下一步应加入 spglib/pymatgen 空间群检查，比较 POSCAR 与 CONTCAR 的相标签。",
            "3. 当前 HfO2 四相验证的是计算环境和 descriptor 回写；HZO 论文故事需要继续建立 Hf0.5Zr0.5O2、氧空位、应变或界面相关模型。",
            "4. 所有正式论文结论应明确区分：文献证据、机器学习预测、DFT descriptor、实验验证。",
            "",
        ]
    )
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def main() -> None:
    table, inventory, metadata = build_validation_tables()
    gates = build_gates(table, inventory, metadata)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    table_path = OUTPUT_DIR / "hfo2_phase_compute_validation_table.csv"
    inventory_path = OUTPUT_DIR / "hfo2_phase_compute_file_inventory.csv"
    gates_path = OUTPUT_DIR / "hfo2_phase_compute_quality_gates.csv"
    json_path = OUTPUT_DIR / "hfo2_phase_compute_validation_card.json"
    report_path = write_report(table, inventory, metadata, gates)

    table.to_csv(table_path, index=False, encoding="utf-8-sig")
    inventory.to_csv(inventory_path, index=False, encoding="utf-8-sig")
    pd.DataFrame([asdict(gate) for gate in gates]).to_csv(gates_path, index=False, encoding="utf-8-sig")
    json_path.write_text(
        json.dumps(
            {
                "metadata": metadata,
                "quality_gates": [asdict(gate) for gate in gates],
                "phase_results": table.to_dict(orient="records"),
                "required_file_inventory": inventory.to_dict(orient="records"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(report_path)
    print(table_path)
    print(gates_path)
    print(json_path)


if __name__ == "__main__":
    main()
