from __future__ import annotations

import csv
import hashlib
import json
import math
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


BUILDER_VERSION = "hfo2-biaxial-strain-pilot-v0.1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_ROOT = Path(
    "/Users/jinfengzhang/Codex/Ferroelectric knowledgegraph/KG agent/"
    "hfo2-ferro-kg/data/computation/tefs_hfo2_phase_smoke_20260622/"
    "runs/hfo2_phase_smoke"
)
STRAIN_FACTORS = (0.99, 1.0, 1.01)
TARGET_ATOMS = 12


@dataclass(frozen=True)
class PoscarStructure:
    comment: str
    lattice: tuple[tuple[float, float, float], ...]
    symbols: tuple[str, ...]
    counts: tuple[int, ...]
    fractional_coordinates: tuple[tuple[float, float, float], ...]

    @property
    def atom_count(self) -> int:
        return sum(self.counts)

    @property
    def formula_units(self) -> int:
        counts = dict(zip(self.symbols, self.counts))
        if set(counts) != {"Hf", "O"} or counts["O"] != 2 * counts["Hf"]:
            raise ValueError("Structure is not stoichiometric HfO2")
        return counts["Hf"]

    def coordinates_by_species(self) -> dict[str, tuple[tuple[float, float, float], ...]]:
        result: dict[str, tuple[tuple[float, float, float], ...]] = {}
        start = 0
        for symbol, count in zip(self.symbols, self.counts):
            result[symbol] = self.fractional_coordinates[start : start + count]
            start += count
        return result


PHASE_SPECS: dict[str, dict[str, Any]] = {
    "monoclinic": {
        "short_label": "m",
        "space_group_number": 14,
        "space_group_symbol": "P2_1/c",
        "source_sha256": "2efef57fd989be257b684e2c3f95b0b94cb0fc380ca31a0b8307c22f6162eef9",
        "source_atoms": 12,
        "supercell_matrix": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "translation_representatives": [[0, 0, 0]],
        "orientation": "source lattice vectors 1 and 2 define the coherent in-plane (001) plane",
    },
    "orthorhombic": {
        "short_label": "o",
        "space_group_number": 29,
        "space_group_symbol": "Pca2_1",
        "source_sha256": "4987c18c5a4edec450c47cb80b1fd7651e2f8ee08ee569406ebfb6dae2f46453",
        "source_atoms": 12,
        "supercell_matrix": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "translation_representatives": [[0, 0, 0]],
        "orientation": "source lattice vectors 1 and 2 define the coherent in-plane (001) plane",
    },
    "tetragonal": {
        "short_label": "t",
        "space_group_number": 137,
        "space_group_symbol": "P4_2/nmc",
        "source_sha256": "00f700b91decd07961cbb09b7e1cf96c3f2c2412887b26e454eb135958250ab8",
        "source_atoms": 6,
        "supercell_matrix": [[1, 1, 0], [-1, 1, 0], [0, 0, 1]],
        "translation_representatives": [[0, 0, 0], [1, 0, 0]],
        "orientation": (
            "sqrt(2) x sqrt(2) x 1 integer supercell; transformed vectors "
            "[110] and [-110] define the coherent in-plane plane"
        ),
    },
}


PUBLICATION_BLOCKERS = [
    "The manually downloaded crystallographic source DOI/repository record and redistribution licence are not frozen in this package.",
    "The builder checks source symmetry with phase-specific operation/motif contracts, not spglib. The frozen unrelaxed generated POSCAR hashes passed one separate spglib precheck, but every relaxed output still requires repeat independent SG verification.",
    "The common (001) orientation is a preregistered pilot choice and is not yet justified as the experimentally selected epitaxial relationship.",
    "ISIF=2 fixes all lattice vectors. A validated out-of-plane and shear relaxation protocol is required before publication-grade epitaxial energies are claimed.",
    "The factor-1 reference imposes phase-dependent coherent prestrain on m and o; it is not each phase's zero-stress equilibrium state.",
    "POTCAR is deliberately absent. PAW dataset identity and hashes must be frozen on the licensed machine and kept identical across all jobs.",
    "ENCUT, k-point density, smearing, force/stress tolerances, functional, and precision require convergence studies shared across phases.",
    "Three factors (0.99, 1.00, 1.01) are a smoke-test pilot and are insufficient to fit a robust phase-boundary crossing.",
    "Every relaxed structure requires phase-identity, minimum-distance, stress, force, electronic, and ionic convergence checks before energy comparison.",
    "Energies must be normalized to four HfO2 formula units and compared only after identical settings and successful static calculations.",
    "Zero-kelvin DFT trends cannot validate a specific experimental annealing temperature, annealing time, phase fraction, Pr, or 2Pr.",
    "The parent TEFS calculation was a method smoke test and was not a publication-grade polymorph benchmark.",
]


