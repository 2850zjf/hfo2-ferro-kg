#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RUN_ID="${1:-llm_retry_watch_$(date +%Y%m%d_%H%M%S)}"
LOG_DIR="logs/llm_retry_watch_${RUN_ID}"
mkdir -p "$LOG_DIR"
WATCH_LOG="${LOG_DIR}/watch.log"

MAX_BACKOFF_SECONDS="${MAX_BACKOFF_SECONDS:-900}"
INITIAL_BACKOFF_SECONDS="${INITIAL_BACKOFF_SECONDS:-120}"
SUCCESS_SLEEP_SECONDS="${SUCCESS_SLEEP_SECONDS:-300}"
ATTEMPT=0
BACKOFF="$INITIAL_BACKOFF_SECONDS"

timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

echo "watch_run_id=${RUN_ID}" | tee -a "$WATCH_LOG"
echo "started_at=$(timestamp)" | tee -a "$WATCH_LOG"

while true; do
  ATTEMPT=$((ATTEMPT + 1))
  echo "attempt=${ATTEMPT} started_at=$(timestamp) backoff=${BACKOFF}" | tee -a "$WATCH_LOG"

  set +e
  HFO2_FERROKG_LLM_ENABLE_THINKING="${HFO2_FERROKG_LLM_ENABLE_THINKING:-false}" \
  HFO2_FERROKG_LLM_TIMEOUT_SECONDS="${HFO2_FERROKG_LLM_TIMEOUT_SECONDS:-45}" \
  HFO2_FERROKG_LLM_MAX_TOKENS="${HFO2_FERROKG_LLM_MAX_TOKENS:-2500}" \
  HFO2_FERROKG_LLM_MAX_CHUNK_RETRIES="${HFO2_FERROKG_LLM_MAX_CHUNK_RETRIES:-3}" \
  PREPARE_QUEUE=0 \
  MAX_WORKERS="${MAX_WORKERS:-1}" \
  STRICT_LLM=1 \
  scripts/run_llm_retry_extraction.sh "$RUN_ID" >> "$WATCH_LOG" 2>&1
  status=$?
  set -e

  echo "attempt=${ATTEMPT} exited_at=$(timestamp) status=${status}" | tee -a "$WATCH_LOG"

  if [[ "$status" == "0" ]]; then
    BACKOFF="$INITIAL_BACKOFF_SECONDS"
    echo "attempt=${ATTEMPT} completed; sleeping ${SUCCESS_SLEEP_SECONDS}s before checking again" | tee -a "$WATCH_LOG"
    sleep "$SUCCESS_SLEEP_SECONDS"
  else
    echo "attempt=${ATTEMPT} failed_or_paused; sleeping ${BACKOFF}s then retrying" | tee -a "$WATCH_LOG"
    sleep "$BACKOFF"
    BACKOFF=$((BACKOFF * 2))
    if [[ "$BACKOFF" -gt "$MAX_BACKOFF_SECONDS" ]]; then
      BACKOFF="$MAX_BACKOFF_SECONDS"
    fi
  fi
done
