#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_DIR="$ROOT/data/runtime/publication_v23"
LOG_DIR="$ROOT/logs/publication_v23"
LOCK_DIR="$RUNTIME_DIR/all_phases.lock"
STATUS_FILE="$RUNTIME_DIR/all_phases.status.json"
PID_FILE="$RUNTIME_DIR/all_phases.pid"
POLL_SECONDS="${HFO2_V23_POLL_SECONDS:-60}"
MAX_RETRIES="${HFO2_V23_MAX_RETRIES:-3}"
TRANSIENT_BACKOFF_SECONDS="${HFO2_V23_TRANSIENT_BACKOFF_SECONDS:-300}"
PHASES=(a_primary_dense b_targeted_science c_primary_context d_review_secondary)

mkdir -p "$RUNTIME_DIR" "$LOG_DIR"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  EXISTING_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$EXISTING_PID" ]] && kill -0 "$EXISTING_PID" 2>/dev/null; then
    echo "Publication v2.3 all-phase controller is already running (PID $EXISTING_PID)." >&2
    exit 3
  fi
  rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR"
fi

exec >> "$LOG_DIR/all_phases.log" 2>&1

STARTED_AT="$(date '+%Y-%m-%dT%H:%M:%S%z')"
CURRENT_PHASE="initializing"
FINAL_STATUS="failed_or_interrupted"
echo "$$" > "$PID_FILE"

write_status() {
  local status="$1"
  local updated_at
  updated_at="$(date '+%Y-%m-%dT%H:%M:%S%z')"
  printf '{"queue_version":"publication-evidence-packet-v2.3","status":"%s","pid":%s,"current_phase":"%s","started_at":"%s","updated_at":"%s","poll_seconds":%s,"max_retries":%s,"transient_backoff_seconds":%s}\n' \
    "$status" "$$" "$CURRENT_PHASE" "$STARTED_AT" "$updated_at" "$POLL_SECONDS" "$MAX_RETRIES" "$TRANSIENT_BACKOFF_SECONDS" > "$STATUS_FILE"
}

read_phase_status() {
  local phase="$1"
  "$ROOT/.venv/bin/python" - "$RUNTIME_DIR/${phase}.status.json" <<'PY'
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

phase_pid_is_active() {
  local phase="$1"
  local phase_pid
  phase_pid="$(cat "$RUNTIME_DIR/${phase}.pid" 2>/dev/null || true)"
  [[ -n "$phase_pid" ]] && kill -0 "$phase_pid" 2>/dev/null
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

printf '===== publication_v23:all_phases started %s =====\n' "$(date '+%Y-%m-%dT%H:%M:%S')"
write_status "running"

for phase in "${PHASES[@]}"; do
  CURRENT_PHASE="$phase"
  write_status "running"

  if phase_pid_is_active "$phase"; then
    echo "Waiting for already-running phase: $phase"
    while phase_pid_is_active "$phase"; do
      write_status "waiting_for_active_phase"
      sleep "$POLL_SECONDS"
    done
  fi

  phase_status="$(read_phase_status "$phase")"
  if [[ "$phase_status" == "complete" ]]; then
    echo "Skipping completed phase: $phase"
    continue
  fi
  if [[ "$phase_status" == "paused_quota_or_provider" ]]; then
    FINAL_STATUS="paused_quota_or_provider"
    echo "Stopping controller because $phase is paused by quota/provider state."
    exit 75
  fi

  attempt=1
  while [[ "$attempt" -le "$MAX_RETRIES" ]]; do
    echo "Starting phase $phase (attempt $attempt/$MAX_RETRIES)"
    set +e
    bash "$ROOT/scripts/run_publication_v23_phase.sh" "$phase"
    exit_code=$?
    set -e

    if [[ "$exit_code" -eq 0 ]]; then
      echo "Completed phase: $phase"
      break
    fi
    if [[ "$exit_code" -eq 75 ]]; then
      FINAL_STATUS="paused_quota_or_provider"
      echo "Stopping controller because $phase returned quota/provider pause."
      exit 75
    fi
    if [[ "$exit_code" -eq 76 ]]; then
      echo "Transient provider/network outage in $phase; retrying after ${TRANSIENT_BACKOFF_SECONDS}s."
      write_status "transient_backoff"
      sleep "$TRANSIENT_BACKOFF_SECONDS"
      continue
    fi
    if [[ "$attempt" -eq "$MAX_RETRIES" ]]; then
      CURRENT_PHASE="$phase"
      FINAL_STATUS="failed"
      echo "Phase $phase failed after $MAX_RETRIES attempts."
      exit "$exit_code"
    fi

    attempt=$((attempt + 1))
    echo "Phase $phase exited with $exit_code; retrying after ${POLL_SECONDS}s."
    write_status "retry_wait"
    sleep "$POLL_SECONDS"
  done
done

CURRENT_PHASE="complete"
FINAL_STATUS="complete"
printf '===== publication_v23:all_phases completed %s =====\n' "$(date '+%Y-%m-%dT%H:%M:%S')"
