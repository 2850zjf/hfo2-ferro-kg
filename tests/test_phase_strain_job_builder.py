from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from backend.services.phase_strain_job_builder import (
    DEFAULT_SOURCE_ROOT,
    PHASE_SPECS,
    STRAIN_FACTORS,
    common_tetragonal_in_plane_reference,
    prepare_phase_strain_jobs,
    read_poscar,
    validate_source_structure,
)


PHASES = ("monoclinic", "orthorhombic", "tetragonal")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def frozen_sources(tmp_path: Path) -> Path:
    missing = [phase for phase in PHASES if not (DEFAULT_SOURCE_ROOT / phase / "CONTCAR").is_file()]
    if missing:
        pytest.skip(f"Frozen TEFS CONTCAR snapshot is unavailable: {missing}")
    source_copy = tmp_path / "readonly_source_copy"
    for phase in PHASES:
        phase_dir = source_copy / phase
        phase_dir.mkdir(parents=True)
        shutil.copyfile(DEFAULT_SOURCE_ROOT / phase / "CONTCAR", phase_dir / "CONTCAR")
    return source_copy


def test_frozen_real_sources_match_hash_symmetry_and_reference() -> None:
    missing = [phase for phase in PHASES if not (DEFAULT_SOURCE_ROOT / phase / "CONTCAR").is_file()]
    if missing:
        pytest.skip(f"Frozen TEFS CONTCAR snapshot is unavailable: {missing}")

    structures = {}
    for phase in PHASES:
        path = DEFAULT_SOURCE_ROOT / phase / "CONTCAR"
        assert _sha256(path) == PHASE_SPECS[phase]["source_sha256"]
        structures[phase] = read_poscar(path)
        verification = validate_source_structure(phase, structures[phase])
        assert verification["status"] == "verified_phase_specific_contract"
        assert verification["expected_space_group_number"] == {"monoclinic": 14, "orthorhombic": 29, "tetragonal": 137}[phase]

    reference = common_tetragonal_in_plane_reference(structures["tetragonal"])
    source_a = math.sqrt(sum(value**2 for value in structures["tetragonal"].lattice[0]))
    assert math.isclose(reference["L_ref_A"], math.sqrt(2.0) * source_a, rel_tol=0, abs_tol=1e-10)


