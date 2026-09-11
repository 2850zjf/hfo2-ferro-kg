from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.core import config as _project_config  # loads project .env before credential lookup
from backend.services.phase_llm_preannotation import (
    AliyunJSONClient,
    load_aliyun_config_from_env,
    preannotate_phase_queue,
)


def _worktree_output(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved != PROJECT_ROOT and PROJECT_ROOT not in resolved.parents:
        raise ValueError(f"Output must stay inside the research worktree: {PROJECT_ROOT}")
    return resolved


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Use an Aliyun OpenAI-compatible model to create evidence-gated machine "
            "suggestions for a phase annotation queue. Human adjudication fields are "
            "preserved and the production database is never accessed."
        )
    )
    parser.add_argument("--input-queue", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-rows", type=int, default=10)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument(
        "--max-retries",
        type=int,
        default=0,
        help="Provider retries per row; pilot default is 0 to prevent duplicate charges.",
    )
    args = parser.parse_args()

    config = load_aliyun_config_from_env()
    if config is None:
        print(json.dumps({
            "status": "skipped",
            "reason": "DASHSCOPE_API_KEY is not configured; rule-based processing remains available.",
        }, ensure_ascii=False, indent=2))
        return
    client = AliyunJSONClient(
        config,
        timeout_seconds=args.timeout_seconds,
        max_retries=args.max_retries,
    )
    summary = preannotate_phase_queue(
        args.input_queue,
        _worktree_output(args.output_dir),
        client,
        max_rows=args.max_rows,
    )
    summary["provider"] = config.public_metadata()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
