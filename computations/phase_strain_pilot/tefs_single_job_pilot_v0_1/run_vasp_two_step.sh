#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

VASP_BIN="${TEFS_VASP_BIN:-/usr/local/vasp.6.3.0/bin/vasp_std}"
NPROCS="${TEFS_NTASKS_PER_NODE:-16}"
RESULT_ARCHIVE="result_bundle_no_potcar.tar.gz"
FATAL_MARKER_PATTERN='BRMIX|EDDDAV|VERY BAD NEWS|internal error|chargedensity file is incomplete|charge density.*incomplete|ZHEGV.*failed|MPI[_[:space:]-]*ABORT|segmentation fault'
DIAGNOSTIC_MARKER_PATTERN='ZBRENT'

mkdir -p inputs provenance results/relax results/static
: > result_status.txt
: > run_environment.txt
: > provenance/VASP_DIAGNOSTICS.txt
cat > claim_scope.txt <<'EOF'
purpose=link_pilot_only
scientific_claim_allowed=false
publication_ready=false
EOF

write_status() {
  printf '%s\n' "$1" > result_status.txt
}

save_stage() {
  local stage="$1"
  local output_dir="results/$stage"
  local file

  for file in OUTCAR OSZICAR CONTCAR vasprun.xml vaspout.h5 XDATCAR DOSCAR EIGENVAL IBZKPT PCDAT REPORT; do
    if [[ -f "$file" ]]; then
      cp -f "$file" "$output_dir/$file"
    fi
  done
  cp -f INCAR "$output_dir/INCAR"
  cp -f POSCAR "$output_dir/POSCAR"
  cp -f KPOINTS "$output_dir/KPOINTS"
  if [[ -f "$stage.output" ]]; then
    cp -f "$stage.output" "$output_dir/$stage.output"
  fi
}

bundle_results() {
  local exit_code=$?
  local bundle_exit=0
  set +e
  printf 'exit_code=%s\n' "$exit_code" >> result_status.txt || bundle_exit=$?
  if [[ "$bundle_exit" -eq 0 ]]; then
    tar -czf "${RESULT_ARCHIVE}.tmp" \
      result_status.txt run_environment.txt claim_scope.txt inputs provenance results \
      job_manifest.json INPUT_SHA256SUMS.txt run_vasp_two_step.sh tefs_hpc.preview.json
    bundle_exit=$?
  fi
  if [[ "$bundle_exit" -eq 0 ]]; then
    mv -f "${RESULT_ARCHIVE}.tmp" "$RESULT_ARCHIVE"
    bundle_exit=$?
  fi

  trap - EXIT
  if [[ "$exit_code" -eq 0 && "$bundle_exit" -ne 0 ]]; then
    exit 21
  fi
  exit "$exit_code"
}
trap bundle_results EXIT

if [[ ! -f INPUT_SHA256SUMS.txt ]] || ! sha256sum -c INPUT_SHA256SUMS.txt; then
  write_status "failed: frozen input checksum verification"
  exit 20
fi

for required in POSCAR POTCAR KPOINTS INCAR.relax INCAR.static; do
  if [[ ! -s "$required" ]]; then
    write_status "failed: missing or empty $required"
    exit 2
  fi
done

for command_name in mpirun tar sha256sum awk grep; do
  if ! command -v "$command_name" >/dev/null; then
    write_status "failed: required command unavailable: $command_name"
    exit 8
  fi
done

if ! df -Pk . | awk 'NR == 2 {exit !($4 >= 2097152)}'; then
  write_status "failed: less than 2 GiB free in the job directory"
  exit 9
fi

if [[ ! "$NPROCS" =~ ^[1-9][0-9]*$ ]]; then
  write_status "failed: invalid MPI rank count"
  exit 3
fi
if [[ ! -x "$VASP_BIN" ]]; then
  write_status "failed: VASP binary not executable"
  exit 4
fi

cp -f POSCAR inputs/POSCAR.original
cp -f KPOINTS inputs/KPOINTS
cp -f INCAR.relax inputs/INCAR.relax
cp -f INCAR.static inputs/INCAR.static
sha256sum POTCAR > provenance/POTCAR_SHA256.txt
grep 'TITEL' POTCAR > provenance/POTCAR_TITEL.txt

check_fatal_markers() {
  local output_file="$1"
  local outcar_file="$2"
  if grep -Eiq "$FATAL_MARKER_PATTERN" \
      "$output_file" "$outcar_file"; then
    return 1
  fi
}

