from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PILOT_DIR = (
    ROOT / "computations" / "phase_strain_pilot" / "tefs_single_job_pilot_v0_1"
)
SCRIPT = PILOT_DIR / "audit_retrieved_result.py"
UPLOAD_BUNDLE = (
    PILOT_DIR / "output" / "hfo2_m_biaxial_0p0000_workflow_smoke.tar.gz"
)

SPEC = importlib.util.spec_from_file_location("tefs_result_audit", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _bundle_files() -> dict[str, bytes]:
    wanted = {
        "POSCAR",
        "KPOINTS",
        "INCAR.relax",
        "INCAR.static",
        "INPUT_SHA256SUMS.txt",
        "job_manifest.json",
        "run_vasp_two_step.sh",
        "tefs_hpc.preview.json",
    }
    found: dict[str, bytes] = {}
    with tarfile.open(UPLOAD_BUNDLE, "r:gz") as archive:
        for member in archive.getmembers():
            name = Path(member.name).name
            if member.isfile() and name in wanted:
                stream = archive.extractfile(member)
                assert stream is not None
                found[name] = stream.read()
    assert set(found) == wanted
    return found


def _vasprun(energy: float, max_force: float) -> str:
    forces = "\n".join(
        f"      <v>{max_force if index == 0 else 0.0:.8f} 0.0 0.0</v>"
        for index in range(12)
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<modeling>
  <calculation>
    <varray name="forces">
{forces}
    </varray>
    <varray name="stress">
      <v>12.0 0.1 0.2</v>
      <v>0.1 -4.0 0.3</v>
      <v>0.2 0.3 8.0</v>
    </varray>
    <energy><i name="e_fr_energy">{energy:.8f}</i></energy>
  </calculation>
</modeling>
"""


def _outcar(energy: float, *, static: bool) -> str:
    ediff = " aborting loop because EDIFF is reached\n" if static else ""
    return f""" vasp.6.3.0 test
{ediff} free  energy   TOTEN  = {energy:20.8f} eV
 General timing and accounting informations for this job:
"""


def _write_result(root: Path, *, zbrent_count: int = 1) -> Path:
    assets = _bundle_files()
    root.mkdir()
    for name in (
        "INPUT_SHA256SUMS.txt",
        "job_manifest.json",
        "run_vasp_two_step.sh",
        "tefs_hpc.preview.json",
    ):
        (root / name).write_bytes(assets[name])
    (root / "result_status.txt").write_text(
        "success: relax and static completed\nexit_code=0\n", encoding="utf-8"
    )
    (root / "claim_scope.txt").write_text(
        "purpose=link_pilot_only\n"
        "scientific_claim_allowed=false\n"
        "publication_ready=false\n",
        encoding="utf-8",
    )
    (root / "run_environment.txt").write_text(
        "started_utc=2026-08-29T00:00:00Z\nfinished_utc=2026-08-29T00:01:00Z\n",
        encoding="utf-8",
    )

    inputs = root / "inputs"
    inputs.mkdir()
    (inputs / "POSCAR.original").write_bytes(assets["POSCAR"])
    (inputs / "KPOINTS").write_bytes(assets["KPOINTS"])
    (inputs / "INCAR.relax").write_bytes(assets["INCAR.relax"])
    (inputs / "INCAR.static").write_bytes(assets["INCAR.static"])

    for stage, energy, max_force in (
        ("relax", -121.70, 0.010),
        ("static", -121.75, 0.008),
    ):
        stage_dir = root / "results" / stage
        stage_dir.mkdir(parents=True)
        (stage_dir / "POSCAR").write_bytes(assets["POSCAR"])
        (stage_dir / "KPOINTS").write_bytes(assets["KPOINTS"])
        (stage_dir / "INCAR").write_bytes(assets[f"INCAR.{stage}"])
        (stage_dir / "OUTCAR").write_text(
            _outcar(energy, static=stage == "static"), encoding="utf-8"
        )
        (stage_dir / "OSZICAR").write_text(
            f" 1 F= {energy:.8f} E0= {energy:.8f} d E=0.0\n", encoding="utf-8"
        )
        (stage_dir / "vasprun.xml").write_text(
            _vasprun(energy, max_force), encoding="utf-8"
        )
        stdout = "normal VASP stdout\n"
        if stage == "relax":
            stdout += "reached required accuracy - stopping structural energy minimisation\n"
            stdout += "ZBRENT diagnostic\n" * zbrent_count
            (stage_dir / "CONTCAR").write_bytes(assets["POSCAR"])
        (stage_dir / f"{stage}.output").write_text(stdout, encoding="utf-8")
    return root


def _snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def _audit(root: Path) -> dict:
    return MODULE.audit_retrieved_result(root, expected_upload_bundle=UPLOAD_BUNDLE)


def test_successful_nested_result_passes_and_records_diagnostics(tmp_path: Path) -> None:
    result_root = _write_result(tmp_path / "result", zbrent_count=2)

    report = _audit(result_root)

    assert report["status"] == "pass", report["failures"]
    assert report["stages"]["static"]["final_toten_eV"] == pytest.approx(-121.75)
    assert report["stages"]["relax"]["max_force_norm_eV_per_A"] == pytest.approx(0.01)
    assert report["stages"]["static"]["final_stress_matrix_kB"][0][0] == 12.0
    assert report["stages"]["relax"]["zbrent"]["count"] == 2
    assert report["stages"]["relax"]["zbrent"]["fatal_by_itself"] is False
    assert report["stages"]["static"]["zero_pressure_threshold_applied"] is False
    assert report["space_group"]["phase_identity_claim"] is False
    assert report["publication_ready"] is False


def test_audit_rejects_alternate_bundle_even_with_matching_sidecar(tmp_path: Path) -> None:
    result_root = _write_result(tmp_path / "result")
    alternate_bundle = tmp_path / "alternate.tar.gz"
    alternate_bundle.write_bytes(UPLOAD_BUNDLE.read_bytes() + b"alternate")
    digest = hashlib.sha256(alternate_bundle.read_bytes()).hexdigest()
    Path(f"{alternate_bundle}.sha256").write_text(
        f"{digest}  {alternate_bundle.name}\n", encoding="utf-8"
    )

    report = MODULE.audit_retrieved_result(
        result_root, expected_upload_bundle=alternate_bundle
    )

    assert report["status"] == "fail"
    assert "trusted_upload_bundle_verified" in report["failures"]


def test_audit_is_read_only_in_process_and_via_cli(tmp_path: Path) -> None:
    result_root = _write_result(tmp_path / "result")
    before = _snapshot(result_root)

    report = _audit(result_root)
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(result_root),
            "--expected-upload-bundle",
            str(UPLOAD_BUNDLE),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert report["status"] == "pass"
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["status"] == "pass"
    assert _snapshot(result_root) == before


def test_nonzero_status_and_potcar_fail_closed(tmp_path: Path) -> None:
    result_root = _write_result(tmp_path / "result")
    (result_root / "result_status.txt").write_text(
        "failed: VASP did not complete\nexit_code=19\n", encoding="utf-8"
    )
    (result_root / "POTCAR").write_text("synthetic forbidden marker\n", encoding="utf-8")

    report = _audit(result_root)

    assert report["status"] == "fail"
    assert "no_potcar" in report["failures"]
    assert "result_status_success" in report["failures"]
    assert "result_status_exit_code_zero" in report["failures"]


@pytest.mark.parametrize(
    ("mutation", "expected_failure"),
    [
        ("missing_static_oszicar", "required_nonempty_file:results/static/OSZICAR"),
        ("broken_static_xml", "static:vasprun_xml_parseable"),
        ("fatal_static_stdout", "static:no_fatal_markers"),
        ("checksum_tamper", "returned_input_checksum:INCAR.static"),
    ],
)
def test_required_evidence_failures_are_closed(
    tmp_path: Path, mutation: str, expected_failure: str
) -> None:
    result_root = _write_result(tmp_path / "result")
    if mutation == "missing_static_oszicar":
        (result_root / "results/static/OSZICAR").unlink()
    elif mutation == "broken_static_xml":
        (result_root / "results/static/vasprun.xml").write_text("<broken", encoding="utf-8")
    elif mutation == "fatal_static_stdout":
        (result_root / "results/static/static.output").write_text(
            "BRMIX: very serious problems\n", encoding="utf-8"
        )
    elif mutation == "checksum_tamper":
        (result_root / "inputs/INCAR.static").write_text("tampered\n", encoding="utf-8")

    report = _audit(result_root)

    assert report["status"] == "fail"
    assert expected_failure in report["failures"]


def test_handoff_and_fixed_lattice_are_independent_gates(tmp_path: Path) -> None:
    handoff_root = _write_result(tmp_path / "handoff")
    static_poscar = handoff_root / "results/static/POSCAR"
    static_poscar.write_text(static_poscar.read_text() + "\n", encoding="utf-8")
    handoff_report = _audit(handoff_root)
    assert "static_poscar_equals_relax_contcar" in handoff_report["failures"]

    lattice_root = _write_result(tmp_path / "lattice")
    relax_contcar = lattice_root / "results/relax/CONTCAR"
    lines = relax_contcar.read_text(encoding="utf-8").splitlines()
    fields = lines[2].split()
    fields[0] = f"{float(fields[0]) + 0.001:.14f}"
    lines[2] = " ".join(fields)
    changed = "\n".join(lines) + "\n"
    relax_contcar.write_text(changed, encoding="utf-8")
    (lattice_root / "results/static/POSCAR").write_text(changed, encoding="utf-8")

    lattice_report = _audit(lattice_root)

    assert "static_poscar_equals_relax_contcar" not in lattice_report["failures"]
    assert "fixed_lattice_preserved" in lattice_report["failures"]


def test_cli_failure_uses_nonzero_exit_and_json(tmp_path: Path) -> None:
    result_root = _write_result(tmp_path / "result")
    (result_root / "results/static/OUTCAR").write_text(
        "no footer and no energy\n", encoding="utf-8"
    )

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), str(result_root)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["status"] == "fail"
    assert "static:outcar_completion_footer" in payload["failures"]
    assert "static:final_toten_present" in payload["failures"]
