#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "Importing manually downloaded HfO2 structures into prepared job packages..."
"$PYTHON_BIN" pipelines/48_import_manual_structures.py
echo
echo "Import report:"
sed -n '1,160p' data/computation/manual_structure_intake/manual_structure_import_report.md