EXTERNAL_SPGLIB_PRECHECK = {
    "status": "passed_for_frozen_unrelaxed_poscar_hashes",
    "performed_by_builder": False,
    "audit_scope": "the nine generated_v0_1 POSCAR files before any VASP execution",
    "audit_date": "2026-08-24",
    "tool": "spglib 2.7.0",
    "symprec": 1e-3,
    "execution_note": "Independent one-time check used an ephemeral /tmp PYTHONPATH and did not modify the original .venv.",
    "phase_results": {
        "monoclinic": {"space_group_symbol": "P2_1/c", "space_group_number": 14, "jobs_passed": 3},
        "orthorhombic": {"space_group_symbol": "Pca2_1", "space_group_number": 29, "jobs_passed": 3},
        "tetragonal": {"space_group_symbol": "P4_2/nmc", "space_group_number": 137, "jobs_passed": 3},
    },
    "reported_minimum_distance_range_A": [1.998, 2.078],
    "audited_poscar_sha256": {
        "monoclinic__biaxial_m0p0100": "3ac1ae7fc68d949c24078df4e73e404badc75bd0c0f11e475ce7e24bfa89e657",
        "monoclinic__biaxial_0p0000": "fe3c64e78ad8ce419eb018e6815f0ce73f646f2ac0bde85ec0c422e6335be9b7",
        "monoclinic__biaxial_p0p0100": "9a19bde609f95f759f2a67cf06c4cfd9267f1d88f4efd16dc716ee4f4f8d0751",
        "orthorhombic__biaxial_m0p0100": "d1b3bca2f87ed8152bfb2df9db937b71094f1529df85214dd429e40edd436645",
        "orthorhombic__biaxial_0p0000": "cc62de76cd0632e57b031ecd4f18fea7b8b2bdb783bcd1caefd2c7d8150acf6a",
        "orthorhombic__biaxial_p0p0100": "b1c369b65089f14864ada73155fd7541bc5c47c624808a368ea46bdf91bbcf1a",
        "tetragonal__biaxial_m0p0100": "c75a1144d4f69c41e1e1baa453a5103e5d554c5d394d6b23b184134e27345d02",
        "tetragonal__biaxial_0p0000": "55cf7a65a7ed2829ef1031953cce0575c61b4f3cf4094e45572fbcfcd072f86b",
        "tetragonal__biaxial_p0p0100": "8a0ffaba82d783856109d033a20863b2fc2f6f169ef4939d10a6ddc346a65b02",
    },
    "applicability_rule": "The audit applies only while every job_id maps to the exact recorded POSCAR SHA-256.",
    "relaxed_output_status": "not_checked; recompute symmetry and minimum distances after every relaxation",
}


def read_poscar(path: str | Path) -> PoscarStructure:
    """Read the conservative VASP-5 Direct-coordinate subset used by the frozen sources."""

    source = Path(path)
    lines = source.read_text(encoding="utf-8").splitlines()
    if len(lines) < 9:
        raise ValueError(f"POSCAR/CONTCAR is too short: {source}")
    try:
        scale_tokens = lines[1].split()
        if len(scale_tokens) != 1:
            raise ValueError("Only one positive POSCAR scale factor is supported")
        scale = float(scale_tokens[0])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid POSCAR scale factor in {source}") from exc
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("Only a finite positive POSCAR scale factor is supported")

    lattice: list[tuple[float, float, float]] = []
    for line in lines[2:5]:
        values = _three_floats(line)
        lattice.append(tuple(scale * value for value in values))
    if _determinant(lattice) <= 0:
        raise ValueError("Lattice must be right-handed with positive volume")

    symbols = tuple(lines[5].split())
    try:
        counts = tuple(int(value) for value in lines[6].split())
    except ValueError as exc:
        raise ValueError("Invalid VASP-5 element counts") from exc
    if not symbols or len(symbols) != len(counts) or any(value <= 0 for value in counts):
        raise ValueError("Element symbols/counts are missing or inconsistent")

    cursor = 7
    if lines[cursor].strip().lower().startswith("s"):
        cursor += 1
    if cursor >= len(lines) or not lines[cursor].strip().lower().startswith("d"):
        raise ValueError("Only Direct fractional coordinates are supported")
    cursor += 1
    atom_count = sum(counts)
    if len(lines) < cursor + atom_count:
        raise ValueError("Coordinate count is smaller than the element-count total")
    coordinates = tuple(
        tuple(_wrap_fractional(value) for value in _three_floats(lines[cursor + index]))
        for index in range(atom_count)
    )
    structure = PoscarStructure(
        comment=lines[0].strip(),
        lattice=tuple(lattice),
        symbols=symbols,
        counts=counts,
        fractional_coordinates=coordinates,
    )
    structure.formula_units
    return structure


