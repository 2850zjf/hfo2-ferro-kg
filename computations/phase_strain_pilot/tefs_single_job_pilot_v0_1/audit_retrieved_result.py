#!/usr/bin/env python3
"""Fail-closed, read-only audit for the extracted TEFS single-job result.

The auditor never extracts an archive, writes a report, reads POTCAR, contacts
TEFS, or opens a database.  It compares the returned checksum manifest with the
locally retained upload bundle and emits one JSON document to stdout.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
import re
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Any


PILOT_DIR = Path(__file__).resolve().parent
DEFAULT_UPLOAD_BUNDLE = (
    PILOT_DIR / "output" / "hfo2_m_biaxial_0p0000_workflow_smoke.tar.gz"
)
EXPECTED_JOB_ID = "monoclinic__biaxial_0p0000"
EXPECTED_POSCAR_SHA256 = (
    "fe3c64e78ad8ce419eb018e6815f0ce73f646f2ac0bde85ec0c422e6335be9b7"
)
EXPECTED_UPLOAD_BUNDLE_SHA256 = (
    "5298d29a898f2b70c520d73850410f6935cacf5a4687f090412f00a030659219"
)
EXPECTED_TEFS_DESCRIPTOR = {
    "node": "1",
    "instance_type": "S6.4XLARGE32",
    "image_id": "img-45t2j6r7",
    "region": "ap-beijing",
    "spot_paid": False,
    "public_ip": False,
    "disk_size": "50",
    "ntasks_per_node": "16",
    "cmd": (
        "command -v timeout >/dev/null && exec timeout "
        "--signal=TERM --kill-after=60s 30m bash ./run_vasp_two_step.sh"
    ),
}
EXPECTED_SPECIES = ["Hf", "O"]
EXPECTED_COUNTS = [4, 8]
EXPECTED_ATOMS = 12
FIXED_CELL_TOLERANCE_A = 1.0e-8
FOOTER_MARKER = "General timing and accounting informations"
RELAX_ACCURACY_MARKER = "reached required accuracy"
STATIC_EDIFF_MARKER = "aborting loop because EDIFF is reached"
FATAL_MARKERS = {
    "BRMIX": re.compile(r"BRMIX", re.IGNORECASE),
    "EDDDAV": re.compile(r"EDDDAV", re.IGNORECASE),
    "VERY_BAD_NEWS": re.compile(r"VERY BAD NEWS", re.IGNORECASE),
    "INTERNAL_ERROR": re.compile(r"internal error", re.IGNORECASE),
    "INCOMPLETE_CHARGE_DENSITY": re.compile(
        r"chargedensity file is incomplete|charge density.*incomplete",
        re.IGNORECASE,
    ),
    "ZHEGV_FAILED": re.compile(r"ZHEGV.*failed", re.IGNORECASE),
    "MPI_ABORT": re.compile(r"MPI[_\s-]*ABORT", re.IGNORECASE),
    "SEGMENTATION_FAULT": re.compile(r"segmentation fault", re.IGNORECASE),
}
TOTEN_RE = re.compile(
    r"free\s+energy\s+TOTEN\s*=\s*([-+0-9.eEdD]+)\s+eV",
    re.IGNORECASE,
)
OSZICAR_E0_RE = re.compile(r"\bE0=\s*([-+0-9.eEdD]+)")
CHECKSUM_RE = re.compile(r"^([0-9a-fA-F]{64}) [ *](.+)$")
RETURNED_CHECKSUM_PATHS = {
    "POSCAR": Path("inputs/POSCAR.original"),
    "KPOINTS": Path("inputs/KPOINTS"),
    "INCAR.relax": Path("inputs/INCAR.relax"),
    "INCAR.static": Path("inputs/INCAR.static"),
    "job_manifest.json": Path("job_manifest.json"),
    "run_vasp_two_step.sh": Path("run_vasp_two_step.sh"),
    "tefs_hpc.preview.json": Path("tefs_hpc.preview.json"),
}
EXPECTED_NOT_RETURNED = {"prepare_licensed_potcar.sh"}


class AuditCollector:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []
        self.failures: list[str] = []

    def check(self, code: str, passed: bool, **details: Any) -> bool:
        item: dict[str, Any] = {"code": code, "passed": bool(passed)}
        if details:
            item["details"] = details
        self.checks.append(item)
        if not passed:
            self.failures.append(code)
        return bool(passed)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _float(text: str) -> float:
    return float(text.replace("D", "E").replace("d", "e"))


def _parse_checksums(text: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip("\n")
        if not line:
            continue
        match = CHECKSUM_RE.fullmatch(line)
        if not match:
            raise ValueError(f"malformed checksum line {line_number}")
        digest, raw_name = match.groups()
        name = raw_name[1:] if raw_name.startswith("*") else raw_name
        posix_name = PurePosixPath(name)
        if posix_name.is_absolute() or ".." in posix_name.parts or name in parsed:
            raise ValueError(f"unsafe or duplicate checksum path on line {line_number}")
        if posix_name.name.upper() == "POTCAR":
            raise ValueError("checksum manifest references POTCAR")
        parsed[name] = digest.lower()
    if not parsed:
        raise ValueError("empty checksum manifest")
    return parsed


def _trusted_upload_checksums(bundle_path: Path) -> tuple[dict[str, str], dict[str, Any]]:
    if bundle_path.is_symlink() or not bundle_path.is_file():
        raise ValueError("trusted upload bundle is missing or is a symlink")
    sidecar = Path(f"{bundle_path}.sha256")
    if sidecar.is_symlink() or not sidecar.is_file():
        raise ValueError("trusted upload bundle SHA-256 sidecar is missing or is a symlink")
    sidecar_fields = _read_text(sidecar).strip().split()
    if not sidecar_fields or not re.fullmatch(r"[0-9a-fA-F]{64}", sidecar_fields[0]):
        raise ValueError("trusted upload bundle sidecar is malformed")
    expected_bundle_sha = sidecar_fields[0].lower()
    actual_bundle_sha = _sha256_file(bundle_path)
    if actual_bundle_sha != expected_bundle_sha:
        raise ValueError("trusted upload bundle does not match its SHA-256 sidecar")
    if actual_bundle_sha != EXPECTED_UPLOAD_BUNDLE_SHA256:
        raise ValueError("trusted upload bundle does not match the frozen reviewed SHA-256")

    with tarfile.open(bundle_path, "r:gz") as archive:
        checksum_members: list[tarfile.TarInfo] = []
        descriptor_members: list[tarfile.TarInfo] = []
        for member in archive.getmembers():
            name = PurePosixPath(member.name)
            if (
                name.is_absolute()
                or ".." in name.parts
                or member.issym()
                or member.islnk()
                or member.isdev()
                or member.isfifo()
            ):
                raise ValueError("trusted upload bundle contains an unsafe member")
            if name.name.upper() == "POTCAR":
                raise ValueError("trusted upload bundle contains POTCAR")
            if name.name == "INPUT_SHA256SUMS.txt" and member.isfile():
                checksum_members.append(member)
            if name.name == "tefs_hpc.preview.json" and member.isfile():
                descriptor_members.append(member)
        if len(checksum_members) != 1:
            raise ValueError("trusted upload bundle must contain one checksum manifest")
        if len(descriptor_members) != 1:
            raise ValueError("trusted upload bundle must contain one TEFS descriptor")
        stream = archive.extractfile(checksum_members[0])
        if stream is None:
            raise ValueError("trusted checksum manifest cannot be read")
        checksum_text = stream.read().decode("utf-8")
        descriptor_stream = archive.extractfile(descriptor_members[0])
        if descriptor_stream is None:
            raise ValueError("trusted TEFS descriptor cannot be read")
        try:
            descriptor = json.loads(descriptor_stream.read().decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("trusted TEFS descriptor is malformed") from exc
        if descriptor != EXPECTED_TEFS_DESCRIPTOR:
            raise ValueError("trusted TEFS descriptor is not the frozen reviewed payload")

    return _parse_checksums(checksum_text), {
        "path": str(bundle_path),
        "sha256": actual_bundle_sha,
        "frozen_expected_sha256": EXPECTED_UPLOAD_BUNDLE_SHA256,
        "sidecar": str(sidecar),
    }


def _required_file(
    collector: AuditCollector, root: Path, relative_path: str | Path
) -> Path | None:
    relative = Path(relative_path)
    path = root / relative
    ok = not path.is_symlink() and path.is_file() and path.stat().st_size > 0
    collector.check(
        f"required_nonempty_file:{relative.as_posix()}",
        ok,
        relative_path=relative.as_posix(),
        size_bytes=path.stat().st_size if ok else None,
    )
    return path if ok else None


def _scan_tree(root: Path) -> tuple[list[str], list[str], list[str]]:
    potcars: list[str] = []
    symlinks: list[str] = []
    walk_errors: list[str] = []

    def onerror(error: OSError) -> None:
        walk_errors.append(str(error))

    for directory, dirnames, filenames in os.walk(root, followlinks=False, onerror=onerror):
        base = Path(directory)
        for name in [*dirnames, *filenames]:
            path = base / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                symlinks.append(relative)
            if name.upper() == "POTCAR":
                potcars.append(relative)
    return sorted(potcars), sorted(symlinks), walk_errors


def _parse_poscar(path: Path) -> dict[str, Any]:
    lines = _read_text(path).splitlines()
    if len(lines) < 8:
        raise ValueError("POSCAR has fewer than eight lines")
    scale_fields = lines[1].split()
    if len(scale_fields) != 1:
        raise ValueError("only a single positive POSCAR scale is accepted")
    scale = _float(scale_fields[0])
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("POSCAR scale must be positive and finite")
    lattice = []
    for line in lines[2:5]:
        values = [_float(value) * scale for value in line.split()]
        if len(values) != 3 or not all(math.isfinite(value) for value in values):
            raise ValueError("invalid POSCAR lattice vector")
        lattice.append(values)
    species = lines[5].split()
    try:
        counts = [int(value) for value in lines[6].split()]
    except ValueError as exc:
        raise ValueError("invalid POSCAR atom counts") from exc
    if len(species) != len(counts) or any(value <= 0 for value in counts):
        raise ValueError("POSCAR species/count mismatch")
    cursor = 7
    if lines[cursor].strip().lower().startswith("s"):
        cursor += 1
    if cursor >= len(lines) or not lines[cursor].strip().lower().startswith("d"):
        raise ValueError("this audit requires Direct coordinates")
    cursor += 1
    atoms = sum(counts)
    if len(lines) < cursor + atoms:
        raise ValueError("POSCAR coordinate list is incomplete")
    positions: list[list[float]] = []
    for line in lines[cursor : cursor + atoms]:
        values = [_float(value) for value in line.split()[:3]]
        if len(values) != 3 or not all(math.isfinite(value) for value in values):
            raise ValueError("invalid POSCAR coordinate")
        positions.append(values)
    return {
        "species": species,
        "counts": counts,
        "atoms": atoms,
        "lattice_A": lattice,
        "fractional_positions": positions,
    }


def _maximum_lattice_delta(left: dict[str, Any], right: dict[str, Any]) -> float:
    return max(
        abs(left["lattice_A"][row][column] - right["lattice_A"][row][column])
        for row in range(3)
        for column in range(3)
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_vasprun(path: Path, expected_atoms: int) -> dict[str, Any]:
    tree = ET.parse(path)
    calculations = [
        element for element in tree.getroot().iter() if _local_name(element.tag) == "calculation"
    ]
    if not calculations:
        raise ValueError("vasprun.xml contains no calculation block")
    calculation = calculations[-1]

    varrays: dict[str, list[list[float]]] = {}
    for element in calculation.iter():
        if _local_name(element.tag) != "varray":
            continue
        name = element.attrib.get("name")
        if name not in {"forces", "stress"}:
            continue
        rows: list[list[float]] = []
        for vector in element:
            if _local_name(vector.tag) != "v" or not vector.text:
                continue
            values = [_float(value) for value in vector.text.split()]
            if len(values) != 3 or not all(math.isfinite(value) for value in values):
                raise ValueError(f"invalid {name} vector in vasprun.xml")
            rows.append(values)
        varrays[name] = rows

    forces = varrays.get("forces", [])
    stress = varrays.get("stress", [])
    if len(forces) != expected_atoms:
        raise ValueError(f"final force vector count is {len(forces)}, expected {expected_atoms}")
    if len(stress) != 3:
        raise ValueError("final stress matrix is not 3x3")
    max_force = max(math.sqrt(sum(component * component for component in row)) for row in forces)

    xml_energies: list[float] = []
    for element in calculation.iter():
        if (
            _local_name(element.tag) == "i"
            and element.attrib.get("name") == "e_fr_energy"
            and element.text
        ):
            xml_energies.append(_float(element.text.strip()))
    return {
        "calculation_blocks": len(calculations),
        "final_force_vectors": len(forces),
        "max_force_norm_eV_per_A": max_force,
        "final_stress_matrix_kB": stress,
        "final_free_energy_eV": xml_energies[-1] if xml_energies else None,
    }


def _stage_metrics(
    collector: AuditCollector, root: Path, stage: str
) -> tuple[dict[str, Any], dict[str, Path]]:
    stdout_name = f"{stage}.output"
    required_names = [
        "OUTCAR",
        "OSZICAR",
        "vasprun.xml",
        stdout_name,
        "INCAR",
        "POSCAR",
        "KPOINTS",
    ]
    if stage == "relax":
        required_names.append("CONTCAR")
    paths = {
        name: _required_file(collector, root, Path("results") / stage / name)
        for name in required_names
    }
    valid_paths = {name: path for name, path in paths.items() if path is not None}
    metrics: dict[str, Any] = {
        "stage": stage,
        "zero_pressure_threshold_applied": False,
        "numeric_force_threshold_applied_by_auditor": False,
    }
    if not all(name in valid_paths for name in ("OUTCAR", "OSZICAR", "vasprun.xml", stdout_name)):
        return metrics, valid_paths

    outcar_text = _read_text(valid_paths["OUTCAR"])
    stdout_text = _read_text(valid_paths[stdout_name])
    oszicar_text = _read_text(valid_paths["OSZICAR"])

    collector.check(
        f"{stage}:outcar_completion_footer",
        FOOTER_MARKER in outcar_text,
    )
    if stage == "relax":
        collector.check(
            "relax:required_accuracy_marker",
            RELAX_ACCURACY_MARKER in stdout_text,
        )
    else:
        collector.check(
            "static:ediff_marker",
            STATIC_EDIFF_MARKER in outcar_text,
        )

    fatal_hits: list[dict[str, Any]] = []
    for file_name, text in ((stdout_name, stdout_text), ("OUTCAR", outcar_text)):
        for marker, pattern in FATAL_MARKERS.items():
            count = len(pattern.findall(text))
            if count:
                fatal_hits.append({"file": file_name, "marker": marker, "count": count})
    collector.check(f"{stage}:no_fatal_markers", not fatal_hits, hits=fatal_hits)

    zbrent_by_file = {
        stdout_name: len(re.findall(r"\bZBRENT\b", stdout_text, re.IGNORECASE)),
        "OUTCAR": len(re.findall(r"\bZBRENT\b", outcar_text, re.IGNORECASE)),
    }
    metrics["zbrent"] = {
        "count": sum(zbrent_by_file.values()),
        "by_file": zbrent_by_file,
        "fatal_by_itself": False,
    }

    toten_values = [_float(value) for value in TOTEN_RE.findall(outcar_text)]
    collector.check(f"{stage}:final_toten_present", bool(toten_values))
    metrics["final_toten_eV"] = toten_values[-1] if toten_values else None
    oszicar_values = [_float(value) for value in OSZICAR_E0_RE.findall(oszicar_text)]
    metrics["oszicar_final_e0_eV"] = oszicar_values[-1] if oszicar_values else None

    try:
        xml_metrics = _parse_vasprun(valid_paths["vasprun.xml"], EXPECTED_ATOMS)
    except (ET.ParseError, OSError, ValueError) as exc:
        collector.check(f"{stage}:vasprun_xml_parseable", False, error=str(exc))
    else:
        collector.check(f"{stage}:vasprun_xml_parseable", True)
        metrics.update(xml_metrics)
    return metrics, valid_paths


def _optional_space_group(structure: dict[str, Any], symprec: float) -> dict[str, Any]:
    base: dict[str, Any] = {
        "informational_only": True,
        "phase_identity_claim": False,
        "symprec_A": symprec,
    }
    try:
        spglib = importlib.import_module("spglib")
    except ImportError:
        return {**base, "status": "not_available"}

    atomic_numbers = {"Hf": 72, "O": 8}
    numbers: list[int] = []
    try:
        for species, count in zip(structure["species"], structure["counts"], strict=True):
            numbers.extend([atomic_numbers[species]] * count)
        dataset = spglib.get_symmetry_dataset(
            (
                structure["lattice_A"],
                structure["fractional_positions"],
                numbers,
            ),
            symprec=symprec,
        )
        if dataset is None:
            return {
                **base,
                "status": "not_resolved",
                "spglib_version": getattr(spglib, "__version__", "unknown"),
            }
        def get_value(name: str) -> Any:
            if hasattr(dataset, name):
                return getattr(dataset, name)
            return dataset[name]

        return {
            **base,
            "status": "reported_not_validated",
            "spglib_version": getattr(spglib, "__version__", "unknown"),
            "number": int(get_value("number")),
            "international_symbol": str(get_value("international")),
            "hall_symbol": str(get_value("hall")),
        }
    except Exception as exc:  # Optional diagnostic must not become a scientific claim.
        return {**base, "status": "error", "error": str(exc)}


def audit_retrieved_result(
    result_root: Path,
    *,
    expected_upload_bundle: Path = DEFAULT_UPLOAD_BUNDLE,
    symprec: float = 1.0e-3,
) -> dict[str, Any]:
    collector = AuditCollector()
    root = Path(result_root).absolute()
    report: dict[str, Any] = {
        "schema_version": "tefs-single-job-result-audit-v0.1",
        "audit_mode": "read_only_fail_closed",
        "result_root": str(root),
        "scientific_claim_allowed": False,
        "publication_ready": False,
        "interpretation_limits": [
            "This is a workflow smoke result, not a phase-boundary result.",
            "No zero-pressure stress threshold is applied.",
            "ZBRENT occurrences are counted as diagnostics and are not fatal by themselves.",
            "Any optional spglib label is informational and is not a phase-identity claim.",
        ],
    }
    if symprec <= 0 or not math.isfinite(symprec):
        collector.check("valid_symprec", False, value=symprec)
        report.update(status="fail", failures=collector.failures, checks=collector.checks)
        return report
    if root.is_symlink() or not root.is_dir():
        collector.check("result_root_is_real_directory", False)
        report.update(status="fail", failures=collector.failures, checks=collector.checks)
        return report
    collector.check("result_root_is_real_directory", True)

    potcars, symlinks, walk_errors = _scan_tree(root)
    collector.check("no_potcar", not potcars, paths=potcars)
    collector.check("no_symlinks", not symlinks, paths=symlinks)
    collector.check("tree_scan_complete", not walk_errors, errors=walk_errors)

    try:
        trusted_checksums, trusted_bundle = _trusted_upload_checksums(
            Path(expected_upload_bundle).absolute()
        )
    except (OSError, tarfile.TarError, UnicodeError, ValueError) as exc:
        collector.check("trusted_upload_bundle_verified", False, error=str(exc))
        trusted_checksums = {}
        trusted_bundle = {"path": str(Path(expected_upload_bundle).absolute())}
    else:
        collector.check("trusted_upload_bundle_verified", True)
    report["trusted_upload_bundle"] = trusted_bundle

    status_path = _required_file(collector, root, "result_status.txt")
    if status_path:
        status_lines = [line.strip() for line in _read_text(status_path).splitlines() if line.strip()]
        exit_lines = [line for line in status_lines if line.startswith("exit_code=")]
        collector.check(
            "result_status_success",
            "success: relax and static completed" in status_lines,
            lines=status_lines,
        )
        collector.check("result_status_exit_code_zero", exit_lines == ["exit_code=0"], lines=exit_lines)

    checksum_path = _required_file(collector, root, "INPUT_SHA256SUMS.txt")
    returned_checksums: dict[str, str] = {}
    if checksum_path:
        try:
            returned_checksums = _parse_checksums(_read_text(checksum_path))
        except ValueError as exc:
            collector.check("returned_checksum_manifest_parseable", False, error=str(exc))
        else:
            collector.check("returned_checksum_manifest_parseable", True)
            collector.check(
                "returned_checksum_manifest_matches_upload",
                bool(trusted_checksums) and returned_checksums == trusted_checksums,
            )

    for name, relative_path in RETURNED_CHECKSUM_PATHS.items():
        path = _required_file(collector, root, relative_path)
        if path and name in returned_checksums:
            actual = _sha256_file(path)
            collector.check(
                f"returned_input_checksum:{name}",
                actual == returned_checksums[name],
                expected=returned_checksums[name],
                actual=actual,
                returned_path=relative_path.as_posix(),
            )
        elif name not in returned_checksums:
            collector.check(f"returned_input_checksum_entry:{name}", False)
    unexpected_unmapped = set(returned_checksums) - set(RETURNED_CHECKSUM_PATHS) - EXPECTED_NOT_RETURNED
    collector.check(
        "checksum_scope_known",
        not unexpected_unmapped,
        expected_not_returned=sorted(EXPECTED_NOT_RETURNED & set(returned_checksums)),
        unexpected_unmapped=sorted(unexpected_unmapped),
    )

    manifest_path = _required_file(collector, root, "job_manifest.json")
    manifest: dict[str, Any] = {}
    if manifest_path:
        try:
            value = json.loads(_read_text(manifest_path))
            if not isinstance(value, dict):
                raise ValueError("manifest is not an object")
            manifest = value
        except (json.JSONDecodeError, ValueError) as exc:
            collector.check("job_manifest_parseable", False, error=str(exc))
        else:
            collector.check("job_manifest_parseable", True)
            identity_ok = (
                manifest.get("job_id") == EXPECTED_JOB_ID
                and manifest.get("phase") == "monoclinic"
                and manifest.get("expected_space_group_number") == 14
                and manifest.get("poscar_sha256") == EXPECTED_POSCAR_SHA256
                and manifest.get("atoms") == EXPECTED_ATOMS
                and manifest.get("formula_units") == 4
                and manifest.get("strain_factor") == 1.0
                and manifest.get("engineering_strain") == 0.0
                and manifest.get("constraint_strategy")
                == "fixed_all_lattice_vectors_ionic_relaxation_ISIF_2"
                and manifest.get("publication_ready") is False
            )
            collector.check("job_manifest_frozen_identity", identity_ok)
    report["job"] = {
        key: manifest.get(key)
        for key in (
            "job_id",
            "phase",
            "strain_factor",
            "engineering_strain",
            "formula_units",
            "publication_ready",
        )
    }

    claim_scope_path = _required_file(collector, root, "claim_scope.txt")
    if claim_scope_path:
        claim_scope = set(_read_text(claim_scope_path).splitlines())
        collector.check(
            "claim_scope_remains_non_publication",
            {"scientific_claim_allowed=false", "publication_ready=false"}.issubset(claim_scope),
        )
    _required_file(collector, root, "run_environment.txt")

    stages: dict[str, Any] = {}
    stage_paths: dict[str, dict[str, Path]] = {}
    for stage in ("relax", "static"):
        stages[stage], stage_paths[stage] = _stage_metrics(collector, root, stage)
    report["stages"] = stages

    frozen_stage_inputs = {
        "relax:incar_matches_frozen_input": (
            root / "results/relax/INCAR",
            root / "inputs/INCAR.relax",
        ),
        "static:incar_matches_frozen_input": (
            root / "results/static/INCAR",
            root / "inputs/INCAR.static",
        ),
        "relax:kpoints_matches_frozen_input": (
            root / "results/relax/KPOINTS",
            root / "inputs/KPOINTS",
        ),
        "static:kpoints_matches_frozen_input": (
            root / "results/static/KPOINTS",
            root / "inputs/KPOINTS",
        ),
        "relax:poscar_matches_frozen_input": (
            root / "results/relax/POSCAR",
            root / "inputs/POSCAR.original",
        ),
    }
    for code, (returned_path, frozen_path) in frozen_stage_inputs.items():
        comparable = (
            returned_path.is_file()
            and frozen_path.is_file()
            and not returned_path.is_symlink()
            and not frozen_path.is_symlink()
        )
        collector.check(
            code,
            comparable and returned_path.read_bytes() == frozen_path.read_bytes(),
            returned_sha256=_sha256_file(returned_path) if comparable else None,
            frozen_sha256=_sha256_file(frozen_path) if comparable else None,
        )

    original_path = root / "inputs/POSCAR.original"
    structure_paths = {
        "original": original_path,
        "relax_poscar": root / "results/relax/POSCAR",
        "relax_contcar": root / "results/relax/CONTCAR",
        "static_poscar": root / "results/static/POSCAR",
    }
    structures: dict[str, dict[str, Any]] = {}
    for label, path in structure_paths.items():
        if path.is_file() and not path.is_symlink() and path.stat().st_size:
            try:
                structure = _parse_poscar(path)
            except (OSError, ValueError) as exc:
                collector.check(f"structure_parseable:{label}", False, error=str(exc))
            else:
                structures[label] = structure
                collector.check(f"structure_parseable:{label}", True)
                collector.check(
                    f"structure_hf4o8_12_atoms:{label}",
                    structure["species"] == EXPECTED_SPECIES
                    and structure["counts"] == EXPECTED_COUNTS
                    and structure["atoms"] == EXPECTED_ATOMS,
                    species=structure["species"],
                    counts=structure["counts"],
                    atoms=structure["atoms"],
                )

    if original_path.is_file() and not original_path.is_symlink():
        original_sha = _sha256_file(original_path)
        collector.check(
            "original_poscar_frozen_sha256",
            original_sha == EXPECTED_POSCAR_SHA256,
            expected=EXPECTED_POSCAR_SHA256,
            actual=original_sha,
        )

    relax_contcar = structure_paths["relax_contcar"]
    static_poscar = structure_paths["static_poscar"]
    if relax_contcar.is_file() and static_poscar.is_file():
        collector.check(
            "static_poscar_equals_relax_contcar",
            relax_contcar.read_bytes() == static_poscar.read_bytes(),
            relax_contcar_sha256=_sha256_file(relax_contcar),
            static_poscar_sha256=_sha256_file(static_poscar),
        )

    if "original" in structures:
        lattice_deltas: dict[str, float] = {}
        for label in ("relax_poscar", "relax_contcar", "static_poscar"):
            if label in structures:
                lattice_deltas[label] = _maximum_lattice_delta(
                    structures["original"], structures[label]
                )
        collector.check(
            "fixed_lattice_preserved",
            len(lattice_deltas) == 3
            and all(delta <= FIXED_CELL_TOLERANCE_A for delta in lattice_deltas.values()),
            tolerance_A=FIXED_CELL_TOLERANCE_A,
            maximum_component_delta_A=lattice_deltas,
            zero_pressure_threshold_applied=False,
        )
        report["structure"] = {
            "species": structures["original"]["species"],
            "counts": structures["original"]["counts"],
            "atoms": structures["original"]["atoms"],
            "fixed_lattice_tolerance_A": FIXED_CELL_TOLERANCE_A,
        }
    if "static_poscar" in structures:
        report["space_group"] = _optional_space_group(structures["static_poscar"], symprec)
    else:
        report["space_group"] = {
            "status": "not_evaluated_missing_structure",
            "informational_only": True,
            "phase_identity_claim": False,
            "symprec_A": symprec,
        }

    report["checks"] = collector.checks
    report["failures"] = collector.failures
    report["status"] = "pass" if not collector.failures else "fail"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only, fail-closed audit of an extracted TEFS single-job result. "
            "Prints JSON and never writes a report, reads POTCAR, contacts TEFS, or uses a database."
        )
    )
    parser.add_argument("result_root", type=Path, help="Extracted result bundle directory")
    parser.add_argument(
        "--expected-upload-bundle",
        type=Path,
        default=DEFAULT_UPLOAD_BUNDLE,
        help="Locally retained POTCAR-free upload bundle used as the checksum authority",
    )
    parser.add_argument(
        "--symprec",
        type=float,
        default=1.0e-3,
        help="Optional informational spglib symmetry tolerance in angstrom",
    )
    args = parser.parse_args(argv)
    try:
        report = audit_retrieved_result(
            args.result_root,
            expected_upload_bundle=args.expected_upload_bundle,
            symprec=args.symprec,
        )
    except Exception as exc:  # Fail closed without leaking a traceback as apparent success.
        report = {
            "schema_version": "tefs-single-job-result-audit-v0.1",
            "audit_mode": "read_only_fail_closed",
            "result_root": str(args.result_root.absolute()),
            "status": "fail",
            "scientific_claim_allowed": False,
            "publication_ready": False,
            "failures": ["unexpected_audit_error"],
            "error": str(exc),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("status") == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
