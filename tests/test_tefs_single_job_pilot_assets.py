import json
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = (
    ROOT
    / "computations"
    / "phase_strain_pilot"
    / "tefs_single_job_pilot_v0_1"
)
SOURCE_JOB = (
    ROOT
    / "computations"
    / "phase_strain_pilot"
    / "generated_v0_1"
    / "jobs"
    / "monoclinic"
    / "biaxial_0p0000"
)
EXPECTED_DESCRIPTOR = {
    "node": "1",
    "instance_type": "S6.4XLARGE32",
    "image_id": "img-45t2j6r7",
    "region": "ap-beijing",
    # Live job 151024 proved that false produced POSTPAID_BY_HOUR in the
    # active TEFS service, despite the opposite mapping in CLI help text.
    "spot_paid": False,
    "public_ip": False,
    "disk_size": "50",
    "ntasks_per_node": "16",
    "cmd": (
        "command -v timeout >/dev/null && exec timeout "
        "--signal=TERM --kill-after=60s 30m bash ./run_vasp_two_step.sh"
    ),
}


def test_tefs_preview_is_one_bounded_non_public_job() -> None:
    payload = json.loads((ASSET_DIR / "tefs_hpc.preview.json").read_text())

    assert payload == EXPECTED_DESCRIPTOR


def test_built_bundle_embeds_the_reviewed_descriptor() -> None:
    archive_path = ASSET_DIR / "output" / "hfo2_m_biaxial_0p0000_workflow_smoke.tar.gz"
    with tarfile.open(archive_path, "r:gz") as archive:
        members = [
            member
            for member in archive.getmembers()
            if Path(member.name).name == "tefs_hpc.preview.json" and member.isfile()
        ]
        assert len(members) == 1
        stream = archive.extractfile(members[0])
        assert stream is not None
        embedded = json.loads(stream.read())

    source = json.loads((ASSET_DIR / "tefs_hpc.preview.json").read_text())
    assert embedded == source == EXPECTED_DESCRIPTOR


def test_pilot_is_bound_to_frozen_monoclinic_input() -> None:
    manifest = json.loads((SOURCE_JOB / "job_manifest.json").read_text())
    static_incar = (SOURCE_JOB / "INCAR.static").read_text()

    assert manifest["job_id"] == "monoclinic__biaxial_0p0000"
    assert manifest["poscar_sha256"] == (
        "fe3c64e78ad8ce419eb018e6815f0ce73f646f2ac0bde85ec0c422e6335be9b7"
    )
    assert manifest["constraint_strategy"] == (
        "fixed_all_lattice_vectors_ionic_relaxation_ISIF_2"
    )
    assert manifest["publication_ready"] is False
    assert "ISTART = 0" in static_incar
    assert "ICHARG = 2" in static_incar


def test_cloud_potcar_prep_is_hash_gated_and_cloud_only() -> None:
    script = (ASSET_DIR / "prepare_licensed_potcar.sh").read_text()

    assert "/root/home/*" in script
    assert "/root/POTCAR/PBE/Hf_pv/POTCAR" in script
    assert "/root/POTCAR/PBE/O/POTCAR" in script
    assert "db42cd3e4a48e81eaff18895282e2f5a37f2c8c6a4b9ac45e357affc13c83b84" in script
    assert "8a74b9a1f5fdb3d0c3e0183c7873177abdbef07d407b310b7edcd9ed0a3eea64" in script
    assert "PAW_PBE Hf_pv 06Sep2000" in script
    assert "PAW_PBE O 08Apr2002" in script


def test_result_archive_explicitly_excludes_potcar_binary() -> None:
    runner = (ASSET_DIR / "run_vasp_two_step.sh").read_text()
    builder = (ASSET_DIR / "build_upload_bundle.sh").read_text()

    archive_block = runner.split("tar -czf", 1)[1].split("mv -f", 1)[0]
    assert " POTCAR" not in archive_block
    assert "job_manifest.json INPUT_SHA256SUMS.txt" in archive_block
    assert "result_bundle_no_potcar.tar.gz" in runner
    assert "grep -qE '(^|/)POTCAR$'" in builder
    assert "assert payload == expected" in builder
    assert '"node": "1"' in builder
    assert '"disk_size": "50"' in builder
    assert '"ntasks_per_node": "16"' in builder
    assert '"image_id": "img-45t2j6r7"' in builder
    assert "reviewed bounded on-demand configuration" in builder
    assert 'gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0)' in builder
    assert "info.pax_headers = {}" in builder
    assert ".env" not in runner
    assert "sqlite" not in runner.lower()


def test_runner_has_fail_closed_two_stage_gates() -> None:
    runner = (ASSET_DIR / "run_vasp_two_step.sh").read_text()

    assert "relax_exit" in runner
    assert "static_exit" in runner
    assert "reached required accuracy" in runner
    assert "General timing and accounting informations" in runner
    assert "aborting loop because EDIFF is reached" in runner
    assert "verify_fixed_cell_and_composition" in runner
    assert "scientific_claim_allowed=false" in runner
    assert "sha256sum -c INPUT_SHA256SUMS.txt" in runner


def test_runner_separates_recoverable_zbrent_from_fatal_markers() -> None:
    runner = (ASSET_DIR / "run_vasp_two_step.sh").read_text()
    fatal_pattern_line = next(
        line for line in runner.splitlines() if line.startswith("FATAL_MARKER_PATTERN=")
    )
    lowered = fatal_pattern_line.lower()

    assert "zbrent" not in lowered
    assert "brmix" in lowered
    assert "edddav" in lowered
    assert "chargedensity file is incomplete" in lowered
    assert "zhegv.*failed" in lowered
    assert "mpi[_[:space:]-]*abort" in lowered
    assert "segmentation fault" in lowered
    assert "DIAGNOSTIC_MARKER_PATTERN='ZBRENT'" in runner
    assert "record_diagnostic_markers relax relax.output OUTCAR" in runner
    assert "record_diagnostic_markers static static.output OUTCAR" in runner


def test_successful_run_fails_closed_when_result_packaging_fails() -> None:
    runner = (ASSET_DIR / "run_vasp_two_step.sh").read_text()

    assert 'if [[ "$exit_code" -eq 0 && "$bundle_exit" -ne 0 ]]' in runner
    assert "trap - EXIT" in runner
    assert "exit 21" in runner


def test_pilot_assets_do_not_submit_by_themselves() -> None:
    for name in (
        "build_upload_bundle.sh",
        "prepare_licensed_potcar.sh",
        "run_vasp_two_step.sh",
    ):
        script = (ASSET_DIR / name).read_text()
        assert "tefs hpc" not in script
        assert "tefs-ai task submit" not in script


def test_legacy_multi_phase_submit_route_is_fail_closed() -> None:
    script = (ROOT / "cloud" / "03_submit_hfo2_smoke.sh").read_text()

    assert "legacy multi-phase route is dry-run only" in script
    assert "exit 64" in script
    assert '"spot_paid": False' in script
    assert '"public_ip": False' in script
    assert "--kill-after=60s 30m" in script
    assert "tefs hpc" not in script
    assert '"spot_paid": True' not in script
