from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence


AUDIT_VERSION = "vasp-raw-output-audit-v0.1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORTS_ROOT = PROJECT_ROOT / "reports"
DEFAULT_SOURCE_DIR = Path(
    "/Users/jinfengzhang/Codex/Ferroelectric knowledgegraph/KG agent/"
    "hfo2-ferro-kg/data/computation/tefs_hfo2_phase_smoke_20260622/"
    "runs/hfo2_phase_smoke"
)

DEFAULT_PHASES = ("monoclinic", "orthorhombic", "tetragonal", "cubic")
EXPECTED_SPACEGROUP_NUMBERS = {
    "monoclinic": 14,
    "orthorhombic": 29,
    "tetragonal": 137,
    "cubic": 225,
}
REQUIRED_PHASE_FILES = ("POSCAR", "CONTCAR", "OUTCAR", "vasprun.xml", "relax.output")
HASHED_PHASE_FILES = REQUIRED_PHASE_FILES + (
    "static.output",
    "INCAR.relax",
    "INCAR.static",
    "KPOINTS",
)
SUMMARY_FILENAME = "phase_energy_summary.csv"
ENERGY_MATCH_TOLERANCE_EV = 1.0e-5
FORCE_SMOKE_THRESHOLD_EV_A = 0.02
FORCE_PUBLICATION_TARGET_EV_A = 0.01

