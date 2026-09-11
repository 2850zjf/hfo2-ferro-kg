from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.services.vasp_raw_audit import (
    audit_vasp_raw_outputs,
    export_vasp_raw_audit,
)


POSCAR_TEXT = """Hf1 O2
1.0
5.0 0.0 0.0
0.0 5.0 0.0
0.0 0.0 5.0
Hf O
1 2
Direct
0.0 0.0 0.0
0.25 0.25 0.25
0.75 0.75 0.75
"""


def _outcar(energy: float = -30.0, max_force: float = 0.010) -> str:
    return f""" vasp.6.3.0 20Jan22 test build complex
   TITEL  = PAW_PBE Hf_pv 06Sep2000
   TITEL  = PAW_PBE O 08Apr2002
   ENCUT  = 520.0 eV
   NKPTS = 27
   NELM = 60
   NIONS = 3
   NSW = 0
   IBRION = -1
 POSITION                                       TOTAL-FORCE (eV/Angst)
 -----------------------------------------------------------------------------------
 0.0 0.0 0.0  0.003 0.004 0.000
 1.0 1.0 1.0 -0.003 0.004 0.000
 2.0 2.0 2.0  0.000 0.000 {max_force:.6f}
 -----------------------------------------------------------------------------------
 aborting loop because EDIFF is reached
 free  energy   TOTEN  = {energy:20.8f} eV
 energy  without entropy= {energy:20.8f}  energy(sigma->0) = {energy:20.8f}
 General timing and accounting informations for this job:
"""


def _vasprun(energy: float = -30.0, max_force: float = 0.010) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<modeling>
  <generator>
    <i name="program">vasp</i>
    <i name="version">6.3.0</i>
    <i name="subversion">test build</i>
    <i name="platform">test</i>
  </generator>
  <incar>
    <i name="NSW">0</i>
    <i name="IBRION">-1</i>
  </incar>
  <parameters><separator>
    <i name="NELM">60</i>
  </separator></parameters>
  <calculation>
    <scstep><energy><i name="e_0_energy">{energy}</i></energy></scstep>
    <scstep><energy><i name="e_0_energy">{energy}</i></energy></scstep>
    <varray name="forces">
      <v>0.003 0.004 0.000</v>
      <v>-0.003 0.004 0.000</v>
      <v>0.000 0.000 {max_force:.6f}</v>
    </varray>
    <varray name="stress">
      <v>0.1 0.0 0.0</v><v>0.0 0.1 0.0</v><v>0.0 0.0 0.1</v>
    </varray>
    <energy>
      <i name="e_fr_energy">{energy}</i>
      <i name="e_0_energy">{energy}</i>
    </energy>
  </calculation>
</modeling>
"""


def _write_fixture(
    root: Path,
    *,
    phase: str = "monoclinic",
    raw_energy: float = -30.0,
    summary_energy: float = -30.0,
    relax_converged: bool = True,
    include_relax_output: bool = True,
    max_force: float = 0.010,
) -> Path:
    phase_dir = root / phase
    phase_dir.mkdir(parents=True)
    (phase_dir / "POSCAR").write_text(POSCAR_TEXT, encoding="utf-8")
    (phase_dir / "CONTCAR").write_text(POSCAR_TEXT, encoding="utf-8")
    (phase_dir / "OUTCAR").write_text(_outcar(raw_energy, max_force), encoding="utf-8")
    (phase_dir / "vasprun.xml").write_text(_vasprun(raw_energy, max_force), encoding="utf-8")
    if include_relax_output:
        marker = (
            "reached required accuracy - stopping structural energy minimisation\n"
            if relax_converged
            else "relaxation stopped without the required-accuracy marker\n"
        )
        (phase_dir / "relax.output").write_text(
            " 1 F= -.30000000E+02 E0= -.30000000E+02 d E=-.300000E+02\n" + marker,
            encoding="utf-8",
        )
    for name in ("static.output", "INCAR.relax", "INCAR.static", "KPOINTS"):
        (phase_dir / name).write_text(f"fixture {name}\n", encoding="utf-8")
    with (root / "phase_energy_summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["phase", "total_energy_eV"])
        writer.writeheader()
        writer.writerow({"phase": phase, "total_energy_eV": summary_energy})
    return phase_dir


def test_raw_xml_and_outcar_are_authority_not_summary_csv(tmp_path: Path) -> None:
    source = tmp_path / "source"
    phase_dir = _write_fixture(source, raw_energy=-30.0, summary_energy=-999.0)

    audit = audit_vasp_raw_outputs(source, phases=("monoclinic",))
    phase = audit["phases"][0]

    assert phase["energy"]["total_energy_eV"] == pytest.approx(-30.0)
    assert phase["energy"]["energy_eV_per_fu"] == pytest.approx(-30.0)
    assert phase["summary_cross_check"]["authoritative"] is False
    assert phase["summary_cross_check"]["matches_raw_within_tolerance"] is False
    assert "summary_energy_mismatch_raw_output" in phase["blockers"]
    assert phase["files"]["OUTCAR"]["sha256"] == hashlib.sha256(
        (phase_dir / "OUTCAR").read_bytes()
    ).hexdigest()
    assert phase["files"]["relax.output"]["sha256"]
    assert audit["publication_grade"] is False


def test_relax_output_marker_is_required_for_ionic_convergence(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_fixture(source, relax_converged=False)

    phase = audit_vasp_raw_outputs(source, phases=("monoclinic",))["phases"][0]

    assert phase["convergence"]["run_completed"] is True
    assert phase["convergence"]["electronic_converged"] is True
    assert phase["convergence"]["ionic_converged"] is False
    assert "ionic_relaxation_not_verified" in phase["blockers"]
    assert "relax_outcar_and_vasprun_xml_not_preserved" in phase["blockers"]


def test_missing_relax_output_makes_raw_audit_incomplete(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_fixture(source, include_relax_output=False)

    audit = audit_vasp_raw_outputs(source, phases=("monoclinic",))
    phase = audit["phases"][0]

    assert phase["raw_output_auditable"] is False
    assert "missing_raw_file:relax.output" in phase["blockers"]
    assert audit["all_requested_phases_auditable"] is False
    assert audit["status"] == "incomplete_raw_audit"


def test_force_above_publication_target_remains_auditable_but_blocked(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_fixture(source, max_force=0.015)

    audit = audit_vasp_raw_outputs(source, phases=("monoclinic",))
    phase = audit["phases"][0]

    assert phase["raw_output_auditable"] is True
    assert "final_force_above_0p01_publication_target_eV_per_A" in phase["blockers"]
    assert "one_or_more_final_forces_above_0p01_eV_per_A" in audit["publication_blockers"]


def test_export_is_confined_to_configured_reports_root(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_fixture(source)
    audit = audit_vasp_raw_outputs(source, phases=("monoclinic",))
    reports_root = tmp_path / "reports"
    output_dir = reports_root / "audit"

    paths = export_vasp_raw_audit(audit, output_dir, reports_root=reports_root)

    assert Path(paths["json"]).is_file()
    assert Path(paths["csv"]).is_file()
    assert Path(paths["markdown"]).is_file()
    saved = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert saved["summary_policy"].endswith("never_authority")
    with pytest.raises(ValueError, match="inside reports root"):
        export_vasp_raw_audit(audit, tmp_path / "outside", reports_root=reports_root)


def test_cli_help_does_not_require_database_or_source_files() -> None:
    project_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(project_root / "pipelines" / "66_audit_vasp_raw_outputs.py"), "--help"],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "No database or" in result.stdout
    assert "external calculation is used" in result.stdout
