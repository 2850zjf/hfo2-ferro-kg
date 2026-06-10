from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


WORKFLOW_DIR = Path(__file__).resolve().parent
DEFAULT_TARGETS = WORKFLOW_DIR / "phase_targets.json"
DEFAULT_OUTPUT = Path("data/computation/mp_hfo2_phase_smoke_test")


@dataclass(frozen=True)
class Candidate:
    material_id: str
    formula_pretty: str
    energy_above_hull: float | None
    spacegroup_symbol: str
    spacegroup_number: int | None
    crystal_system: str
    structure: Any


def _load_runtime_imports():
    try:
        from mp_api.client import MPRester
        from pymatgen.io.vasp.inputs import Poscar
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Missing compute dependencies. Install with:\n"
            "python -m pip install -r computations/mp_hfo2_phase_smoke_test/requirements-compute.txt"
        ) from exc
    return MPRester, Poscar


def _symmetry_value(symmetry: Any, key: str, default: Any = "") -> Any:
    if symmetry is None:
        return default
    if isinstance(symmetry, dict):
        return symmetry.get(key, default)
    return getattr(symmetry, key, default)


def _as_candidate(doc: Any) -> Candidate:
    symmetry = getattr(doc, "symmetry", None)
    material_id = str(getattr(doc, "material_id", ""))
    return Candidate(
        material_id=material_id,
        formula_pretty=str(getattr(doc, "formula_pretty", "")),
        energy_above_hull=getattr(doc, "energy_above_hull", None),
        spacegroup_symbol=str(_symmetry_value(symmetry, "symbol", "")),
        spacegroup_number=_symmetry_value(symmetry, "number", None),
        crystal_system=str(_symmetry_value(symmetry, "crystal_system", "")),
        structure=getattr(doc, "structure", None),
    )


def _norm(text: str) -> str:
    return text.replace(" ", "").replace("_", "").replace("-", "").lower()


def _matches_target(candidate: Candidate, target: dict[str, Any]) -> bool:
    system = str(target.get("preferred_crystal_system", ""))
    fragments = [str(item) for item in target.get("preferred_spacegroup_fragments", [])]
    if system and candidate.crystal_system.lower() == system.lower():
        return True
    candidate_symbol = _norm(candidate.spacegroup_symbol)
    return any(_norm(fragment) in candidate_symbol for fragment in fragments)


def _candidate_sort_key(candidate: Candidate) -> tuple[float, str]:
    energy = candidate.energy_above_hull
    if energy is None:
        energy = 999.0
    return float(energy), candidate.material_id


