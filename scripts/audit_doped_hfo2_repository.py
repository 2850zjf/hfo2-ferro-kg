#!/usr/bin/env python3
"""Audit the public Doped-HfO2 repository without executing scientific jobs."""

from __future__ import annotations

import argparse
import json
import py_compile
import re
import subprocess
import zipfile
from collections import Counter
from pathlib import Path

from ase.io import read


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPOSITORY = (
    ROOT
    / "data/literature_intake/computation_20260813/04_code_and_data/by_parent"
    / "10.1016_j.commatsci.2026.114793/Doped-HfO2"
)
DEFAULT_OUTPUT = (
    ROOT
    / "data/literature_intake/computation_20260813/06_reading_cards"
    / "doped_hfo2_repository_audit.json"
)


def parse_scalar(lines: list[str], key: str) -> int | None:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s+(\d+)")
    for line in lines:
        match = pattern.match(line)
        if match:
            return int(match.group(1))
    return None


def parse_block(lines: list[str], key: str) -> list[str]:
    header = re.compile(rf"^\s*{re.escape(key)}\s*$")
    start = next((index for index, line in enumerate(lines) if header.match(line)), None)
    if start is None:
        return []

    values: list[str] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            break
        values.extend(stripped.split())
    return values


def audit_abinit_input(path: Path) -> dict[str, object]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    natom = parse_scalar(lines, "natom")
    typat_count = len(parse_block(lines, "typat"))
    xred_values = parse_block(lines, "xred")
    xred_count = len(xred_values) // 3 if len(xred_values) % 3 == 0 else -1
    return {
        "path": path.as_posix(),
        "natom": natom,
        "typat_count": typat_count,
        "xred_count": xred_count,
        "structure_block_valid": natom == typat_count == xred_count,
    }


def git_commit(repository: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def audit(repository: Path) -> dict[str, object]:
    cif_records: list[dict[str, object]] = []
    for path in sorted((repository / "structures").glob("*.cif")):
        atoms = read(path, format="cif")
        cif_records.append(
            {
                "file_name": path.name,
                "atom_count": len(atoms),
                "formula": atoms.get_chemical_formula(mode="hill"),
                "element_counts": dict(sorted(Counter(atoms.get_chemical_symbols()).items())),
                "cell_lengths_angstrom": [round(float(value), 6) for value in atoms.cell.lengths()],
                "cell_volume_angstrom3": round(float(atoms.get_volume()), 6),
            }
        )

    scf_inputs = sorted((repository / "abinit_inputs/scf").glob("*.txt"))
    berry_inputs = sorted((repository / "abinit_inputs/berry_phase").glob("*.txt"))
    input_records = [audit_abinit_input(path) for path in scf_inputs + berry_inputs]

    expected_berry = {
        f"berry_{polarity}_{concentration}Per_{dopant}_HfO2_2x2x2-in.txt"
        for polarity in ("PlusP", "MinusP")
        for concentration in ("3.125", "6.25", "9.375")
        for dopant in ("Sc", "Y", "Zr")
    }
    actual_berry = {path.name for path in berry_inputs}

    python_scripts = sorted((repository / "scripts").glob("*.py"))
    compile_failures: list[dict[str, str]] = []
    for path in python_scripts:
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as error:
            compile_failures.append({"file_name": path.name, "error": str(error)})

    result_archives = sorted((repository / "results").rglob("*.zip"))
    bad_archives: list[str] = []
    archive_members: dict[str, list[str]] = {}
    for path in result_archives:
        try:
            with zipfile.ZipFile(path) as archive:
                bad_member = archive.testzip()
                if bad_member:
                    bad_archives.append(f"{path.name}:{bad_member}")
                archive_members[path.name] = archive.namelist()
        except zipfile.BadZipFile:
            bad_archives.append(path.name)

    slurm_scripts = sorted((repository / "slurm_jobs").glob("*-slurm.txt"))
    author_path_pattern = re.compile(r"/home/(?:yxu|yanan)/")
    author_path_files = [
        path.name
        for path in sorted(repository.rglob("*"))
        if path.is_file()
        and path.suffix in {".py", ".txt"}
        and author_path_pattern.search(path.read_text(encoding="utf-8", errors="ignore"))
    ]
    pseudopotentials = sorted(
        path.relative_to(repository).as_posix()
        for path in repository.rglob("*")
        if path.is_file() and path.suffix.lower() in {".psp8", ".pspnc", ".psp"}
    )

    return {
        "repository": repository.as_posix(),
        "git_commit": git_commit(repository),
        "structures": {
            "cif_count": len(cif_records),
            "all_parse_with_ase": len(cif_records) == 9,
            "records": cif_records,
        },
        "abinit_inputs": {
            "total": len(input_records),
            "scf": len(scf_inputs),
            "berry_phase": len(berry_inputs),
            "valid_structure_blocks": sum(
                bool(record["structure_block_valid"]) for record in input_records
            ),
            "missing_expected_berry_inputs": sorted(expected_berry - actual_berry),
            "records": input_records,
        },
        "python_scripts": {
            "count": len(python_scripts),
            "compile_failures": compile_failures,
        },
        "slurm_scripts": {
            "count": len(slurm_scripts),
            "portable_as_is": not author_path_files,
        },
        "result_archives": {
            "count": len(result_archives),
            "bad_archives": bad_archives,
            "members": archive_members,
        },
        "reproduction_gaps": {
            "pseudopotential_files": pseudopotentials,
            "author_absolute_path_file_count": len(author_path_files),
            "author_absolute_path_files": author_path_files,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=DEFAULT_REPOSITORY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    report = audit(args.repository.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = {
        "git_commit": report["git_commit"],
        "cif_count": report["structures"]["cif_count"],
        "abinit_inputs": report["abinit_inputs"]["total"],
        "valid_abinit_structure_blocks": report["abinit_inputs"][
            "valid_structure_blocks"
        ],
        "missing_berry_inputs": len(
            report["abinit_inputs"]["missing_expected_berry_inputs"]
        ),
        "python_compile_failures": len(report["python_scripts"]["compile_failures"]),
        "slurm_scripts": report["slurm_scripts"]["count"],
        "result_archives": report["result_archives"]["count"],
        "bad_archives": len(report["result_archives"]["bad_archives"]),
        "pseudopotential_files": len(
            report["reproduction_gaps"]["pseudopotential_files"]
        ),
        "author_absolute_path_files": report["reproduction_gaps"][
            "author_absolute_path_file_count"
        ],
        "output": args.output.resolve().as_posix(),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