_FLOAT = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?"


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_record(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "path": str(path),
            "exists": False,
            "byte_size": None,
            "sha256": None,
        }
    return {
        "path": str(path.resolve()),
        "exists": True,
        "byte_size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _last_float(pattern: str, text: str, *, flags: int = 0) -> float | None:
    matches = re.findall(pattern, text, flags=flags)
    if not matches:
        return None
    value = matches[-1]
    if isinstance(value, tuple):
        value = value[-1]
    return _safe_float(value)


def _last_int(pattern: str, text: str, *, flags: int = 0) -> int | None:
    value = _last_float(pattern, text, flags=flags)
    return int(value) if value is not None else None


def _deduplicate(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in seen:
            output.append(normalized)
            seen.add(normalized)
    return output


def _determinant_3x3(matrix: Sequence[Sequence[float]]) -> float:
    a, b, c = matrix
    return (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )


def _inverse_3x3(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    a, b, c = matrix
    determinant = _determinant_3x3(matrix)
    if abs(determinant) < 1.0e-14:
        raise ValueError("POSCAR lattice is singular")
    return [
        [
            (b[1] * c[2] - b[2] * c[1]) / determinant,
            (a[2] * c[1] - a[1] * c[2]) / determinant,
            (a[1] * b[2] - a[2] * b[1]) / determinant,
        ],
        [
            (b[2] * c[0] - b[0] * c[2]) / determinant,
            (a[0] * c[2] - a[2] * c[0]) / determinant,
            (a[2] * b[0] - a[0] * b[2]) / determinant,
        ],
        [
            (b[0] * c[1] - b[1] * c[0]) / determinant,
            (a[1] * c[0] - a[0] * c[1]) / determinant,
            (a[0] * b[1] - a[1] * b[0]) / determinant,
        ],
    ]


def _row_vector_times_matrix(vector: Sequence[float], matrix: Sequence[Sequence[float]]) -> list[float]:
    return [sum(vector[index] * matrix[index][column] for index in range(3)) for column in range(3)]


def _formula_from_composition(composition: dict[str, int]) -> str:
    return "".join(f"{element}{count}" for element, count in composition.items())


def parse_poscar(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    lines = [line.strip() for line in target.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    if len(lines) < 8:
        raise ValueError(f"POSCAR is too short: {target}")

    scale = float(lines[1].split()[0])
    raw_lattice = [[float(value) for value in lines[index].split()[:3]] for index in range(2, 5)]
    raw_volume = abs(_determinant_3x3(raw_lattice))
    if scale < 0:
        if raw_volume <= 0:
            raise ValueError(f"POSCAR has invalid negative volume scale: {target}")
        lattice_scale = (abs(scale) / raw_volume) ** (1.0 / 3.0)
    else:
        lattice_scale = scale
    lattice = [[component * lattice_scale for component in vector] for vector in raw_lattice]

    species_tokens = lines[5].split()
    vasp4_counts = all(re.fullmatch(r"\d+", token) for token in species_tokens)
    if vasp4_counts:
        species = [f"X{index + 1}" for index in range(len(species_tokens))]
        counts = [int(token) for token in species_tokens]
        coordinate_mode_index = 6
    else:
        species = species_tokens
        counts = [int(float(token)) for token in lines[6].split()]
        coordinate_mode_index = 7
    if len(species) != len(counts):
        raise ValueError(f"POSCAR species/count mismatch: {target}")

    if lines[coordinate_mode_index].lower().startswith("s"):
        coordinate_mode_index += 1
    coordinate_mode = lines[coordinate_mode_index].lower()
    coordinate_start = coordinate_mode_index + 1
    atom_count = sum(counts)
    coordinate_lines = lines[coordinate_start : coordinate_start + atom_count]
    if len(coordinate_lines) != atom_count:
        raise ValueError(f"POSCAR coordinate count mismatch: {target}")
    coordinates = [[float(value) for value in line.split()[:3]] for line in coordinate_lines]
    if coordinate_mode.startswith("d"):
        fractional_coordinates = coordinates
    elif coordinate_mode.startswith(("c", "k")):
        inverse = _inverse_3x3(lattice)
        cartesian = [[component * lattice_scale for component in vector] for vector in coordinates]
        fractional_coordinates = [_row_vector_times_matrix(vector, inverse) for vector in cartesian]
    else:
        raise ValueError(f"Unknown POSCAR coordinate mode {lines[coordinate_mode_index]!r}: {target}")

    composition = {element: count for element, count in zip(species, counts)}
    formula_units = None
    if set(composition) == {"Hf", "O"} and composition["Hf"] > 0 and composition["O"] == 2 * composition["Hf"]:
        formula_units = composition["Hf"]
    atom_types: list[int] = []
    for index, count in enumerate(counts, start=1):
        atom_types.extend([index] * count)

    return {
        "comment": lines[0],
        "species": species,
        "counts": counts,
        "composition": composition,
        "formula": _formula_from_composition(composition),
        "atom_count": atom_count,
        "formula_units_hfo2": formula_units,
        "lattice_A": lattice,
        "volume_A3": abs(_determinant_3x3(lattice)),
        "coordinate_mode": "direct" if coordinate_mode.startswith("d") else "cartesian",
        "fractional_coordinates": fractional_coordinates,
        "atom_types": atom_types,
    }


def _last_force_block_from_outcar(text: str) -> list[list[float]]:
    lines = text.splitlines()
    last_block: list[list[float]] = []
    index = 0
    while index < len(lines):
        if "POSITION" not in lines[index] or "TOTAL-FORCE" not in lines[index]:
            index += 1
            continue
        block: list[list[float]] = []
        index += 1
        while index < len(lines):
            stripped = lines[index].strip()
            index += 1
            if not stripped or set(stripped) <= {"-"}:
                if block:
                    break
                continue
            parts = stripped.split()
            if len(parts) < 6:
                if block:
                    break
                continue
            force = [_safe_float(parts[3]), _safe_float(parts[4]), _safe_float(parts[5])]
            if any(value is None for value in force):
                if block:
                    break
                continue
            block.append([float(value) for value in force if value is not None])
        if block:
            last_block = block
    return last_block


def _max_force(forces: Sequence[Sequence[float]]) -> float | None:
    if not forces:
        return None
    return max(math.sqrt(sum(component * component for component in force[:3])) for force in forces)


def parse_outcar(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    text = target.read_text(encoding="utf-8", errors="replace")
    version_match = re.search(r"^\s*(vasp\.\S+[^\n]*)", text, flags=re.MULTILINE | re.IGNORECASE)
    potcar_titles = _deduplicate(re.findall(r"^\s*TITEL\s*=\s*(.+?)\s*$", text, flags=re.MULTILINE))
    nsw = _last_int(r"\bNSW\s*=\s*(-?\d+)", text)
    ibrion = _last_int(r"\bIBRION\s*=\s*(-?\d+)", text)
    nelm = _last_int(r"\bNELM\s*=\s*(\d+)", text)
    nions = _last_int(r"\bNIONS\s*=\s*(\d+)", text)
    encut = _last_float(r"\bENCUT\s*=\s*(" + _FLOAT + r")", text)
    nkpts = _last_int(r"\bNKPTS\s*=\s*(\d+)", text)
    toten = _last_float(r"free\s+energy\s+TOTEN\s*=\s*(" + _FLOAT + r")", text)
    e0 = _last_float(r"energy\(sigma->0\)\s*=\s*(" + _FLOAT + r")", text)
    force_block = _last_force_block_from_outcar(text)
    run_completed = "General timing and accounting informations for this job" in text
    electronic_marker = "aborting loop because EDIFF is reached" in text
    ionic_marker = "reached required accuracy - stopping structural energy minimisation" in text
    static_run = nsw == 0 or ibrion == -1
    return {
        "vasp_signature": version_match.group(1).strip() if version_match else None,
        "potcar_titles": potcar_titles,
        "nions": nions,
        "nsw": nsw,
        "ibrion": ibrion,
        "nelm": nelm,
        "encut_eV": encut,
        "nkpts": nkpts,
        "toten_eV": toten,
        "e0_energy_eV": e0,
        "selected_energy_eV": e0 if e0 is not None else toten,
        "final_forces_eV_A": force_block,
        "final_max_force_eV_A": _max_force(force_block),
        "run_completed": run_completed,
        "electronic_converged": bool(run_completed and electronic_marker),
        "ionic_converged": None if static_run else bool(run_completed and ionic_marker),
        "static_run": static_run,
        "convergence_markers": {
            "ediff_reached": electronic_marker,
            "ionic_accuracy_reached": ionic_marker,
            "general_timing_present": run_completed,
        },
    }


def parse_relax_output(path: str | Path) -> dict[str, Any]:
    """Parse the preserved relaxation stdout without treating static output as ionic evidence."""

    target = Path(path)
    text = target.read_text(encoding="utf-8", errors="replace")
    ionic_steps = [
        {
            "step": int(match.group(1)),
            "free_energy_eV": float(match.group(2)),
            "e0_energy_eV": float(match.group(3)),
        }
        for match in re.finditer(
            r"^\s*(\d+)\s+F=\s*(" + _FLOAT + r")\s+E0=\s*(" + _FLOAT + r")",
            text,
            flags=re.MULTILINE,
        )
    ]
    accuracy_marker = "reached required accuracy - stopping structural energy minimisation" in text
    fatal_patterns = {
        "brmix": r"\bBRMIX:\s*very serious problems\b",
        "zhegv": r"\bZHEGV\b.*\bfailed\b",
        "segmentation_fault": r"segmentation fault",
        "mpi_abort": r"MPI_ABORT",
    }
    fatal_markers = [
        name for name, pattern in fatal_patterns.items() if re.search(pattern, text, flags=re.IGNORECASE)
    ]
    return {
        "ionic_steps": len(ionic_steps),
        "last_ionic_step": ionic_steps[-1] if ionic_steps else None,
        "required_accuracy_marker": accuracy_marker,
        "ionic_converged": bool(accuracy_marker and ionic_steps and not fatal_markers),
        "fatal_markers": fatal_markers,
        "provenance_limit": "relax_OUTCAR_and_relax_vasprun_xml_were_not_preserved",
    }


def _xml_named_value(element: ET.Element | None, name: str) -> str | None:
    if element is None:
        return None
    for item in element.findall("i"):
        if item.get("name") == name:
            return (item.text or "").strip()
    return None


def _xml_parameter(root: ET.Element, name: str) -> str | None:
    for section_name in ("incar", "parameters"):
        section = root.find(section_name)
        if section is None:
            continue
        for item in section.iter("i"):
            if item.get("name") == name:
                return (item.text or "").strip()
    return None


def _xml_varray(element: ET.Element | None, name: str) -> list[list[float]]:
    if element is None:
        return []
    varray = element.find(f"varray[@name='{name}']")
    if varray is None:
        return []
    output: list[list[float]] = []
    for vector in varray.findall("v"):
        values = [_safe_float(value) for value in (vector.text or "").split()[:3]]
        if len(values) == 3 and all(value is not None for value in values):
            output.append([float(value) for value in values if value is not None])
    return output


def parse_vasprun_xml(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    tree = ET.parse(target)
    root = tree.getroot()
    generator = root.find("generator")
    generator_values = {
        name: _xml_named_value(generator, name)
        for name in ("program", "version", "subversion", "platform")
    }
    calculations = root.findall("calculation")
    last_calculation = calculations[-1] if calculations else None
    final_energy = last_calculation.find("energy") if last_calculation is not None else None
    e0 = _safe_float(_xml_named_value(final_energy, "e_0_energy"))
    toten = _safe_float(_xml_named_value(final_energy, "e_fr_energy"))
    forces = _xml_varray(last_calculation, "forces")
    stresses = _xml_varray(last_calculation, "stress")
    electronic_steps = len(last_calculation.findall("scstep")) if last_calculation is not None else 0
    nelm_value = _safe_float(_xml_parameter(root, "NELM"))
    nsw_value = _safe_float(_xml_parameter(root, "NSW"))
    ibrion_value = _safe_float(_xml_parameter(root, "IBRION"))
    nelm = int(nelm_value) if nelm_value is not None else None
    nsw = int(nsw_value) if nsw_value is not None else None
    ibrion = int(ibrion_value) if ibrion_value is not None else None
    return {
        "generator": generator_values,
        "calculation_count": len(calculations),
        "electronic_steps_last_calculation": electronic_steps,
        "nelm": nelm,
        "nsw": nsw,
        "ibrion": ibrion,
        "e0_energy_eV": e0,
        "toten_eV": toten,
        "selected_energy_eV": e0 if e0 is not None else toten,
        "final_forces_eV_A": forces,
        "final_max_force_eV_A": _max_force(forces),
        "final_stress_kbar": stresses,
        "xml_complete": bool(calculations and final_energy is not None),
        "electronic_converged_by_step_count": (
            electronic_steps < nelm if nelm is not None and electronic_steps else None
        ),
        "static_run": nsw == 0 or ibrion == -1,
    }


def _spglib_symmetry(structure: dict[str, Any], expected_number: int | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "available": False,
        "checked": False,
        "spglib_version": None,
        "expected_spacegroup_number": expected_number,
        "datasets": [],
        "matches_expected": None,
        "error": None,
    }
    try:
        import spglib  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        result["error"] = "spglib_not_installed"
        return result

    result["available"] = True
    result["spglib_version"] = str(getattr(spglib, "__version__", "unknown"))
    cell = (
        structure["lattice_A"],
        structure["fractional_coordinates"],
        structure["atom_types"],
    )
    try:
        for symprec in (1.0e-3, 1.0e-2):
            dataset = spglib.get_symmetry_dataset(cell, symprec=symprec)
            if dataset is None:
                result["datasets"].append({"symprec": symprec, "status": "not_resolved"})
                continue
            number = getattr(dataset, "number", None)
            international = getattr(dataset, "international", None)
            hall = getattr(dataset, "hall", None)
            if isinstance(dataset, dict):
                number = dataset.get("number", number)
                international = dataset.get("international", international)
                hall = dataset.get("hall", hall)
            result["datasets"].append(
                {
                    "symprec": symprec,
                    "spacegroup_number": int(number) if number is not None else None,
                    "international": str(international) if international is not None else None,
                    "hall": str(hall) if hall is not None else None,
                }
            )
        numbers = {
            row["spacegroup_number"]
            for row in result["datasets"]
            if row.get("spacegroup_number") is not None
        }
        result["checked"] = bool(numbers)
        result["matches_expected"] = expected_number in numbers if expected_number is not None and numbers else None
    except Exception as exc:  # pragma: no cover - depends on optional native runtime
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _read_summary(source_dir: Path) -> dict[str, Any]:
    path = source_dir / SUMMARY_FILENAME
    file_record = _file_record(path)
    rows: dict[str, dict[str, str]] = {}
    error = None
    if path.is_file():
        try:
            with path.open(encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    phase = str(row.get("phase") or "").strip()
                    if phase:
                        rows[phase] = dict(row)
        except (OSError, csv.Error) as exc:
            error = f"{type(exc).__name__}: {exc}"
    return {
        "authoritative": False,
        "purpose": "cross_check_only_raw_OUTCAR_and_vasprun_xml_are_authoritative",
        "file": file_record,
        "rows": rows,
        "error": error,
    }


def _summary_energy(row: dict[str, str] | None) -> float | None:
    if not row:
        return None
    for name in ("total_energy_eV", "final_energy_eV"):
        value = _safe_float(row.get(name))
        if value is not None:
            return value
    return None


def audit_vasp_phase(
    phase_dir: str | Path,
    phase: str,
    *,
    summary_row: dict[str, str] | None = None,
) -> dict[str, Any]:
    directory = Path(phase_dir).resolve()
    files = {name: _file_record(directory / name) for name in HASHED_PHASE_FILES}
    blockers: list[str] = []
    for name in REQUIRED_PHASE_FILES:
        record = files[name]
        if not record["exists"]:
            blockers.append(f"missing_raw_file:{name}")

    poscar: dict[str, Any] | None = None
    contcar: dict[str, Any] | None = None
    outcar: dict[str, Any] | None = None
    vasprun: dict[str, Any] | None = None
    relax_output: dict[str, Any] | None = None
    parse_errors: dict[str, str] = {}
    parsers = {
        "POSCAR": (parse_poscar, "poscar"),
        "CONTCAR": (parse_poscar, "contcar"),
        "OUTCAR": (parse_outcar, "outcar"),
        "vasprun.xml": (parse_vasprun_xml, "vasprun"),
        "relax.output": (parse_relax_output, "relax_output"),
    }
    parsed: dict[str, dict[str, Any]] = {}
    for filename, (parser, key) in parsers.items():
        path = directory / filename
        if not path.is_file():
            continue
        try:
            parsed[key] = parser(path)
        except (OSError, ValueError, ET.ParseError) as exc:
            parse_errors[filename] = f"{type(exc).__name__}: {exc}"
            blockers.append(f"raw_parse_failed:{filename}")
    poscar = parsed.get("poscar")
    contcar = parsed.get("contcar")
    outcar = parsed.get("outcar")
    vasprun = parsed.get("vasprun")
    relax_output = parsed.get("relax_output")

    if poscar and contcar:
        if poscar["composition"] != contcar["composition"]:
            blockers.append("poscar_contcar_composition_mismatch")
        if poscar["atom_count"] != contcar["atom_count"]:
            blockers.append("poscar_contcar_atom_count_mismatch")
    structure = contcar or poscar
    formula_units = structure.get("formula_units_hfo2") if structure else None
    if structure and formula_units is None:
        blockers.append("structure_is_not_exact_stoichiometric_hfo2")
    if outcar and structure and outcar.get("nions") not in (None, structure["atom_count"]):
        blockers.append("outcar_structure_atom_count_mismatch")

    outcar_energy = outcar.get("selected_energy_eV") if outcar else None
    vasprun_energy = vasprun.get("selected_energy_eV") if vasprun else None
    energy_difference = None
    if outcar_energy is not None and vasprun_energy is not None:
        energy_difference = abs(float(outcar_energy) - float(vasprun_energy))
        if energy_difference > ENERGY_MATCH_TOLERANCE_EV:
            blockers.append("outcar_vasprun_energy_mismatch")
    raw_energy = vasprun_energy if vasprun_energy is not None else outcar_energy
    if raw_energy is None:
        blockers.append("missing_raw_total_energy")
    energy_per_fu = float(raw_energy) / formula_units if raw_energy is not None and formula_units else None

    max_force = vasprun.get("final_max_force_eV_A") if vasprun else None
    if max_force is None and outcar:
        max_force = outcar.get("final_max_force_eV_A")
    force_difference = None
    if outcar and vasprun:
        outcar_force = outcar.get("final_max_force_eV_A")
        vasprun_force = vasprun.get("final_max_force_eV_A")
        if outcar_force is not None and vasprun_force is not None:
            force_difference = abs(float(outcar_force) - float(vasprun_force))
    if max_force is None:
        blockers.append("missing_final_force")
    else:
        if float(max_force) > FORCE_SMOKE_THRESHOLD_EV_A:
            blockers.append("final_force_above_0p02_eV_per_A")
        if float(max_force) > FORCE_PUBLICATION_TARGET_EV_A:
            blockers.append("final_force_above_0p01_publication_target_eV_per_A")

    run_completed = bool(outcar and outcar.get("run_completed") and vasprun and vasprun.get("xml_complete"))
    electronic_converged = bool(
        run_completed
        and outcar
        and outcar.get("electronic_converged")
        and vasprun
        and vasprun.get("electronic_converged_by_step_count") is not False
    )
    if not run_completed:
        blockers.append("raw_static_run_not_complete")
    if not electronic_converged:
        blockers.append("static_electronic_convergence_not_verified")
    static_run = bool((outcar and outcar.get("static_run")) or (vasprun and vasprun.get("static_run")))
    ionic_converged = relax_output.get("ionic_converged") if relax_output else None
    if ionic_converged is not True:
        blockers.append("ionic_relaxation_not_verified")
    blockers.append("relax_outcar_and_vasprun_xml_not_preserved")

    expected_spacegroup = EXPECTED_SPACEGROUP_NUMBERS.get(phase)
    symmetry = _spglib_symmetry(structure, expected_spacegroup) if structure else {
        "available": False,
        "checked": False,
        "expected_spacegroup_number": expected_spacegroup,
        "datasets": [],
        "matches_expected": None,
        "error": "missing_structure",
    }
    if not symmetry.get("checked"):
        blockers.append("phase_identity_not_verified")
    elif symmetry.get("matches_expected") is not True:
        blockers.append("final_structure_spacegroup_does_not_match_phase_label")

    summary_value = _summary_energy(summary_row)
    summary_delta = abs(float(raw_energy) - summary_value) if raw_energy is not None and summary_value is not None else None
    summary_matches = summary_delta <= ENERGY_MATCH_TOLERANCE_EV if summary_delta is not None else None
    if summary_matches is False:
        blockers.append("summary_energy_mismatch_raw_output")

    potcar_titles = outcar.get("potcar_titles", []) if outcar else []
    if not potcar_titles:
        blockers.append("potcar_titles_not_found_in_outcar")
    blockers.extend(
        [
            "potcar_binary_sha256_unavailable",
            "no_encut_convergence_series",
            "no_kpoint_density_convergence_series",
            "no_biaxial_strain_series",
        ]
    )

    return {
        "phase": phase,
        "source_directory": str(directory),
        "files": files,
        "parse_errors": parse_errors,
        "structure": {
            "input": _public_structure_summary(poscar),
            "final": _public_structure_summary(contcar),
            "composition_consistent": (
                poscar["composition"] == contcar["composition"] if poscar and contcar else None
            ),
            "symmetry": symmetry,
        },
        "vasp": {
            "outcar_signature": outcar.get("vasp_signature") if outcar else None,
            "xml_generator": vasprun.get("generator") if vasprun else None,
            "potcar_titles_from_outcar": potcar_titles,
            "potcar_binary_sha256": None,
            "encut_eV_from_outcar": outcar.get("encut_eV") if outcar else None,
            "nkpts_from_outcar": outcar.get("nkpts") if outcar else None,
        },
        "energy": {
            "authority": "vasprun.xml:e_0_energy_then_OUTCAR:energy_sigma_to_zero",
            "total_energy_eV": raw_energy,
            "formula_units_hfo2": formula_units,
            "energy_eV_per_fu": energy_per_fu,
            "relative_energy_meV_per_fu": None,
            "outcar_energy_eV": outcar_energy,
            "vasprun_energy_eV": vasprun_energy,
            "outcar_vasprun_abs_difference_eV": energy_difference,
        },
        "convergence": {
            "run_completed": run_completed,
            "static_run": static_run,
            "electronic_converged": electronic_converged,
            "ionic_converged": ionic_converged,
            "ionic_convergence_note": (
                "derived_only_from_relax.output_required_accuracy_marker; top-level OUTCAR/vasprun.xml are static"
                if relax_output
                else "relax.output_missing_or_unparseable"
            ),
            "final_max_force_eV_A": max_force,
            "outcar_vasprun_max_force_abs_difference_eV_A": force_difference,
            "outcar": outcar,
            "vasprun": vasprun,
            "relax_output": relax_output,
        },
        "summary_cross_check": {
            "authoritative": False,
            "summary_row": summary_row,
            "summary_total_energy_eV": summary_value,
            "raw_summary_abs_difference_eV": summary_delta,
            "matches_raw_within_tolerance": summary_matches,
        },
        "raw_output_auditable": bool(
            raw_energy is not None
            and energy_per_fu is not None
            and run_completed
            and electronic_converged
            and ionic_converged is True
            and not parse_errors
        ),
        "publication_grade": False,
        "blockers": sorted(set(blockers)),
    }


def _public_structure_summary(structure: dict[str, Any] | None) -> dict[str, Any] | None:
    if structure is None:
        return None
    return {
        "comment": structure["comment"],
        "species": structure["species"],
        "counts": structure["counts"],
        "composition": structure["composition"],
        "formula": structure["formula"],
        "atom_count": structure["atom_count"],
        "formula_units_hfo2": structure["formula_units_hfo2"],
        "lattice_A": structure["lattice_A"],
        "volume_A3": structure["volume_A3"],
        "volume_A3_per_fu": (
            structure["volume_A3"] / structure["formula_units_hfo2"]
            if structure["formula_units_hfo2"]
            else None
        ),
        "coordinate_mode": structure["coordinate_mode"],
    }


def _input_consistency(phases: Sequence[dict[str, Any]]) -> dict[str, Any]:
    signatures = sorted(
        {
            str(row["vasp"].get("outcar_signature"))
            for row in phases
            if row["vasp"].get("outcar_signature")
        }
    )
    potcar_sets = {
        tuple(row["vasp"].get("potcar_titles_from_outcar") or [])
        for row in phases
        if row["vasp"].get("potcar_titles_from_outcar")
    }
    encuts = sorted(
        {
            float(row["vasp"]["encut_eV_from_outcar"])
            for row in phases
            if row["vasp"].get("encut_eV_from_outcar") is not None
        }
    )
    return {
        "vasp_signatures": signatures,
        "same_vasp_signature": len(signatures) == 1 and bool(signatures),
        "potcar_title_sets": [list(values) for values in sorted(potcar_sets)],
        "same_potcar_titles": len(potcar_sets) == 1 and bool(potcar_sets),
        "encut_values_eV": encuts,
        "same_encut": len(encuts) == 1 and bool(encuts),
        "potcar_binary_hash_checked": False,
    }


def audit_vasp_raw_outputs(
    source_dir: str | Path = DEFAULT_SOURCE_DIR,
    *,
    phases: Sequence[str] = DEFAULT_PHASES,
) -> dict[str, Any]:
    source = Path(source_dir).expanduser().resolve()
    if not source.is_dir():
        raise FileNotFoundError(source)

    summary = _read_summary(source)
    phase_rows = [
        audit_vasp_phase(
            source / phase,
            phase,
            summary_row=summary["rows"].get(phase),
        )
        for phase in phases
    ]
    finite_energies = [
        float(row["energy"]["energy_eV_per_fu"])
        for row in phase_rows
        if row["energy"].get("energy_eV_per_fu") is not None
    ]
    if finite_energies:
        baseline = min(finite_energies)
        for row in phase_rows:
            value = row["energy"].get("energy_eV_per_fu")
            if value is not None:
                row["energy"]["relative_energy_meV_per_fu"] = (float(value) - baseline) * 1000.0

    consistency = _input_consistency(phase_rows)
    global_blockers = {
        "zero_strain_smoke_test_only_no_biaxial_strain_matrix",
        "no_encut_convergence_study",
        "no_kpoint_density_convergence_study",
        "no_repeat_calculations_or_numerical_uncertainty",
        "potcar_binary_sha256_unavailable",
        "relax_OUTCAR_and_relax_vasprun_xml_not_preserved",
    }
    if not summary["file"]["exists"]:
        global_blockers.add("missing_non_authoritative_summary_cross_check")
    if summary.get("error"):
        global_blockers.add("summary_cross_check_parse_failed")
    if not consistency["same_vasp_signature"]:
        global_blockers.add("vasp_version_not_consistent_or_missing")
    if not consistency["same_potcar_titles"]:
        global_blockers.add("potcar_titles_not_consistent_or_missing")
    if not consistency["same_encut"]:
        global_blockers.add("encut_not_consistent_or_missing")
    if any(not row["structure"]["symmetry"].get("checked") for row in phase_rows):
        global_blockers.add("phase_identity_not_fully_verified")
    if any(
        row["convergence"].get("final_max_force_eV_A") is not None
        and float(row["convergence"]["final_max_force_eV_A"]) > FORCE_PUBLICATION_TARGET_EV_A
        for row in phase_rows
    ):
        global_blockers.add("one_or_more_final_forces_above_0p01_eV_per_A")
    for row in phase_rows:
        for blocker in row["blockers"]:
            if blocker.startswith(("missing_raw_file", "raw_parse_failed", "missing_raw_total_energy")):
                global_blockers.add(f"{row['phase']}:{blocker}")

    auditable_phases = sum(bool(row["raw_output_auditable"]) for row in phase_rows)
    return {
        "audit_version": AUDIT_VERSION,
        "generated_at": _utc_stamp(),
        "source_directory": str(source),
        "source_access_policy": "read_only_no_database_no_environment_file",
        "energy_authority": "raw_vasprun.xml_and_OUTCAR_only",
        "summary_policy": "phase_energy_summary.csv_is_cross_check_only_never_authority",
        "phases_requested": list(phases),
        "phases_auditable": auditable_phases,
        "all_requested_phases_auditable": auditable_phases == len(phase_rows),
        "input_consistency": consistency,
        "summary_cross_check": summary,
        "phases": phase_rows,
        "status": "audited_with_publication_blockers" if auditable_phases == len(phase_rows) else "incomplete_raw_audit",
        "publication_grade": False,
        "publication_blockers": sorted(global_blockers),
        "interpretation_boundary": (
            "The audited values are zero-strain static smoke-test descriptors parsed from raw files. "
            "They do not establish publication-grade phase energetics, biaxial-strain response, a phase boundary, "
            "or any experimental annealing/Pr/2Pr claim."
        ),
    }


def _ensure_output_under_reports(output_dir: Path, reports_root: Path) -> tuple[Path, Path]:
    root = reports_root.expanduser().resolve()
    output = output_dir.expanduser().resolve()
    if output != root and root not in output.parents:
        raise ValueError(f"Output directory must be inside reports root: {root}")
    return output, root


def _csv_phase_row(row: dict[str, Any]) -> dict[str, Any]:
    final_structure = row["structure"].get("final") or {}
    symmetry = row["structure"].get("symmetry") or {}
    datasets = symmetry.get("datasets") or []
    resolved_spacegroups = ";".join(
        f"{item.get('symprec')}:{item.get('international')}({item.get('spacegroup_number')})"
        for item in datasets
        if item.get("spacegroup_number") is not None
    )
    files = row["files"]
    return {
        "phase": row["phase"],
        "formula": final_structure.get("formula"),
        "atom_count": final_structure.get("atom_count"),
        "formula_units_hfo2": row["energy"].get("formula_units_hfo2"),
        "total_energy_eV": row["energy"].get("total_energy_eV"),
        "energy_eV_per_fu": row["energy"].get("energy_eV_per_fu"),
        "relative_energy_meV_per_fu": row["energy"].get("relative_energy_meV_per_fu"),
        "final_max_force_eV_A": row["convergence"].get("final_max_force_eV_A"),
        "run_completed": row["convergence"].get("run_completed"),
        "electronic_converged": row["convergence"].get("electronic_converged"),
        "ionic_converged": row["convergence"].get("ionic_converged"),
        "vasp_signature": row["vasp"].get("outcar_signature"),
        "potcar_titles": ";".join(row["vasp"].get("potcar_titles_from_outcar") or []),
        "resolved_spacegroups": resolved_spacegroups,
        "phase_identity_matches_expected": symmetry.get("matches_expected"),
        "summary_matches_raw": row["summary_cross_check"].get("matches_raw_within_tolerance"),
        "raw_output_auditable": row["raw_output_auditable"],
        "publication_grade": False,
        "blockers": ";".join(row["blockers"]),
        "poscar_sha256": files["POSCAR"].get("sha256"),
        "contcar_sha256": files["CONTCAR"].get("sha256"),
        "outcar_sha256": files["OUTCAR"].get("sha256"),
        "vasprun_xml_sha256": files["vasprun.xml"].get("sha256"),
        "relax_output_sha256": files["relax.output"].get("sha256"),
    }


def _markdown_report(audit: dict[str, Any]) -> str:
    symmetry_versions = sorted(
        {
            str(row["structure"]["symmetry"].get("spglib_version"))
            for row in audit["phases"]
            if row["structure"]["symmetry"].get("spglib_version")
        }
    )
    lines = [
        "# VASP Raw Output Audit",
        "",
        f"- status: `{audit['status']}`",
        f"- source: `{audit['source_directory']}`",
        f"- energy authority: `{audit['energy_authority']}`",
        f"- publication_grade: `{str(audit['publication_grade']).lower()}`",
        "- database writes: none",
        f"- symmetry runtime: `spglib {', '.join(symmetry_versions)}`" if symmetry_versions else "- symmetry runtime: unavailable",
        "",
        "## Raw phase records",
        "",
        "| phase | final SG | formula | E (eV/f.u.) | relative (meV/f.u.) | max force (eV/Å) | electronic converged | ionic converged | auditable |",
        "|---|---|---|---:|---:|---:|---|---|---|",
    ]
    for row in audit["phases"]:
        final_structure = row["structure"].get("final") or {}
        energy = row["energy"].get("energy_eV_per_fu")
        relative = row["energy"].get("relative_energy_meV_per_fu")
        force = row["convergence"].get("final_max_force_eV_A")
        symmetry = row["structure"].get("symmetry") or {}
        datasets = symmetry.get("datasets") or []
        resolved = next(
            (
                f"{item.get('international')} #{item.get('spacegroup_number')}"
                for item in datasets
                if item.get("spacegroup_number") is not None
            ),
            "not checked",
        )
        lines.append(
            "| {phase} | {resolved} | {formula} | {energy} | {relative} | {force} | {electronic} | {ionic} | {auditable} |".format(
                phase=row["phase"],
                resolved=resolved,
                formula=final_structure.get("formula") or "unknown",
                energy=f"{energy:.8f}" if energy is not None else "NA",
                relative=f"{relative:.3f}" if relative is not None else "NA",
                force=f"{force:.6f}" if force is not None else "NA",
                electronic=row["convergence"].get("electronic_converged"),
                ionic=row["convergence"].get("ionic_converged"),
                auditable=row["raw_output_auditable"],
            )
        )
    lines.extend(["", "## Publication blockers", ""])
    lines.extend(f"- `{blocker}`" for blocker in audit["publication_blockers"])
    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            audit["interpretation_boundary"],
            "",
            "`phase_energy_summary.csv` was used only for a consistency cross-check. No CSV value or pytest fixture was used as the energy authority.",
        ]
    )
    return "\n".join(lines) + "\n"


def export_vasp_raw_audit(
    audit: dict[str, Any],
    output_dir: str | Path,
    *,
    reports_root: str | Path = REPORTS_ROOT,
) -> dict[str, str]:
    output, _ = _ensure_output_under_reports(Path(output_dir), Path(reports_root))
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "vasp_raw_audit.json"
    csv_path = output / "vasp_raw_phase_audit.csv"
    markdown_path = output / "vasp_raw_audit.md"
    json_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    csv_rows = [_csv_phase_row(row) for row in audit["phases"]]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        fieldnames = list(csv_rows[0]) if csv_rows else ["phase", "publication_grade", "blockers"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(csv_rows)
    markdown_path.write_text(_markdown_report(audit), encoding="utf-8")
    return {
        "json": str(json_path),
        "csv": str(csv_path),
        "markdown": str(markdown_path),
    }