def _row(candidate: Candidate, label: str = "") -> dict[str, Any]:
    return {
        "label": label,
        "material_id": candidate.material_id,
        "formula_pretty": candidate.formula_pretty,
        "energy_above_hull": candidate.energy_above_hull,
        "spacegroup_symbol": candidate.spacegroup_symbol,
        "spacegroup_number": candidate.spacegroup_number,
        "crystal_system": candidate.crystal_system,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _copy_templates(target_dir: Path) -> None:
    template_dir = WORKFLOW_DIR / "templates"
    for name in ["INCAR.relax", "INCAR.static", "KPOINTS"]:
        shutil.copyfile(template_dir / name, target_dir / name)
    (target_dir / "POTCAR_NOT_INCLUDED.txt").write_text(
        "POTCAR is intentionally not included. Copy it on the licensed VASP machine "
        "according to your institutional setup and record the PAW symbols in results_template.csv.\n",
        encoding="utf-8",
    )


def _write_job_notes(target_dir: Path, candidate: Candidate, label: str) -> None:
    notes = f"""# {label} | {candidate.material_id}

Source: Materials Project via mp-api.

- formula: {candidate.formula_pretty}
- space group: {candidate.spacegroup_symbol} ({candidate.spacegroup_number})
- crystal system: {candidate.crystal_system}
- energy above hull from MP: {candidate.energy_above_hull}

Run pattern:

1. Copy the correct POTCAR on the licensed VASP machine.
2. Run relaxation with INCAR.relax.
3. Run static calculation with INCAR.static from the relaxed structure.
4. Normalize final total energy per formula unit.
5. Fill ../../results_template.csv or your copied results CSV.

Claim boundary:

This is a HfO2 phase-stability smoke test. It checks computational setup quality and relative polymorph trends. It does not directly predict experimental Pr or 2Pr.
"""
    (target_dir / "job_notes.md").write_text(notes, encoding="utf-8")


def fetch_candidates(formula: str, max_energy_above_hull: float | None) -> list[Candidate]:
    api_key = os.getenv("MP_API_KEY")
    if not api_key:
        raise SystemExit("MP_API_KEY is not set. Export it in the shell; do not write it into the repository.")

    MPRester, _ = _load_runtime_imports()
    fields = [
        "material_id",
        "formula_pretty",
        "energy_above_hull",
        "symmetry",
        "structure",
    ]
    with MPRester(api_key) as mpr:
        docs = mpr.materials.summary.search(formula=formula, fields=fields)
    candidates = [_as_candidate(doc) for doc in docs if getattr(doc, "structure", None) is not None]
    if max_energy_above_hull is not None:
        candidates = [
            item
            for item in candidates
            if item.energy_above_hull is not None and float(item.energy_above_hull) <= max_energy_above_hull
        ]
    return sorted(candidates, key=_candidate_sort_key)


def select_targets(candidates: list[Candidate], targets: list[dict[str, Any]]) -> tuple[list[tuple[str, Candidate]], list[str]]:
    selected: list[tuple[str, Candidate]] = []
    missing: list[str] = []
    used_ids: set[str] = set()
    for target in targets:
        label = str(target.get("label", "target"))
        matches = [item for item in candidates if item.material_id not in used_ids and _matches_target(item, target)]
        if not matches:
            missing.append(label)
            continue
        choice = sorted(matches, key=_candidate_sort_key)[0]
        selected.append((label, choice))
        used_ids.add(choice.material_id)
    return selected, missing


def write_vasp_folders(selected: list[tuple[str, Candidate]], output_dir: Path) -> None:
    _, Poscar = _load_runtime_imports()
    for label, candidate in selected:
        folder = output_dir / f"{label}__{candidate.material_id}"
        folder.mkdir(parents=True, exist_ok=True)
        Poscar(candidate.structure).write_file(folder / "POSCAR")
        _copy_templates(folder)
        _write_job_notes(folder, candidate, label)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch HfO2 polymorph structures from Materials Project and prepare VASP folders.")
    parser.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--formula", default=None, help="Override formula from phase_targets.json. Use HfO2 for the first smoke test.")
    parser.add_argument("--max-energy-above-hull", type=float, default=0.35)
    parser.add_argument("--list-only", action="store_true", help="Only write candidate and selected manifests; do not write POSCAR folders.")
    args = parser.parse_args()

    config = json.loads(args.targets.read_text(encoding="utf-8"))
    formula = args.formula or config.get("formula", "HfO2")
    targets = list(config.get("targets", []))

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates = fetch_candidates(formula=formula, max_energy_above_hull=args.max_energy_above_hull)
    selected, missing = select_targets(candidates, targets)

    _write_csv(output_dir / "candidates_manifest.csv", [_row(item) for item in candidates])
    _write_csv(output_dir / "selected_manifest.csv", [_row(candidate, label) for label, candidate in selected])
    (output_dir / "missing_targets.json").write_text(json.dumps({"missing": missing}, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copyfile(WORKFLOW_DIR / "results_template.csv", output_dir / "results_template.csv")

    if not args.list_only:
        write_vasp_folders(selected, output_dir)

    summary = {
        "formula": formula,
        "candidates": len(candidates),
        "selected": len(selected),
        "missing_targets": missing,
        "output_dir": str(output_dir),
        "note": "Structures were fetched from Materials Project. POTCAR is not included.",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
