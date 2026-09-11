#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_DIR="$ROOT/data/runtime/publication_v23"
LOG_DIR="$ROOT/logs/publication_v23"
LOCK_DIR="$RUNTIME_DIR/retry_sweep.lock"
STATUS_FILE="$RUNTIME_DIR/retry_sweep.status.json"
PID_FILE="$RUNTIME_DIR/retry_sweep.pid"
MAIN_STATUS_FILE="$RUNTIME_DIR/all_phases.status.json"
POLL_SECONDS="${HFO2_V23_RETRY_POLL_SECONDS:-300}"
MAX_SWEEPS="${HFO2_V23_MAX_RETRY_SWEEPS:-3}"
PHASES=(a_primary_dense b_targeted_science c_primary_context d_review_secondary)

mkdir -p "$RUNTIME_DIR" "$LOG_DIR"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  existing_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
    echo "Publication v2.3 retry sweep is already running (PID $existing_pid)." >&2
    exit 3
  fi
  rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR"
fi

exec >> "$LOG_DIR/retry_sweep.log" 2>&1

STARTED_AT="$(date '+%Y-%m-%dT%H:%M:%S%z')"
CURRENT_PHASE="waiting_for_main_pipeline"
FINAL_STATUS="failed_or_interrupted"
REMAINING_ERRORS=0
REMAINING_MISSING=0
REMAINING_ISSUES=0
echo "$$" > "$PID_FILE"

write_status() {
  local status="$1"
  local updated_at
  updated_at="$(date '+%Y-%m-%dT%H:%M:%S%z')"
  printf '{"queue_version":"publication-evidence-packet-v2.3","status":"%s","pid":%s,"current_phase":"%s","started_at":"%s","updated_at":"%s","remaining_errors":%s,"remaining_missing":%s,"remaining_issues":%s,"max_sweeps":%s}\n' \
    "$status" "$$" "$CURRENT_PHASE" "$STARTED_AT" "$updated_at" "$REMAINING_ERRORS" "$REMAINING_MISSING" "$REMAINING_ISSUES" "$MAX_SWEEPS" > "$STATUS_FILE"
}

read_main_status() {
  "$ROOT/.venv/bin/python" - "$MAIN_STATUS_FILE" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except (OSError, ValueError, json.JSONDecodeError):
    print("missing")
else:
    print(payload.get("status") or "unknown")
PY
}

count_retryable_issues() {
  "$ROOT/.venv/bin/python" - "$ROOT" <<'PY'
import csv
import sqlite3
import sys
from pathlib import Path

root = Path(sys.argv[1])
queue_dir = root / "data" / "extraction_queues" / "publication_v3"
chunk_ids = set()
for phase in ("a_primary_dense", "b_targeted_science", "c_primary_context", "d_review_secondary"):
    with (queue_dir / f"phase_{phase}.csv").open(encoding="utf-8-sig") as handle:
        chunk_ids.update(
            str(row.get("chunk_id") or "").strip()
            for row in csv.DictReader(handle)
            if str(row.get("chunk_id") or "").strip()
        )

with sqlite3.connect(root / "data" / "hfo2_ferrokg.sqlite3") as conn:
    conn.execute("CREATE TEMP TABLE retry_scope (chunk_id TEXT PRIMARY KEY)")
    conn.executemany("INSERT INTO retry_scope (chunk_id) VALUES (?)", ((value,) for value in chunk_ids))
    error_count = conn.execute(
        """
        SELECT COUNT(DISTINCT ec.chunk_id)
        FROM extraction_candidates ec
        JOIN retry_scope rs ON rs.chunk_id = ec.chunk_id
        WHERE ec.ontology_version = 'hfo2-ferrokg-v2.3'
          AND ec.status = 'extraction_error'
        """
    ).fetchone()[0]
    missing_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM retry_scope rs
        WHERE NOT EXISTS (
            SELECT 1
            FROM extraction_candidates ec
            WHERE ec.chunk_id = rs.chunk_id
              AND ec.ontology_version = 'hfo2-ferrokg-v2.3'
        )
        """
    ).fetchone()[0]
print(int(error_count), int(missing_count), int(error_count + missing_count))
PY
}

refresh_issue_counts() {
  read -r REMAINING_ERRORS REMAINING_MISSING REMAINING_ISSUES < <(count_retryable_issues)
}

cleanup() {
  local exit_code=$?
  if [[ "$FINAL_STATUS" == "failed_or_interrupted" ]] && [[ "$exit_code" -eq 0 ]]; then
    FINAL_STATUS="complete"
  fi
  write_status "$FINAL_STATUS"
  rm -rf "$LOCK_DIR"
  rm -f "$PID_FILE"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

printf '===== publication_v23:retry_sweep started %s =====\n' "$(date '+%Y-%m-%dT%H:%M:%S')"
write_status "waiting_for_main_pipeline"

while true; do
  main_status="$(read_main_status)"
  refresh_issue_counts
  case "$main_status" in
    complete)
      break
      ;;
    paused_quota_or_provider)
      FINAL_STATUS="waiting_for_quota_or_provider"
      echo "Main pipeline paused by quota/provider state; retry sweep will not call the API."
      exit 75
      ;;
    failed|failed_or_interrupted)
      FINAL_STATUS="waiting_for_main_pipeline_recovery"
      echo "Main pipeline is not complete ($main_status); retry sweep is stopping safely."
      exit 1
      ;;
    *)
      write_status "waiting_for_main_pipeline"
      sleep "$POLL_SECONDS"
      ;;
  esac
done

for sweep in $(seq 1 "$MAX_SWEEPS"); do
  refresh_issue_counts
  echo "Retry sweep $sweep/$MAX_SWEEPS: $REMAINING_ERRORS errors, $REMAINING_MISSING missing chunks ($REMAINING_ISSUES total issues)."
  if [[ "$REMAINING_ISSUES" -eq 0 ]]; then
    CURRENT_PHASE="complete"
    FINAL_STATUS="complete"
    exit 0
  fi

  for phase in "${PHASES[@]}"; do
    CURRENT_PHASE="$phase"
    write_status "running_retry_sweep"
    while true; do
      set +e
      bash "$ROOT/scripts/run_publication_v23_phase.sh" "$phase"
      exit_code=$?
      set -e
      if [[ "$exit_code" -eq 0 ]]; then
        break
      fi
      if [[ "$exit_code" -eq 75 ]]; then
        FINAL_STATUS="paused_quota_or_provider"
        exit 75
      fi
      if [[ "$exit_code" -eq 76 ]]; then
        write_status "transient_backoff"
        sleep "$POLL_SECONDS"
        continue
      fi
      FINAL_STATUS="failed"
      exit "$exit_code"
    done
  done
done

refresh_issue_counts
if [[ "$REMAINING_ISSUES" -eq 0 ]]; then
  CURRENT_PHASE="complete"
  FINAL_STATUS="complete"
  exit 0
fi

CURRENT_PHASE="manual_review_required"
FINAL_STATUS="incomplete_after_retry_limit"
echo "$REMAINING_ERRORS errors and $REMAINING_MISSING missing chunks remain after $MAX_SWEEPS retry sweeps."
exit 4