def write_poscar(structure: PoscarStructure, path: str | Path, *, comment: str) -> None:
    target = Path(path)
    lines = [comment, "1.0"]
    lines.extend("  " + "  ".join(f"{value:20.14f}" for value in vector) for vector in structure.lattice)
    lines.append("  " + "  ".join(structure.symbols))
    lines.append("  " + "  ".join(str(value) for value in structure.counts))
    lines.append("Direct")
    lines.extend(
        "  " + "  ".join(f"{_wrap_fractional(value):18.14f}" for value in coordinate)
        for coordinate in structure.fractional_coordinates
    )
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_source_structure(phase: str, structure: PoscarStructure) -> dict[str, Any]:
    if phase not in PHASE_SPECS:
        raise ValueError(f"Unsupported phase: {phase}")
    spec = PHASE_SPECS[phase]
    issues: list[str] = []
    if structure.symbols != ("Hf", "O"):
        issues.append("elements_must_be_Hf_then_O")
    if structure.atom_count != int(spec["source_atoms"]):
        issues.append(f"expected_{spec['source_atoms']}_source_atoms")
    try:
        structure.formula_units
    except ValueError:
        issues.append("not_stoichiometric_HfO2")

    a, b, c = structure.lattice
    lengths = [_norm(vector) for vector in structure.lattice]
    angles = {
        "alpha_deg": _angle_degrees(b, c),
        "beta_deg": _angle_degrees(a, c),
        "gamma_deg": _angle_degrees(a, b),
    }
    if abs(angles["gamma_deg"] - 90.0) > 0.05:
        issues.append("source_in_plane_vectors_are_not_orthogonal")
    if phase == "orthorhombic" and any(abs(value - 90.0) > 0.05 for value in angles.values()):
        issues.append("orthorhombic_metric_contract_failed")
    if phase == "tetragonal":
        if abs(lengths[0] - lengths[1]) > 1e-6:
            issues.append("tetragonal_a_b_lengths_differ")
        if any(abs(value - 90.0) > 0.05 for value in angles.values()):
            issues.append("tetragonal_metric_contract_failed")

    if phase in {"monoclinic", "orthorhombic"}:
        operations = _phase_operations(phase)
        if not _operation_closure_holds(structure, operations, tolerance=2e-5):
            issues.append("expected_space_group_operation_closure_failed")
    elif not _tetragonal_motif_holds(structure, tolerance=2e-5):
        issues.append("expected_P4_2_nmc_motif_failed")

    return {
        "status": "verified_phase_specific_contract" if not issues else "failed",
        "expected_space_group_number": spec["space_group_number"],
        "expected_space_group_symbol": spec["space_group_symbol"],
        "verification_method": (
            "phase_specific_operation_closure"
            if phase in {"monoclinic", "orthorhombic"}
            else "P4_2_nmc_metric_and_special_position_motif"
        ),
        "independent_space_group_refinement_performed": False,
        "atom_count": structure.atom_count,
        "formula_units": structure.formula_units if not issues or "not_stoichiometric_HfO2" not in issues else None,
        "lattice_lengths_A": lengths,
        "lattice_angles_deg": angles,
        "volume_A3": _determinant(structure.lattice),
        "issues": issues,
    }


