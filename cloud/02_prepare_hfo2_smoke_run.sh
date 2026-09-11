#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

JOB_DIR="${JOB_DIR:-}"
if [[ -z "$JOB_DIR" ]]; then
  JOB_DIR="$(find data/computation/validation_jobs -maxdepth 1 -type d -name 'cjob_*__phase_stability' | sort | head -1)"
fi

if [[ -z "$JOB_DIR" || ! -d "$JOB_DIR" ]]; then
  echo "No phase-stability job directory found."
  exit 2
fi

if [[ -z "${TEFS_POTCAR_HFO2:-}" ]]; then
  cat <<'EOF'
TEFS_POTCAR_HFO2 is not set.

Create or locate a licensed HfO2 POTCAR on the cloud machine, then run for example:

  export TEFS_POTCAR_HFO2=/root/POTCAR/HfO2/POTCAR

Do not copy POTCAR back to the local project or GitHub.
EOF
  exit 2
fi

if [[ ! -f "$TEFS_POTCAR_HFO2" ]]; then
  echo "POTCAR file not found: $TEFS_POTCAR_HFO2"
  exit 2
fi

RUN_ROOT="${RUN_ROOT:-runs/hfo2_phase_smoke}"
mkdir -p "$RUN_ROOT"

echo "Source job: $JOB_DIR"
echo "Run root: $RUN_ROOT"

for phase in monoclinic tetragonal cubic orthorhombic; do
  src="$JOB_DIR/manual_structures/$phase/POSCAR"
  if [[ ! -f "$src" ]]; then
    echo "Missing POSCAR for $phase: $src"
    echo "Run cloud/01_import_manual_structures.sh after uploading the four POSCAR files."
    exit 2
  fi
  dest="$RUN_ROOT/$phase"
  mkdir -p "$dest"
  cp "$src" "$dest/POSCAR"
  cp "$TEFS_POTCAR_HFO2" "$dest/POTCAR"
  cp "$JOB_DIR/templates/KPOINTS" "$dest/KPOINTS"
  cp "$JOB_DIR/templates/INCAR.relax" "$dest/INCAR.relax"
  cp "$JOB_DIR/templates/INCAR.static" "$dest/INCAR.static"
  cat > "$dest/run_vasp_two_step.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

VASP_BIN="${TEFS_VASP_BIN:-/usr/local/vasp.6.3.0/bin/vasp_std}"
NPROCS="${TEFS_NTASKS_PER_NODE:-1}"

save_outputs() {
  stage="$1"
  out_dir="results/$stage"
  mkdir -p "$out_dir"
  for file in OUTCAR OSZICAR CONTCAR vasprun.xml vaspout.h5 XDATCAR DOSCAR EIGENVAL IBZKPT PCDAT REPORT; do
    if [[ -e "$file" ]]; then
      cp -f "$file" "$out_dir/$file"
    fi
  done
  cp -f INCAR "$out_dir/INCAR"
  cp -f POSCAR "$out_dir/POSCAR"
  cp -f KPOINTS "$out_dir/KPOINTS"
  if [[ -f "$stage.output" ]]; then
    cp -f "$stage.output" "$out_dir/$stage.output"
  fi
}

cp INCAR.relax INCAR
mpirun -n "$NPROCS" "$VASP_BIN" > relax.output 2>&1
save_outputs relax

if [[ -s CONTCAR ]]; then
  cp CONTCAR POSCAR
else
  echo "CONTCAR is missing or empty after relaxation." >&2
  exit 3
fi

cp INCAR.static INCAR
mpirun -n "$NPROCS" "$VASP_BIN" > static.output 2>&1
save_outputs static
EOF
  chmod +x "$dest/run_vasp_two_step.sh"
  cat > "$dest/README_TEFS.md" <<EOF
# HfO2 $phase TEFS run

Run with:

\`\`\`bash
bash run_vasp_two_step.sh
\`\`\`

This folder intentionally receives POTCAR only on the cloud machine.
EOF
done

echo "Prepared four phase folders under $RUN_ROOT."
find "$RUN_ROOT" -maxdepth 2 -type f | sort
