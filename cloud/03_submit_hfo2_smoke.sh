#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SUBMIT=0
if [[ "${1:-}" == "--submit" ]]; then
  SUBMIT=1
fi

RUN_ROOT="${RUN_ROOT:-runs/hfo2_phase_smoke}"
REGION="${TEFS_REGION:-ap-beijing}"
INSTANCE_TYPE="${TEFS_INSTANCE_TYPE:-}"
IMAGE_ID="${TEFS_IMAGE_ID:-}"
NODE="${TEFS_NODE:-1}"
NTASKS="${TEFS_NTASKS_PER_NODE:-1}"
DISK_SIZE="${TEFS_DISK_SIZE:-200}"
VASP_BIN="${TEFS_VASP_BIN:-/usr/local/vasp.6.3.0/bin/vasp_std}"

if [[ ! -d "$RUN_ROOT" ]]; then
  echo "Run root missing: $RUN_ROOT"
  echo "Run cloud/02_prepare_hfo2_smoke_run.sh first."
  exit 2
fi

if [[ -z "$INSTANCE_TYPE" || -z "$IMAGE_ID" ]]; then
  cat <<'EOF'
Set TEFS_INSTANCE_TYPE and TEFS_IMAGE_ID before submission.

Use these TEFS commands in Cloud Shell:

  tefs cvm
  tefs images

Then export values, for example:

  export TEFS_REGION=ap-beijing
  export TEFS_INSTANCE_TYPE=GN10Xp.2XLARGE40
  export TEFS_IMAGE_ID=img-g3gm0m37
  export TEFS_NTASKS_PER_NODE=1

Dry-run again before real submission.
EOF
  exit 2
fi

echo "Submission mode: $([[ "$SUBMIT" == 1 ]] && echo real-submit || echo dry-run)"

for phase_dir in "$RUN_ROOT"/*; do
  [[ -d "$phase_dir" ]] || continue
  phase="$(basename "$phase_dir")"
  json_path="$phase_dir/tefs_hpc.json"
  PHASE="$phase" \
  NODE="$NODE" \
  INSTANCE_TYPE="$INSTANCE_TYPE" \
  IMAGE_ID="$IMAGE_ID" \
  REGION="$REGION" \
  NTASKS="$NTASKS" \
  DISK_SIZE="$DISK_SIZE" \
  VASP_BIN="$VASP_BIN" \
  python3 - <<'PY' > "$json_path"
import json
import os

ntasks = os.environ["NTASKS"]
vasp_bin = os.environ["VASP_BIN"]
cmd = (
    f"export TEFS_NTASKS_PER_NODE={ntasks}; "
    f"export TEFS_VASP_BIN={vasp_bin}; "
    "bash run_vasp_two_step.sh"
)
payload = {
    "node": os.environ["NODE"],
    "instance_type": os.environ["INSTANCE_TYPE"],
    "image_id": os.environ["IMAGE_ID"],
    "region": os.environ["REGION"],
    "spot_paid": True,
    "public_ip": True,
    "disk_size": os.environ["DISK_SIZE"],
    "ntasks_per_node": ntasks,
    "cmd": cmd,
}
print(json.dumps(payload, indent=2))
PY
  echo
  echo "== $phase =="
  cat "$json_path"
  if [[ "$SUBMIT" == 1 ]]; then
    (
      cd "$phase_dir"
      tefs hpc tefs_hpc.json
    )
    sleep 3
  else
    echo "Dry run only. To submit: cloud/03_submit_hfo2_smoke.sh --submit"
  fi
done

echo
echo "Monitor:"
echo "  tefs squeue"
echo "  tefs get JOBID"
echo "  tefs rsync JOBID"