def build_tetragonal_sqrt2_supercell(structure: PoscarStructure) -> PoscarStructure:
    """Apply the frozen det=2 integer transform to the six-atom tetragonal cell."""

    matrix = PHASE_SPECS["tetragonal"]["supercell_matrix"]
    translations = PHASE_SPECS["tetragonal"]["translation_representatives"]
    transformed_lattice = tuple(
        tuple(sum(float(matrix[row][index]) * structure.lattice[index][col] for index in range(3)) for col in range(3))
        for row in range(3)
    )
    inverse = (
        (0.5, -0.5, 0.0),
        (0.5, 0.5, 0.0),
        (0.0, 0.0, 1.0),
    )
    coordinates: list[tuple[float, float, float]] = []
    counts: list[int] = []
    start = 0
    for count in structure.counts:
        species_coordinates = structure.fractional_coordinates[start : start + count]
        for coordinate in species_coordinates:
            for translation in translations:
                shifted = tuple(coordinate[index] + float(translation[index]) for index in range(3))
                mapped = tuple(
                    _wrap_fractional(sum(shifted[index] * inverse[index][col] for index in range(3)))
                    for col in range(3)
                )
                coordinates.append(mapped)
        counts.append(count * len(translations))
        start += count
    result = PoscarStructure(
        comment=structure.comment,
        lattice=transformed_lattice,
        symbols=structure.symbols,
        counts=tuple(counts),
        fractional_coordinates=tuple(coordinates),
    )
    if result.atom_count != TARGET_ATOMS or result.formula_units != 4:
        raise ValueError("Tetragonal supercell did not produce Hf4O8")
    if _minimum_periodic_distance(result) < 1.0:
        raise ValueError("Tetragonal supercell contains overlapping atoms")
    return result


def common_tetragonal_in_plane_reference(structure: PoscarStructure) -> dict[str, Any]:
    supercell = build_tetragonal_sqrt2_supercell(structure)
    a_length = _norm(supercell.lattice[0])
    b_length = _norm(supercell.lattice[1])
    gamma = _angle_degrees(supercell.lattice[0], supercell.lattice[1])
    if abs(a_length - b_length) > 1e-8 or abs(gamma - 90.0) > 1e-8:
        raise ValueError("Tetragonal sqrt(2) x sqrt(2) in-plane cell is not square")
    source_a = _norm(structure.lattice[0])
    return {
        "definition": "actual relaxed tetragonal sqrt(2) x sqrt(2) in-plane vector length",
        "source_tetragonal_a_A": source_a,
        "formula": "sqrt(2) * source_tetragonal_a_A",
        "L_ref_A": a_length,
        "transformed_a_A": a_length,
        "transformed_b_A": b_length,
        "transformed_gamma_deg": gamma,
    }


def build_comparable_structure(
    phase: str,
    source: PoscarStructure,
    *,
    reference_length_A: float,
    strain_factor: float,
) -> tuple[PoscarStructure, dict[str, Any]]:
    if strain_factor not in STRAIN_FACTORS:
        raise ValueError(f"Only the preregistered factors {STRAIN_FACTORS} are allowed")
    base = build_tetragonal_sqrt2_supercell(source) if phase == "tetragonal" else source
    if base.atom_count != TARGET_ATOMS or base.formula_units != 4:
        raise ValueError(f"{phase} comparable cell is not Hf4O8")
    first, second, third = base.lattice
    if abs(_angle_degrees(first, second) - 90.0) > 0.05:
        raise ValueError(f"{phase} selected in-plane vectors are not orthogonal")
    target_length = reference_length_A * strain_factor
    new_first = _scale_to_length(first, target_length)
    new_second = _scale_to_length(second, target_length)
    result = PoscarStructure(
        comment=base.comment,
        lattice=(new_first, new_second, third),
        symbols=base.symbols,
        counts=base.counts,
        fractional_coordinates=base.fractional_coordinates,
    )
    minimum_distance = _minimum_periodic_distance(result)
    if minimum_distance < 1.0:
        raise ValueError(f"{phase} strained structure contains atoms closer than 1.0 A")
    mapping = {
        "integer_supercell_matrix": PHASE_SPECS[phase]["supercell_matrix"],
        "translation_representatives": PHASE_SPECS[phase]["translation_representatives"],
        "source_or_supercell_in_plane_lengths_A": [_norm(first), _norm(second)],
        "source_or_supercell_in_plane_gamma_deg": _angle_degrees(first, second),
        "source_to_target_axis_scale": [target_length / _norm(first), target_length / _norm(second)],
        "lattice_row_scaling_matrix": [
            [target_length / _norm(first), 0.0, 0.0],
            [0.0, target_length / _norm(second), 0.0],
            [0.0, 0.0, 1.0],
        ],
        "target_in_plane_length_A": target_length,
        "target_in_plane_gamma_deg": _angle_degrees(new_first, new_second),
        "out_of_plane_vector_policy": "copied unchanged from the source/comparable supercell",
        "fractional_coordinate_policy": "copied after exact integer-cell remapping; no invented displacement",
        "minimum_periodic_distance_A": minimum_distance,
    }
    return result, mapping


