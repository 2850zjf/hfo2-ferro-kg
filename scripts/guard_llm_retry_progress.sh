#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RUN_ID="${1:-llm_retry_guard_$(date +%Y%m%d_%H%M%S)}"
LOG_DIR="logs/llm_retry_guard_${RUN_ID}"
mkdir -p "$LOG_DIR"
GUARD_LOG="${LOG_DIR}/guard.log"

STALL_SECONDS="${STALL_SECONDS:-900}"
CHECK_SECONDS="${CHECK_SECONDS:-60}"
TERM_GRACE_SECONDS="${TERM_GRACE_SECONDS:-20}"
PROCESS_PATTERN="${PROCESS_PATTERN:-pipelines/05_run_extraction.py.*full_core_llm_retry}"
PROGRESS_FILES=(
  "data/extraction_candidates/hfo2_candidates.jsonl"
  "data/hfo2_ferrokg.sqlite3"
)

timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

file_mtime() {
  local path="$1"
  if [[ ! -e "$path" ]]; then
    echo 0
    return
  fi
  if stat -f "%m" "$path" >/dev/null 2>&1; then
    stat -f "%m" "$path"
  else
    stat -c "%Y" "$path"
  fi
}

latest_progress_mtime() {
  local latest=0
  local mtime=0
  local path
  for path in "${PROGRESS_FILES[@]}"; do
    mtime="$(file_mtime "$path")"
    if [[ "$mtime" -gt "$latest" ]]; then
      latest="$mtime"
    fi
  done
  echo "$latest"
}

running_extraction_pids() {
  pgrep -f "$PROCESS_PATTERN" || true
}

terminate_stale_extractors() {
  local pids="$1"
  echo "$(timestamp) deferring current LLM chunk before terminating stale extractor" | tee -a "$GUARD_LOG"
  .venv/bin/python pipelines/43_defer_current_llm_chunk.py \
    --reason "No extraction candidate writes for ${STALL_SECONDS}s; deferred by progress guard." \
    --failure-count "${HFO2_FERROKG_LLM_MAX_CHUNK_RETRIES:-3}" \
    --max-retries "${HFO2_FERROKG_LLM_MAX_CHUNK_RETRIES:-3}" \
    >> "$GUARD_LOG" 2>&1 || true
  local pid
  for pid in $pids; do
    echo "$(timestamp) stale extractor pid=${pid}; sending TERM" | tee -a "$GUARD_LOG"
    kill -TERM "$pid" 2>/dev/null || true
  done
  sleep "$TERM_GRACE_SECONDS"
  for pid in $pids; do
    if kill -0 "$pid" 2>/dev/null; then
      echo "$(timestamp) stale extractor pid=${pid}; sending KILL" | tee -a "$GUARD_LOG"
      kill -KILL "$pid" 2>/dev/null || true
    fi
  done
}

echo "guard_run_id=${RUN_ID}" | tee -a "$GUARD_LOG"
echo "started_at=$(timestamp)" | tee -a "$GUARD_LOG"
echo "stall_seconds=${STALL_SECONDS}" | tee -a "$GUARD_LOG"
echo "check_seconds=${CHECK_SECONDS}" | tee -a "$GUARD_LOG"
echo "process_pattern=${PROCESS_PATTERN}" | tee -a "$GUARD_LOG"

while true; do
  pids="$(running_extraction_pids)"
  if [[ -z "$pids" ]]; then
    echo "$(timestamp) no retry extraction process found; sleeping" | tee -a "$GUARD_LOG"
    sleep "$CHECK_SECONDS"
    continue
  fi

  now="$(date +%s)"
  progress_mtime="$(latest_progress_mtime)"
  idle_seconds=$((now - progress_mtime))
  echo "$(timestamp) pids=[$(echo "$pids" | tr '\n' ' ')] idle_seconds=${idle_seconds}" | tee -a "$GUARD_LOG"

  if [[ "$progress_mtime" -gt 0 && "$idle_seconds" -ge "$STALL_SECONDS" ]]; then
    terminate_stale_extractors "$pids"
  fi
  sleep "$CHECK_SECONDS"
done
