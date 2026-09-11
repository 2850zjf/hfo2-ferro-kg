#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
SOURCE_DIR="$REPO_ROOT/computations/phase_strain_pilot/generated_v0_1/jobs/monoclinic/biaxial_0p0000"
OUTPUT_DIR="$SCRIPT_DIR/output"
BUNDLE_NAME="hfo2_m_biaxial_0p0000_workflow_smoke"
ARCHIVE_PATH="$OUTPUT_DIR/${BUNDLE_NAME}.tar.gz"

if [[ ! -d "$SOURCE_DIR" ]]; then
  echo "Frozen source job is missing: $SOURCE_DIR" >&2
  exit 2
fi

mkdir -p "$OUTPUT_DIR"
stage_root="$(mktemp -d "${TMPDIR:-/tmp}/hfo2-tefs-pilot.XXXXXX")"
trap 'rm -rf -- "$stage_root"' EXIT
stage_job="$stage_root/$BUNDLE_NAME"
mkdir -p "$stage_job"

for file in POSCAR KPOINTS INCAR.relax INCAR.static job_manifest.json POTCAR_NOT_INCLUDED.txt; do
  cp -f "$SOURCE_DIR/$file" "$stage_job/$file"
done
cp -f "$SCRIPT_DIR/run_vasp_two_step.sh" "$stage_job/run_vasp_two_step.sh"
cp -f "$SCRIPT_DIR/prepare_licensed_potcar.sh" "$stage_job/prepare_licensed_potcar.sh"
cp -f "$SCRIPT_DIR/tefs_hpc.preview.json" "$stage_job/tefs_hpc.preview.json"
chmod 700 "$stage_job/run_vasp_two_step.sh" "$stage_job/prepare_licensed_potcar.sh"

(
  cd "$stage_job"
  shasum -a 256 POSCAR KPOINTS INCAR.relax INCAR.static job_manifest.json \
    run_vasp_two_step.sh prepare_licensed_potcar.sh tefs_hpc.preview.json \
    > INPUT_SHA256SUMS.txt
)

STAGE_ROOT="$stage_root" ARCHIVE_PATH="$ARCHIVE_PATH" BUNDLE_NAME="$BUNDLE_NAME" \
python3 - <<'PY'
import gzip
import os
import tarfile
from pathlib import Path

stage_root = Path(os.environ["STAGE_ROOT"])
archive_path = Path(os.environ["ARCHIVE_PATH"])
bundle_name = os.environ["BUNDLE_NAME"]
bundle_root = stage_root / bundle_name
paths = [bundle_root, *sorted(bundle_root.rglob("*"), key=lambda p: p.as_posix())]

with archive_path.open("wb") as raw:
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for path in paths:
                arcname = path.relative_to(stage_root).as_posix()
                info = tar.gettarinfo(str(path), arcname=arcname)
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                info.mtime = 0
                info.pax_headers = {}
                if info.isdir():
                    info.mode = 0o755
                    tar.addfile(info)
                elif info.isfile():
                    info.mode = 0o700 if path.name.endswith(".sh") else 0o644
                    with path.open("rb") as stream:
                        tar.addfile(info, stream)
                else:
                    raise RuntimeError(f"Unsupported bundle entry: {path}")
PY

if tar -tzf "$ARCHIVE_PATH" | grep -qE '(^|/)POTCAR$'; then
  echo "Refusing bundle: licensed POTCAR was included." >&2
  exit 3
fi

if ! tar -xOf "$ARCHIVE_PATH" "$BUNDLE_NAME/tefs_hpc.preview.json" \
  | python3 -c 'import json, sys; payload = json.load(sys.stdin); expected = {"node": "1", "instance_type": "S6.4XLARGE32", "image_id": "img-45t2j6r7", "region": "ap-beijing", "spot_paid": False, "public_ip": False, "disk_size": "50", "ntasks_per_node": "16", "cmd": "command -v timeout >/dev/null && exec timeout --signal=TERM --kill-after=60s 30m bash ./run_vasp_two_step.sh"}; assert payload == expected'; then
  echo "Refusing bundle: TEFS descriptor is not the reviewed bounded on-demand configuration." >&2
  exit 4
fi

(
  cd "$OUTPUT_DIR"
  shasum -a 256 "${BUNDLE_NAME}.tar.gz" > "${BUNDLE_NAME}.tar.gz.sha256"
)
printf '%s\n' "$ARCHIVE_PATH"
cat "${ARCHIVE_PATH}.sha256"
