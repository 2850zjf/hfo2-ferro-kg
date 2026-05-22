from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.benchmark_extractor import export_benchmark_csv, run_open_benchmark_extraction


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-chunks", type=int, default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument(
        "--high-value-only",
        action="store_true",
        help="Only process chunks marked as high value or table chunks.",
    )
    parser.add_argument("--commit-every", type=int, default=20)
    parser.add_argument("--progress-every", type=int, default=10)
    parser.add_argument("--max-workers", type=int, default=16)
    args = parser.parse_args()
    stats = run_open_benchmark_extraction(
        limit_chunks=args.limit_chunks,
        model=args.model,
        reset_existing=args.reset,
        include_low_value=not args.high_value_only,
        commit_every=args.commit_every,
        progress_every=args.progress_every,
        max_workers=args.max_workers,
    )
    exports = {} if stats.get("paused") else export_benchmark_csv()
    print(json.dumps({"stats": stats, "exports": exports}, ensure_ascii=True, indent=2))
    if stats.get("paused"):
        raise SystemExit(75)


if __name__ == "__main__":
    main()
