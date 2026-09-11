#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PHASE="${1:-a_primary_dense}"
WORKERS="${HFO2_V23_WORKERS:-2}"
LLM_MAX_TOKENS="${HFO2_V23_LLM_MAX_TOKENS:-6000}"
COMMIT_EVERY="${HFO2_V23_COMMIT_EVERY:-1}"
PROGRESS_EVERY="${HFO2_V23_PROGRESS_EVERY:-5}"
RUNTIME_DIR="$ROOT/data/runtime/publication_v23"
LOG_DIR="$ROOT/logs/publication_v23"
LOCK_DIR="$RUNTIME_DIR/${PHASE}.lock"

case "$PHASE" in
  smoke)
    QUEUE="$ROOT/data/extraction_queues/publication_v3/publication_smoke_chunks.csv"
    ;;
  a_primary_dense|b_targeted_science|c_primary_context|d_review_secondary)
    QUEUE="$ROOT/data/extraction_queues/publication_v3/phase_${PHASE}.csv"
    ;;
  *)
    echo "Unknown phase: $PHASE" >&2
    echo "Allowed: smoke, a_primary_dense, b_targeted_science, c_primary_context, d_review_secondary" >&2
    exit 2
    ;;
esac

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

FINALIZED=0
cleanup() {
  EXIT_CODE=$?
  if [[ "$FINALIZED" -eq 0 ]]; then
    FINISHED_AT="$(date '+%Y-%m-%dT%H:%M:%S%z')"
    LOG_FINISHED_AT="$(date '+%Y-%m-%dT%H:%M:%S')"
    printf '===== publication_v23:%s exit_code=%s ended %s =====\n' "$PHASE" "$EXIT_CODE" "$LOG_FINISHED_AT"
    printf '{"queue_version":"publication-evidence-packet-v2.3","phase":"%s","status":"failed_or_interrupted","pid":%s,"started_at":"%s","finished_at":"%s","exit_code":%s,"queue":"%s","workers":%s}\n' \
      "$PHASE" "$$" "$STARTED_AT" "$FINISHED_AT" "$EXIT_CODE" "$QUEUE" "$WORKERS" > "$RUNTIME_DIR/${PHASE}.status.json"
  fi
  rm -rf "$LOCK_DIR"
  rm -f "$RUNTIME_DIR/${PHASE}.pid"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

STARTED_AT="$(date '+%Y-%m-%dT%H:%M:%S%z')"
LOG_STARTED_AT="$(date '+%Y-%m-%dT%H:%M:%S')"
printf '===== publication_v23:%s started %s =====\n' "$PHASE" "$LOG_STARTED_AT"
echo "$$" > "$RUNTIME_DIR/${PHASE}.pid"
printf '{"queue_version":"publication-evidence-packet-v2.3","phase":"%s","status":"running","pid":%s,"started_at":"%s","queue":"%s","workers":%s}\n' \
  "$PHASE" "$$" "$STARTED_AT" "$QUEUE" "$WORKERS" > "$RUNTIME_DIR/${PHASE}.status.json"

cd "$ROOT"
"$ROOT/.venv/bin/python" pipelines/54_prepare_extraction_retry.py \
  --chunk-list "$QUEUE" \
  --ontology-version hfo2-ferrokg-v2.3 \
  --apply

EXTRACTION_COMMAND=(
  env PYTHONUNBUFFERED=1
  "$ROOT/.venv/bin/python" pipelines/05_run_extraction.py
  --chunk-list "$QUEUE" \
  --all-chunks \
  --incremental \
  --model qwen3.7-max \
  --commit-every "$COMMIT_EVERY" \
  --progress-every "$PROGRESS_EVERY" \
  --max-workers "$WORKERS" \
  --llm-strict \
  --no-llm-thinking \
  --llm-max-tokens "$LLM_MAX_TOKENS" \
  --llm-timeout-seconds 120 \
  --llm-context-chars 400
)
if [[ -n "${HFO2_V23_LIMIT_CHUNKS:-}" ]]; then
  EXTRACTION_COMMAND+=(--limit-chunks "$HFO2_V23_LIMIT_CHUNKS")
fi

set +e
"${EXTRACTION_COMMAND[@]}"
EXIT_CODE=$?
set -e

FINISHED_AT="$(date '+%Y-%m-%dT%H:%M:%S%z')"
LOG_FINISHED_AT="$(date '+%Y-%m-%dT%H:%M:%S')"
if [[ "$EXIT_CODE" -eq 0 ]]; then
  STATUS="complete"
elif [[ "$EXIT_CODE" -eq 75 ]]; then
  STATUS="paused_quota_or_provider"
elif [[ "$EXIT_CODE" -eq 76 ]]; then
  STATUS="paused_transient_provider"
else
  STATUS="failed"
fi
printf '{"queue_version":"publication-evidence-packet-v2.3","phase":"%s","status":"%s","pid":%s,"started_at":"%s","finished_at":"%s","exit_code":%s,"queue":"%s","workers":%s}\n' \
  "$PHASE" "$STATUS" "$$" "$STARTED_AT" "$FINISHED_AT" "$EXIT_CODE" "$QUEUE" "$WORKERS" > "$RUNTIME_DIR/${PHASE}.status.json"
printf '===== publication_v23:%s exit_code=%s ended %s =====\n' "$PHASE" "$EXIT_CODE" "$LOG_FINISHED_AT"
FINALIZED=1
exit "$EXIT_CODE"
