#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INTAKE = PROJECT_ROOT / "data" / "computation" / "manual_structure_intake" / "raw"
DEFAULT_JOBS = PROJECT_ROOT / "data" / "computation" / "validation_jobs"

REQUIRED_PHASES = ["monoclinic", "tetragonal", "cubic", "orthorhombic"]
ACCEPTED_EXTENSIONS = [".POSCAR", ".vasp", ".cif", ".CIF"]


def _find_phase_file(intake_dir: Path, phase: str) -> Path | None:
    stems = [
        f"hfo2_{phase}",
        f"HfO2_{phase}",
        phase,
    ]
    for stem in stems:
        for ext in ACCEPTED_EXTENSIONS:
            path = intake_dir / f"{stem}{ext}"
            if path.exists():
                return path
    return None


def _copy_templates(job_dir: Path, target_dir: Path) -> None:
    template_dir = job_dir / "templates"
    for name in ["INCAR.relax", "INCAR.static", "KPOINTS"]:
        source = template_dir / name
        if source.exists():
            shutil.copyfile(source, target_dir / name)
    potcar_note = target_dir / "POTCAR_NOT_INCLUDED.txt"
    potcar_note.write_text(
        "POTCAR is intentionally not included. Copy it only on the licensed "
        "compute machine and record PAW choices in the result manifest.\n",
        encoding="utf-8",
    )


def _write_phase_note(target_dir: Path, phase: str, source: Path, job_id: str) -> None:
    direct_vasp = source.suffix in {".POSCAR", ".vasp"}
    note = [
        f"# HfO2 {phase} structure",
        "",
        f"- job_id: {job_id}",
        f"- source_file: {source.name}",
        "- source_policy: user-downloaded documented crystallographic structure",
        f"- direct_vasp_ready: {str(direct_vasp).lower()}",
        "",
        "## Next Step",
        "",
    ]
    if direct_vasp:
        note.append("Use `POSCAR` with the copied INCAR/KPOINTS templates on the licensed compute machine.")
    else:
        note.append("Convert `SOURCE.cif` to POSCAR with pymatgen/ASE or VESTA before VASP submission.")
    target_dir.joinpath("job_notes.md").write_text("\n".join(note) + "\n", encoding="utf-8")


def _job_dirs(jobs_dir: Path) -> list[Path]:
    return sorted(path for path in jobs_dir.glob("cjob_*__phase_stability") if path.is_dir())


def import_manual_structures(intake_dir: Path, jobs_dir: Path, dry_run: bool = False) -> dict[str, object]:
    available = {phase: _find_phase_file(intake_dir, phase) for phase in REQUIRED_PHASES}
    missing = [phase for phase, path in available.items() if path is None]
    jobs = _job_dirs(jobs_dir)

    copied_rows: list[dict[str, str]] = []
    if not missing and not dry_run:
        for job_dir in jobs:
            manifest_path = job_dir / "job_manifest.json"
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                job_id = str(manifest.get("job_id") or job_dir.name.split("__", 1)[0])
            else:
                job_id = job_dir.name.split("__", 1)[0]
            for phase, source in available.items():
                if source is None:
                    continue
                target_dir = job_dir / "manual_structures" / phase
                target_dir.mkdir(parents=True, exist_ok=True)
                if source.suffix in {".POSCAR", ".vasp"}:
                    target_file = target_dir / "POSCAR"
                else:
                    target_file = target_dir / f"SOURCE{source.suffix.lower()}"
                shutil.copyfile(source, target_file)
                _copy_templates(job_dir, target_dir)
                _write_phase_note(target_dir, phase, source, job_id)
                copied_rows.append(
                    {
                        "job_id": job_id,
                        "phase": phase,
                        "source_file": str(source),
                        "target_file": str(target_file),
                    }
                )

    report_dir = intake_dir.parent
    report_dir.mkdir(parents=True, exist_ok=True)
    manifest_csv = report_dir / "manual_structure_import_manifest.csv"
    if copied_rows:
        with manifest_csv.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(copied_rows[0]))
            writer.writeheader()
            writer.writerows(copied_rows)

    status = "ready" if not missing else "missing_required_structures"
    summary = {
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "intake_dir": str(intake_dir),
        "jobs_dir": str(jobs_dir),
        "required_phases": REQUIRED_PHASES,
        "available": {phase: str(path) if path else None for phase, path in available.items()},
        "missing": missing,
        "jobs_found": len(jobs),
        "copied_files": len(copied_rows),
        "dry_run": dry_run,
        "manifest_csv": str(manifest_csv) if copied_rows else None,
        "safety_note": "No VASP, SSH, cloud job, POTCAR, or API call was executed.",
    }
    (report_dir / "manual_structure_import_state.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# Manual Structure Import Report",
        "",
        f"- status: {summary['status']}",
        f"- jobs_found: {summary['jobs_found']}",
        f"- copied_files: {summary['copied_files']}",
        f"- dry_run: {summary['dry_run']}",
        "",
        "## Missing",
        "",
        *[f"- {phase}" for phase in missing],
        "",
        "## Available",
        "",
        *[f"- {phase}: {path or 'missing'}" for phase, path in summary["available"].items()],
        "",
        "## Safety",
        "",
        "- No VASP, SSH, cloud job, POTCAR, or API call was executed.",
    ]
    (report_dir / "manual_structure_import_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Import manually downloaded HfO2 structures into prepared computation jobs.")
    parser.add_argument("--intake-dir", type=Path, default=DEFAULT_INTAKE)
    parser.add_argument("--jobs-dir", type=Path, default=DEFAULT_JOBS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    summary = import_manual_structures(args.intake_dir, args.jobs_dir, dry_run=args.dry_run)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
