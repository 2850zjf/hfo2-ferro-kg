#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RUN_ID="${1:-$(date +%Y%m%d_%H%M%S)}"
RELEVANCE_TIERS="${RELEVANCE_TIERS:-core}"
SCREEN_LLM_MODE="${SCREEN_LLM_MODE:-off}"
SHARD_PREFIX="${SHARD_PREFIX:-full_core_high_value}"
SHARD_DIR="${SHARD_DIR:-data/extraction_queues/shards/${SHARD_PREFIX}}"
QUEUE_PATH="${QUEUE_PATH:-data/extraction_queues/full_hzo_llm_high_value_chunks.csv}"
PREPARE_QUEUE="${PREPARE_QUEUE:-1}"
SHARD_SIZE="${SHARD_SIZE:-500}"
MAX_WORKERS="${MAX_WORKERS:-1}"
STRICT_LLM="${STRICT_LLM:-1}"
LOG_DIR="logs/full_high_value_reextraction_${RUN_ID}"
STATUS_DIR="${LOG_DIR}/status"
mkdir -p "$LOG_DIR" "$STATUS_DIR"

timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

echo "run_id=${RUN_ID}" | tee "${LOG_DIR}/run.info"
echo "started_at=$(timestamp)" | tee -a "${LOG_DIR}/run.info"
echo "shard_dir=${SHARD_DIR}" | tee -a "${LOG_DIR}/run.info"
echo "relevance_tiers=${RELEVANCE_TIERS}" | tee -a "${LOG_DIR}/run.info"
echo "screen_llm_mode=${SCREEN_LLM_MODE}" | tee -a "${LOG_DIR}/run.info"
echo "max_workers=${MAX_WORKERS}" | tee -a "${LOG_DIR}/run.info"
echo "strict_llm=${STRICT_LLM}" | tee -a "${LOG_DIR}/run.info"

if [[ "$PREPARE_QUEUE" == "1" ]]; then
  echo "screen_literature_relevance $(timestamp)" | tee -a "${LOG_DIR}/run.info"
  .venv/bin/python pipelines/40_screen_literature_relevance.py \
    --llm-mode "$SCREEN_LLM_MODE" \
    --skip-existing \
    --progress-every 100 \
    > "${LOG_DIR}/40_screen_literature_relevance.log" 2>&1

  echo "prepare_core_queue $(timestamp)" | tee -a "${LOG_DIR}/run.info"
  .venv/bin/python pipelines/36_prepare_full_llm_extraction.py \
    --include-existing \
    --relevance-tiers "$RELEVANCE_TIERS" \
    > "${LOG_DIR}/36_prepare_full_llm_extraction.log" 2>&1

  rm -rf "$SHARD_DIR"
  echo "split_core_queue $(timestamp)" | tee -a "${LOG_DIR}/run.info"
  .venv/bin/python pipelines/38_split_extraction_queue.py \
    --input "$QUEUE_PATH" \
    --output-dir "$SHARD_DIR" \
    --prefix "$SHARD_PREFIX" \
    --shard-size "$SHARD_SIZE" \
    > "${LOG_DIR}/38_split_extraction_queue.log" 2>&1
fi

shopt -s nullglob
shards=("${SHARD_DIR}"/"${SHARD_PREFIX}"_shard*.csv)
if [[ ${#shards[@]} -eq 0 ]]; then
  echo "no shards found in ${SHARD_DIR}" | tee -a "${LOG_DIR}/run.info"
  exit 1
fi

for shard in "${shards[@]}"; do
  shard_name="$(basename "$shard" .csv)"
  done_file="${STATUS_DIR}/${shard_name}.done"
  log_file="${LOG_DIR}/${shard_name}.log"
  if [[ -f "$done_file" ]]; then
    echo "skip ${shard_name}: already done" | tee -a "${LOG_DIR}/run.info"
    continue
  fi
  resume_args=()
  if [[ -s "$log_file" ]]; then
    resume_args+=(--incremental)
    echo "resume ${shard_name} with --incremental $(timestamp)" | tee -a "${LOG_DIR}/run.info"
  fi
  echo "start ${shard_name} $(timestamp)" | tee -a "${LOG_DIR}/run.info"
  {
    echo ""
    echo "=== ${shard_name} start $(timestamp) args=${resume_args[*]-} ==="
  } >> "$log_file"
  strict_args=()
  if [[ "$STRICT_LLM" == "1" ]]; then
    strict_args+=(--llm-strict)
  fi
  if [[ ${#resume_args[@]} -gt 0 ]]; then
    .venv/bin/python pipelines/05_run_extraction.py \
      --chunk-list "$shard" \
      --model qwen3.7-max \
      --commit-every 10 \
      --progress-every 10 \
      --max-workers "$MAX_WORKERS" \
      "${strict_args[@]}" \
      "${resume_args[@]}" \
      >> "$log_file" 2>&1
  else
    .venv/bin/python pipelines/05_run_extraction.py \
      --chunk-list "$shard" \
      --model qwen3.7-max \
      --commit-every 10 \
      --progress-every 10 \
      --max-workers "$MAX_WORKERS" \
      "${strict_args[@]}" \
      >> "$log_file" 2>&1
  fi
  touch "$done_file"
  echo "done ${shard_name} $(timestamp)" | tee -a "${LOG_DIR}/run.info"
done

echo "rebuild_design_outputs $(timestamp)" | tee -a "${LOG_DIR}/run.info"
.venv/bin/python pipelines/21_build_design_dataset.py > "${LOG_DIR}/21_build_design_dataset.log" 2>&1
.venv/bin/python pipelines/29_build_benchmark_tiers.py > "${LOG_DIR}/29_build_benchmark_tiers.log" 2>&1
.venv/bin/python pipelines/37_apply_physical_constraints.py \
  --input data/design/hfo2_design_dataset.csv \
  --output data/design/hfo2_design_dataset_physics_constrained.csv \
  > "${LOG_DIR}/37_apply_physical_constraints.log" 2>&1
.venv/bin/python pipelines/33_filter_and_compare_models.py > "${LOG_DIR}/33_filter_and_compare_models.log" 2>&1

echo "completed_at=$(timestamp)" | tee -a "${LOG_DIR}/run.info"
