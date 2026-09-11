#!/usr/bin/env bash
set -euo pipefail

echo "== TEFS shell check =="
pwd
echo

echo "== Important directories =="
for d in /root/home /root/public /root/experiments "$HOME/POTCAR"; do
  if [[ -e "$d" ]]; then
    ls -ld "$d"
  else
    echo "missing: $d"
  fi
done
echo

echo "== TEFS client =="
if command -v tefs >/dev/null 2>&1; then
  tefs || true
else
  echo "tefs command not found"
fi
echo

echo "== Queue snapshot =="
tefs squeue || true
echo

echo "== Available images and CVM types =="
echo "Run these manually if needed:"
echo "  tefs images"
echo "  tefs cvm"
echo

echo "== Current configurable variables =="
cat <<EOF
TEFS_REGION=${TEFS_REGION:-ap-beijing}
TEFS_INSTANCE_TYPE=${TEFS_INSTANCE_TYPE:-unset}
TEFS_IMAGE_ID=${TEFS_IMAGE_ID:-unset}
TEFS_NODE=${TEFS_NODE:-1}
TEFS_NTASKS_PER_NODE=${TEFS_NTASKS_PER_NODE:-1}
TEFS_DISK_SIZE=${TEFS_DISK_SIZE:-200}
TEFS_POTCAR_HFO2=${TEFS_POTCAR_HFO2:-unset}
TEFS_VASP_BIN=${TEFS_VASP_BIN:-/usr/local/vasp.6.3.0/bin/vasp_std}
EOF