record_diagnostic_markers() {
  local stage="$1"
  local output_file="$2"
  local outcar_file="$3"
  local diagnostic_count
  local diagnostic_files=()

  [[ -f "$output_file" ]] && diagnostic_files+=("$output_file")
  [[ -f "$outcar_file" ]] && diagnostic_files+=("$outcar_file")
  if [[ "${#diagnostic_files[@]}" -eq 0 ]]; then
    diagnostic_count=0
  else
    diagnostic_count="$(
      awk -v marker="$DIAGNOSTIC_MARKER_PATTERN" \
        'index(tolower($0), tolower(marker)) {count += 1} END {print count + 0}' \
        "${diagnostic_files[@]}"
    )"
  fi
  printf '%s_zbrent_count=%s\n' "$stage" "$diagnostic_count" \
    >> provenance/VASP_DIAGNOSTICS.txt
}

verify_fixed_cell_and_composition() {
  awk '
    FNR == 1 {file_number += 1}
    file_number == 1 && FNR == 2 {scale_a = $1}
    file_number == 2 && FNR == 2 {scale_b = $1}
    file_number == 1 && FNR >= 3 && FNR <= 5 {
      for (column = 1; column <= 3; column += 1) {
        lattice_a[FNR, column] = $column
      }
    }
    file_number == 2 && FNR >= 3 && FNR <= 5 {
      for (column = 1; column <= 3; column += 1) {
        lattice_b[FNR, column] = $column
      }
    }
    file_number == 2 && FNR == 6 {
      composition_ok = ($1 == "Hf" && $2 == "O" && NF == 2)
    }
    file_number == 2 && FNR == 7 {
      counts_ok = ($1 == 4 && $2 == 8 && NF == 2)
    }
    END {
      if (!composition_ok || !counts_ok) exit 1
      for (row = 3; row <= 5; row += 1) {
        for (column = 1; column <= 3; column += 1) {
          delta = scale_a * lattice_a[row, column] - scale_b * lattice_b[row, column]
          if (delta < 0) delta = -delta
          if (delta > 1e-8) exit 2
        }
      }
    }
  ' inputs/POSCAR.original CONTCAR
}

{
  printf 'started_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'vasp_binary=%s\n' "$VASP_BIN"
  printf 'mpi_ranks=%s\n' "$NPROCS"
  printf 'working_directory=%s\n' "$(pwd -P)"
  uname -a
  mpirun --version | head -n 1
} > run_environment.txt

write_status "running: relaxation"
cp -f INCAR.relax INCAR
set +e
mpirun -n "$NPROCS" --allow-run-as-root "$VASP_BIN" > relax.output 2>&1
relax_exit=$?
set -e
save_stage relax
record_diagnostic_markers relax relax.output OUTCAR

if [[ "$relax_exit" -ne 0 ]]; then
  write_status "failed: relaxation VASP exit code $relax_exit"
  exit 10
fi
if ! grep -q 'reached required accuracy' relax.output; then
  write_status "failed: ionic relaxation did not report required accuracy"
  exit 11
fi
if [[ ! -s OUTCAR ]] || ! grep -q 'General timing and accounting informations' OUTCAR; then
  write_status "failed: relaxation OUTCAR lacks completion footer"
  exit 12
fi
if ! check_fatal_markers relax.output OUTCAR; then
  write_status "failed: relaxation contains a fatal VASP marker"
  exit 13
fi
if [[ ! -s CONTCAR ]]; then
  write_status "failed: relaxation produced no CONTCAR"
  exit 14
fi
if ! verify_fixed_cell_and_composition; then
  write_status "failed: relaxation changed fixed cell or Hf4O8 composition"
  exit 15
fi

sha256sum -c provenance/POTCAR_SHA256.txt >/dev/null
cp -f CONTCAR POSCAR
write_status "running: static"
cp -f INCAR.static INCAR
set +e
mpirun -n "$NPROCS" --allow-run-as-root "$VASP_BIN" > static.output 2>&1
static_exit=$?
set -e
save_stage static
record_diagnostic_markers static static.output OUTCAR

if [[ "$static_exit" -ne 0 ]]; then
  write_status "failed: static VASP exit code $static_exit"
  exit 16
fi
if [[ ! -s OUTCAR ]] || ! grep -q 'General timing and accounting informations' OUTCAR; then
  write_status "failed: static OUTCAR lacks completion footer"
  exit 17
fi
if ! grep -q 'aborting loop because EDIFF is reached' OUTCAR; then
  write_status "failed: static OUTCAR lacks EDIFF convergence marker"
  exit 18
fi
if ! check_fatal_markers static.output OUTCAR; then
  write_status "failed: static calculation contains a fatal VASP marker"
  exit 19
fi

printf 'finished_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> run_environment.txt
write_status "success: relax and static completed"