def prepare_phase_strain_jobs(
    source_root: str | Path,
    output_dir: str | Path,
    *,
    allowed_output_root: str | Path = PROJECT_ROOT,
) -> dict[str, Any]:
    """Prepare a no-execution, no-POTCAR HfO2 biaxial-strain pilot package."""

    source_base = Path(source_root).expanduser().resolve()
    target = Path(output_dir).expanduser().resolve()
    allowed = Path(allowed_output_root).expanduser().resolve()
    _require_inside(target, allowed)
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    try:
        result = _prepare_into(source_base, temporary)
        temporary.replace(target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    result["output_dir"] = str(target)
    result["manifest_path"] = str(target / "strain_pilot_manifest.json")
    return result


def _prepare_into(source_root: Path, target: Path) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    source_records: dict[str, dict[str, Any]] = {}
    structures: dict[str, PoscarStructure] = {}
    blockers: list[str] = []
    for phase, spec in PHASE_SPECS.items():
        source_path = source_root / phase / "CONTCAR"
        record: dict[str, Any] = {
            "phase": phase,
            "path": str(source_path),
            "expected_sha256": spec["source_sha256"],
            "expected_space_group_number": spec["space_group_number"],
            "expected_space_group_symbol": spec["space_group_symbol"],
        }
        if not source_path.is_file():
            record["status"] = "missing"
            blockers.append(f"missing_source_CONTCAR:{phase}")
            source_records[phase] = record
            continue
        source_hash = _sha256_file(source_path)
        record["sha256"] = source_hash
        if source_hash != spec["source_sha256"]:
            record["status"] = "hash_mismatch"
            blockers.append(f"frozen_source_hash_mismatch:{phase}")
            source_records[phase] = record
            continue
        try:
            structure = read_poscar(source_path)
            verification = validate_source_structure(phase, structure)
        except (OSError, ValueError) as exc:
            record["status"] = "invalid"
            record["error"] = str(exc)
            blockers.append(f"invalid_source_structure:{phase}")
            source_records[phase] = record
            continue
        record["verification"] = verification
        if verification["status"] != "verified_phase_specific_contract":
            record["status"] = "symmetry_contract_failed"
            blockers.append(f"source_symmetry_contract_failed:{phase}")
        else:
            record["status"] = "verified"
            structures[phase] = structure
        source_records[phase] = record

    manifest: dict[str, Any] = {
        "builder_version": BUILDER_VERSION,
        "generated_at_utc": generated_at,
        "scope": "preregistered three-point HfO2 biaxial-strain dry-run pilot",
        "execution_authorized": False,
        "vasp_invoked": False,
        "cloud_job_submitted": False,
        "database_read": False,
        "dotenv_read": False,
        "potcar_included": False,
        "publication_ready": False,
        "source_root": str(source_root),
        "source_structures": source_records,
        "frozen_phase_contract": {
            phase: {
                "space_group_number": spec["space_group_number"],
                "space_group_symbol": spec["space_group_symbol"],
                "target_atoms": TARGET_ATOMS,
                "target_formula_units": 4,
                "orientation": spec["orientation"],
                "integer_supercell_matrix": spec["supercell_matrix"],
                "translation_representatives": spec["translation_representatives"],
            }
            for phase, spec in PHASE_SPECS.items()
        },
        "strain_contract": {
            "mode": "square_in_plane_biaxial",
            "factors": list(STRAIN_FACTORS),
            "engineering_strain": [round(value - 1.0, 8) for value in STRAIN_FACTORS],
            "orientation": "first two vectors of each comparable cell",
            "constraint_strategy": "fixed_all_lattice_vectors_ionic_relaxation_ISIF_2",
            "relaxed_degrees_of_freedom": "ions_only",
            "fixed_degrees_of_freedom": "all lattice vectors, including the imposed square in-plane vectors",
            "pilot_boundary": "No result from this package exists until separately executed and validated.",
        },
        "publication_blockers": PUBLICATION_BLOCKERS,
        "external_preparation_audit": EXTERNAL_SPGLIB_PRECHECK,
        "preparation_blockers": blockers,
        "jobs": [],
    }

    if blockers:
        manifest["status"] = "blocked_contract_only"
        manifest["geometry_generated"] = False
        _write_root_artifacts(target, manifest)
        return _summary(manifest)

    reference = common_tetragonal_in_plane_reference(structures["tetragonal"])
    manifest["strain_contract"]["reference"] = reference
    jobs: list[dict[str, Any]] = []
    for phase in PHASE_SPECS:
        for factor in STRAIN_FACTORS:
            strained, mapping = build_comparable_structure(
                phase,
                structures[phase],
                reference_length_A=float(reference["L_ref_A"]),
                strain_factor=factor,
            )
            strain = round(factor - 1.0, 8)
            job_id = f"{phase}__{_strain_slug(strain)}"
            job_dir = target / "jobs" / phase / _strain_slug(strain)
            job_dir.mkdir(parents=True, exist_ok=True)
            poscar_path = job_dir / "POSCAR"
            write_poscar(
                strained,
                poscar_path,
                comment=(
                    f"HfO2 {phase} SG#{PHASE_SPECS[phase]['space_group_number']} "
                    f"12-atom biaxial factor={factor:.5f} dry-run"
                ),
            )
            _write_vasp_templates(job_dir, phase=phase, factor=factor)
            poscar_hash = _sha256_file(poscar_path)
            job = {
                "job_id": job_id,
                "phase": phase,
                "expected_space_group_number": PHASE_SPECS[phase]["space_group_number"],
                "expected_space_group_symbol": PHASE_SPECS[phase]["space_group_symbol"],
                "source_sha256": PHASE_SPECS[phase]["source_sha256"],
                "poscar_relative_path": str(poscar_path.relative_to(target)),
                "poscar_sha256": poscar_hash,
                "atoms": strained.atom_count,
                "formula_units": strained.formula_units,
                "strain_factor": factor,
                "engineering_strain": strain,
                "mapping": mapping,
                "constraint_strategy": manifest["strain_contract"]["constraint_strategy"],
                "execution_status": "prepared_not_executed",
                "publication_ready": False,
                "publication_blockers": PUBLICATION_BLOCKERS,
            }
            (job_dir / "job_manifest.json").write_text(
                json.dumps(job, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            jobs.append(job)

    manifest["jobs"] = jobs
    manifest["status"] = "prepared_dry_run_not_executed"
    manifest["geometry_generated"] = True
    manifest["job_count"] = len(jobs)
    _write_root_artifacts(target, manifest)
    return _summary(manifest)


def _write_root_artifacts(target: Path, manifest: dict[str, Any]) -> None:
    (target / "strain_pilot_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (target / "publication_blockers.json").write_text(
        json.dumps(
            {
                "publication_ready": False,
                "blockers": PUBLICATION_BLOCKERS,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    with (target / "results_template.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "job_id",
                "phase",
                "strain_factor",
                "engineering_strain",
                "target_in_plane_length_A",
                "total_energy_eV",
                "formula_units",
                "energy_eV_per_fu",
                "relative_energy_meV_per_fu",
                "max_force_eV_A",
                "in_plane_stress_kB",
                "out_of_plane_stress_kB",
                "electronic_converged",
                "ionic_converged",
                "post_relax_space_group",
                "phase_identity_status",
                "quality_status",
                "notes",
            ]
        )
        for job in manifest.get("jobs", []):
            writer.writerow(
                [
                    job["job_id"],
                    job["phase"],
                    job["strain_factor"],
                    job["engineering_strain"],
                    job["mapping"]["target_in_plane_length_A"],
                    "",
                    4,
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "not_run",
                    "dry_run_no_result",
                    "",
                ]
            )
    readme_lines = [
        "# Generated HfO2 biaxial-strain pilot",
        "",
        f"Status: `{manifest['status']}`",
        "",
        "This directory is a dry-run input contract. It has not run or submitted VASP.",
        "No POTCAR, API key, database content, or `.env` content is included.",
        "",
        "## Frozen pilot",
        "",
        "- phases: m P2_1/c #14, o Pca2_1 #29, t P4_2/nmc #137",
        "- comparable size: Hf4O8, 12 atoms, four formula units",
        "- factors: 0.99, 1.00, 1.01",
        "- common square in-plane reference: actual relaxed tetragonal sqrt(2) x sqrt(2) length",
        "- relaxation template: fixed cell, ions only (`ISIF = 2`)",
        "",
        "## Independent pre-execution audit",
        "",
        "The builder itself does not call spglib. A separate one-time spglib 2.7.0 check",
        "at symprec=1e-3 found m #14, o #29, and t #137 for all nine frozen",
        "unrelaxed POSCAR hashes; reported minimum distances span about 1.998-2.078 A.",
        "This audit is hash-bound and does not verify any future relaxed output.",
        "",
        "Review `strain_pilot_manifest.json` and every publication blocker before any licensed-machine execution.",
    ]
    (target / "README_GENERATED.md").write_text("\n".join(readme_lines) + "\n", encoding="utf-8")


def _write_vasp_templates(job_dir: Path, *, phase: str, factor: float) -> None:
    (job_dir / "INCAR.relax").write_text(
        "\n".join(
            [
                f"SYSTEM = HfO2 {phase} biaxial pilot factor {factor:.5f} - ions only",
                "PREC = Accurate",
                "ENCUT = 520",
                "EDIFF = 1E-6",
                "EDIFFG = -0.02",
                "ISMEAR = 0",
                "SIGMA = 0.05",
                "IBRION = 2",
                "ISIF = 2",
                "NSW = 120",
                "ISYM = 0",
                "LREAL = Auto",
                "LASPH = .TRUE.",
                "ADDGRID = .TRUE.",
                "LWAVE = .FALSE.",
                "LCHARG = .FALSE.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (job_dir / "INCAR.static").write_text(
        "\n".join(
            [
                f"SYSTEM = HfO2 {phase} biaxial pilot factor {factor:.5f} - static",
                "PREC = Accurate",
                "ENCUT = 520",
                "EDIFF = 1E-7",
                "ISMEAR = 0",
                "SIGMA = 0.05",
                "IBRION = -1",
                "NSW = 0",
                "ISTART = 0",
                "ICHARG = 2",
                "ISYM = 0",
                "LREAL = .FALSE.",
                "LASPH = .TRUE.",
                "ADDGRID = .TRUE.",
                "LWAVE = .FALSE.",
                "LCHARG = .FALSE.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (job_dir / "KPOINTS").write_text(
        "Automatic common 12-atom-cell mesh (pilot; convergence not yet established)\n"
        "0\nGamma\n6 6 6\n0 0 0\n",
        encoding="utf-8",
    )
    (job_dir / "POTCAR_NOT_INCLUDED.txt").write_text(
        "POTCAR is intentionally absent. Supply one licensed, identical PAW dataset stack "
        "on the compute machine only after method review.\n",
        encoding="utf-8",
    )


def _summary(manifest: dict[str, Any]) -> dict[str, Any]:
    reference = manifest.get("strain_contract", {}).get("reference", {})
    return {
        "builder_version": BUILDER_VERSION,
        "status": manifest["status"],
        "geometry_generated": bool(manifest.get("geometry_generated")),
        "jobs": len(manifest.get("jobs", [])),
        "reference_length_A": reference.get("L_ref_A"),
        "preparation_blockers": list(manifest.get("preparation_blockers", [])),
        "publication_ready": False,
        "execution_authorized": False,
    }


def _phase_operations(phase: str) -> tuple[tuple[tuple[tuple[int, int, int], ...], tuple[float, float, float]], ...]:
    identity = (((1, 0, 0), (0, 1, 0), (0, 0, 1)), (0.0, 0.0, 0.0))
    if phase == "monoclinic":
        return (
            identity,
            (((-1, 0, 0), (0, 1, 0), (0, 0, -1)), (0.0, 0.5, 0.5)),
            (((-1, 0, 0), (0, -1, 0), (0, 0, -1)), (0.0, 0.0, 0.0)),
            (((1, 0, 0), (0, -1, 0), (0, 0, 1)), (0.0, 0.5, 0.5)),
        )
    if phase == "orthorhombic":
        return (
            identity,
            (((1, 0, 0), (0, 1, 0), (0, 0, -1)), (0.0, 0.5, 0.5)),
            (((-1, 0, 0), (0, 1, 0), (0, 0, 1)), (0.0, 0.0, 0.5)),
            (((-1, 0, 0), (0, 1, 0), (0, 0, -1)), (0.0, 0.5, 0.0)),
        )
    raise ValueError(f"No operation contract for phase: {phase}")


def _operation_closure_holds(
    structure: PoscarStructure,
    operations: Sequence[tuple[Sequence[Sequence[int]], Sequence[float]]],
    *,
    tolerance: float,
) -> bool:
    for positions in structure.coordinates_by_species().values():
        for coordinate in positions:
            for rotation, translation in operations:
                transformed = tuple(
                    _wrap_fractional(
                        sum(float(rotation[row][col]) * coordinate[col] for col in range(3))
                        + float(translation[row])
                    )
                    for row in range(3)
                )
                if not any(_periodic_coordinate_distance(transformed, other) <= tolerance for other in positions):
                    return False
    return True


def _tetragonal_motif_holds(structure: PoscarStructure, *, tolerance: float) -> bool:
    positions = structure.coordinates_by_species()
    if set(positions) != {"Hf", "O"} or len(positions["Hf"]) != 2 or len(positions["O"]) != 4:
        return False
    expected_hf = ((0.0, 0.0, 0.0), (0.5, 0.5, 0.5))
    if any(not any(_periodic_coordinate_distance(target, actual) <= tolerance for actual in positions["Hf"]) for target in expected_hf):
        return False
    xy_a = [value[2] for value in positions["O"] if _periodic_coordinate_distance(value[:2], (0.0, 0.5)) <= tolerance]
    xy_b = [value[2] for value in positions["O"] if _periodic_coordinate_distance(value[:2], (0.5, 0.0)) <= tolerance]
    if len(xy_a) != 2 or len(xy_b) != 2:
        return False
    for z_value in xy_a:
        expected_a = (z_value, _wrap_fractional(z_value + 0.5))
        expected_b = (_wrap_fractional(0.5 - z_value), _wrap_fractional(1.0 - z_value))
        if _sets_periodically_match(expected_a, xy_a, tolerance) and _sets_periodically_match(expected_b, xy_b, tolerance):
            return True
    return False


def _sets_periodically_match(expected: Iterable[float], actual: Iterable[float], tolerance: float) -> bool:
    remaining = list(actual)
    for value in expected:
        for index, candidate in enumerate(remaining):
            if abs(((value - candidate + 0.5) % 1.0) - 0.5) <= tolerance:
                remaining.pop(index)
                break
        else:
            return False
    return not remaining


def _minimum_periodic_distance(structure: PoscarStructure) -> float:
    minimum = math.inf
    coordinates = structure.fractional_coordinates
    for index, first in enumerate(coordinates):
        for second in coordinates[index + 1 :]:
            delta = tuple(((first[axis] - second[axis] + 0.5) % 1.0) - 0.5 for axis in range(3))
            cartesian = tuple(
                sum(delta[row] * structure.lattice[row][col] for row in range(3))
                for col in range(3)
            )
            minimum = min(minimum, _norm(cartesian))
    return minimum


def _periodic_coordinate_distance(first: Sequence[float], second: Sequence[float]) -> float:
    return max(abs(((float(a) - float(b) + 0.5) % 1.0) - 0.5) for a, b in zip(first, second))


def _scale_to_length(vector: Sequence[float], target: float) -> tuple[float, float, float]:
    length = _norm(vector)
    if length <= 0 or not math.isfinite(target) or target <= 0:
        raise ValueError("Cannot scale a zero vector or to a non-positive length")
    return tuple(float(value) * target / length for value in vector)


def _norm(vector: Sequence[float]) -> float:
    return math.sqrt(sum(float(value) ** 2 for value in vector))


def _angle_degrees(first: Sequence[float], second: Sequence[float]) -> float:
    denominator = _norm(first) * _norm(second)
    if denominator <= 0:
        raise ValueError("Cannot compute an angle involving a zero vector")
    cosine = sum(float(a) * float(b) for a, b in zip(first, second)) / denominator
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def _determinant(matrix: Sequence[Sequence[float]]) -> float:
    a, b, c = matrix
    return (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )


def _three_floats(line: str) -> tuple[float, float, float]:
    tokens = line.split()
    if len(tokens) < 3:
        raise ValueError(f"Expected three numeric fields: {line!r}")
    try:
        values = tuple(float(value) for value in tokens[:3])
    except ValueError as exc:
        raise ValueError(f"Expected three numeric fields: {line!r}") from exc
    if not all(math.isfinite(value) for value in values):
        raise ValueError("POSCAR contains non-finite numeric values")
    return values  # type: ignore[return-value]


def _wrap_fractional(value: float) -> float:
    wrapped = float(value) % 1.0
    return 0.0 if abs(wrapped - 1.0) < 1e-12 or abs(wrapped) < 1e-12 else wrapped


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strain_slug(strain: float) -> str:
    if abs(strain) < 5e-12:
        return "biaxial_0p0000"
    prefix = "p" if strain > 0 else "m"
    return f"biaxial_{prefix}{abs(strain):.4f}".replace(".", "p")


def _require_inside(path: Path, root: Path) -> None:
    if path == root or root not in path.parents:
        raise ValueError(f"Output must be a child of the allowed root: {root}")