def test_prepare_real_dry_run_package_is_12_atom_square_and_never_executes(
    tmp_path: Path,
    frozen_sources: Path,
) -> None:
    output = tmp_path / "allowed" / "pilot"
    allowed = tmp_path / "allowed"
    source_hashes_before = {
        phase: _sha256(frozen_sources / phase / "CONTCAR") for phase in PHASES
    }

    summary = prepare_phase_strain_jobs(
        frozen_sources,
        output,
        allowed_output_root=allowed,
    )

    assert summary["status"] == "prepared_dry_run_not_executed"
    assert summary["jobs"] == 9
    assert summary["geometry_generated"] is True
    assert summary["publication_ready"] is False
    assert source_hashes_before == {
        phase: _sha256(frozen_sources / phase / "CONTCAR") for phase in PHASES
    }
    manifest = json.loads((output / "strain_pilot_manifest.json").read_text(encoding="utf-8"))
    assert manifest["vasp_invoked"] is False
    assert manifest["cloud_job_submitted"] is False
    assert manifest["database_read"] is False
    assert manifest["dotenv_read"] is False
    assert manifest["potcar_included"] is False
    assert manifest["strain_contract"]["factors"] == list(STRAIN_FACTORS)
    assert manifest["strain_contract"]["constraint_strategy"].endswith("ISIF_2")
    assert len(manifest["publication_blockers"]) >= 10
    assert not list(output.rglob("POTCAR"))
    assert not list(output.rglob("*.sh"))
    audited_hashes = manifest["external_preparation_audit"]["audited_poscar_sha256"]
    external_audit = manifest["external_preparation_audit"]
    assert external_audit["performed_by_builder"] is False
    assert external_audit["tool"] == "spglib 2.7.0"
    assert external_audit["symprec"] == pytest.approx(1e-3)
    assert external_audit["relaxed_output_status"].startswith("not_checked")

    reference = float(manifest["strain_contract"]["reference"]["L_ref_A"])
    assert reference == pytest.approx(summary["reference_length_A"], abs=1e-12)
    for job in manifest["jobs"]:
        poscar = read_poscar(output / job["poscar_relative_path"])
        assert poscar.atom_count == 12
        assert poscar.formula_units == 4
        first, second = poscar.lattice[:2]
        first_length = math.sqrt(sum(value**2 for value in first))
        second_length = math.sqrt(sum(value**2 for value in second))
        dot = sum(a * b for a, b in zip(first, second))
        expected_length = reference * float(job["strain_factor"])
        assert first_length == pytest.approx(expected_length, abs=1e-10)
        assert second_length == pytest.approx(expected_length, abs=1e-10)
        assert dot == pytest.approx(0.0, abs=1e-9)
        job_dir = (output / job["poscar_relative_path"]).parent
        assert "ISIF = 2" in (job_dir / "INCAR.relax").read_text(encoding="utf-8")
        static_incar = (job_dir / "INCAR.static").read_text(encoding="utf-8")
        assert "ISTART = 0" in static_incar
        assert "ICHARG = 2" in static_incar
        assert (job_dir / "POTCAR_NOT_INCLUDED.txt").is_file()
        assert _sha256(output / job["poscar_relative_path"]) == job["poscar_sha256"]
        assert audited_hashes[job["job_id"]] == job["poscar_sha256"]
        assert external_audit["audited_poscar_sha256"][job["job_id"]] == job["poscar_sha256"]

    tetragonal_jobs = [job for job in manifest["jobs"] if job["phase"] == "tetragonal"]
    assert all(
        job["mapping"]["integer_supercell_matrix"]
        == [[1, 1, 0], [-1, 1, 0], [0, 0, 1]]
        for job in tetragonal_jobs
    )


def test_missing_sources_emit_only_a_blocked_contract(tmp_path: Path) -> None:
    source = tmp_path / "missing_sources"
    output = tmp_path / "allowed" / "blocked"
    summary = prepare_phase_strain_jobs(
        source,
        output,
        allowed_output_root=tmp_path / "allowed",
    )
    assert summary["status"] == "blocked_contract_only"
    assert summary["geometry_generated"] is False
    assert set(summary["preparation_blockers"]) == {
        "missing_source_CONTCAR:monoclinic",
        "missing_source_CONTCAR:orthorhombic",
        "missing_source_CONTCAR:tetragonal",
    }
    assert (output / "strain_pilot_manifest.json").is_file()
    assert not list(output.rglob("POSCAR"))


def test_changed_source_hash_blocks_all_geometry(
    tmp_path: Path,
    frozen_sources: Path,
) -> None:
    changed = frozen_sources / "orthorhombic" / "CONTCAR"
    changed.write_text(changed.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    output = tmp_path / "allowed" / "hash_blocked"
    summary = prepare_phase_strain_jobs(
        frozen_sources,
        output,
        allowed_output_root=tmp_path / "allowed",
    )
    assert summary["status"] == "blocked_contract_only"
    assert "frozen_source_hash_mismatch:orthorhombic" in summary["preparation_blockers"]
    assert not list(output.rglob("POSCAR"))


def test_output_must_be_new_and_inside_allowed_root(
    tmp_path: Path,
    frozen_sources: Path,
) -> None:
    allowed = tmp_path / "allowed"
    with pytest.raises(ValueError, match="child of the allowed root"):
        prepare_phase_strain_jobs(
            frozen_sources,
            tmp_path / "outside",
            allowed_output_root=allowed,
        )
    existing = allowed / "existing"
    existing.mkdir(parents=True)
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        prepare_phase_strain_jobs(
            frozen_sources,
            existing,
            allowed_output_root=allowed,
        )


def test_cli_entrypoint_exposes_only_preparation_options() -> None:
    project_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(project_root / "pipelines" / "67_prepare_phase_strain_jobs.py"), "--help"],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "dry-run" in result.stdout
    assert "source-root" in result.stdout
    assert "--submit" not in result.stdout
