#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RUN_ROOT="${RUN_ROOT:-runs/hfo2_phase_smoke}"
OUT="${OUT:-hfo2_phase_smoke_results.csv}"

echo "job_id,phase,final_energy_eV,formula_units,energy_per_fu_eV,converged,notes" > "$OUT"

job_id="$(basename "$(find data/computation/validation_jobs -maxdepth 1 -type d -name 'cjob_*__phase_stability' | sort | head -1)" | sed 's/__phase_stability//')"

for phase_dir in "$RUN_ROOT"/*; do
  [[ -d "$phase_dir" ]] || continue
  phase="$(basename "$phase_dir")"
  energy=""
  converged="false"
  notes="missing_energy"
  if [[ -f "$phase_dir/OUTCAR" ]]; then
    energy="$(grep 'free  energy   TOTEN' "$phase_dir/OUTCAR" | tail -1 | awk '{print $5}')"
    if grep -q 'General timing and accounting informations' "$phase_dir/OUTCAR"; then
      converged="true"
    fi
  elif [[ -f "$phase_dir/OSZICAR" ]]; then
    energy="$(grep 'E0=' "$phase_dir/OSZICAR" | tail -1 | awk '{for(i=1;i<=NF;i++) if($i==\"E0=\") print $(i+1)}')"
  fi
  if [[ -n "$energy" ]]; then
    notes="raw_total_energy_needs_formula_unit_normalization"
  fi
  echo "$job_id,$phase,$energy,,,$converged,$notes" >> "$OUT"
done

echo "Wrote $OUT"
cat "$OUT"
