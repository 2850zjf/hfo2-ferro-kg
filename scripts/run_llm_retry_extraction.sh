#!/usr/bin/env bash
set -euo pipefail

RUN_ID="${1:-llm_retry_$(date +%Y%m%d_%H%M%S)}"

PREPARE_QUEUE="${PREPARE_QUEUE:-0}" \
MAX_WORKERS="${MAX_WORKERS:-1}" \
SHARD_PREFIX="${SHARD_PREFIX:-full_core_llm_retry}" \
SHARD_DIR="${SHARD_DIR:-data/extraction_queues/shards/full_core_llm_retry}" \
QUEUE_PATH="${QUEUE_PATH:-data/extraction_queues/full_hzo_llm_retry_chunks.csv}" \
"$(dirname "$0")/run_full_high_value_reextraction.sh" "$RUN_ID"
