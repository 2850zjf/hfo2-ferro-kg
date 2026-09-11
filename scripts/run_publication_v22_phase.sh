#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PHASE="${1:-a_primary_dense}"
WORKERS="${HFO2_V22_WORKERS:-4}"
QUEUE="$ROOT/data/extraction_queues/publication_v2/phase_${PHASE}.csv"
RUNTIME_DIR="$ROOT/data/runtime/publication_v22"
LOG_DIR="$ROOT/logs/publication_v22"
LOCK_DIR="$RUNTIME_DIR/${PHASE}.lock"

if [[ ! -f "$QUEUE" ]]; then
  echo "Queue not found: $QUEUE" >&2
  exit 2
fi

mkdir -p "$RUNTIME_DIR" "$LOG_DIR"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  EXISTING_PID="$(cat "$RUNTIME_DIR/${PHASE}.pid" 2>/dev/null || true)"
  if [[ -n "$EXISTING_PID" ]] && kill -0 "$EXISTING_PID" 2>/dev/null; then
    echo "Phase already has an active lock: $PHASE (PID $EXISTING_PID)" >&2
    exit 3
  fi
  rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR"
fi

LOG_FILE="$LOG_DIR/${PHASE}.log"
exec >> "$LOG_FILE" 2>&1

cleanup() {
  rm -rf "$LOCK_DIR"
  rm -f "$RUNTIME_DIR/${PHASE}.pid"
}
trap cleanup EXIT INT TERM

STARTED_AT="$(date '+%Y-%m-%dT%H:%M:%S%z')"
echo "$$" > "$RUNTIME_DIR/${PHASE}.pid"
printf '{"phase":"%s","status":"running","pid":%s,"started_at":"%s","queue":"%s","workers":%s}\n' \
  "$PHASE" "$$" "$STARTED_AT" "$QUEUE" "$WORKERS" > "$RUNTIME_DIR/${PHASE}.status.json"

cd "$ROOT"
"$ROOT/.venv/bin/python" pipelines/54_prepare_extraction_retry.py \
  --chunk-list "$QUEUE" \
  --apply
set +e
"$ROOT/.venv/bin/python" pipelines/05_run_extraction.py \
  --chunk-list "$QUEUE" \
  --incremental \
  --model qwen3.7-max \
  --commit-every 5 \
  --progress-every 10 \
  --max-workers "$WORKERS" \
  --llm-strict \
  --no-llm-thinking \
  --llm-max-tokens 4000 \
  --llm-timeout-seconds 90 \
  --llm-context-chars 400
EXIT_CODE=$?
set -e

FINISHED_AT="$(date '+%Y-%m-%dT%H:%M:%S%z')"
if [[ "$EXIT_CODE" -eq 0 ]]; then
  STATUS="complete"
elif [[ "$EXIT_CODE" -eq 75 ]]; then
  STATUS="paused"
else
  STATUS="failed"
fi
printf '{"phase":"%s","status":"%s","pid":%s,"started_at":"%s","finished_at":"%s","exit_code":%s,"queue":"%s","workers":%s}\n' \
  "$PHASE" "$STATUS" "$$" "$STARTED_AT" "$FINISHED_AT" "$EXIT_CODE" "$QUEUE" "$WORKERS" > "$RUNTIME_DIR/${PHASE}.status.json"
exit "$EXIT_CODE"
