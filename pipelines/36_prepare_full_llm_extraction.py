from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.llm_full_extraction_planner import build_full_llm_extraction_plan


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all-chunks", action="store_true", help="Plan every chunk, not only high-value chunks.")
    parser.add_argument("--include-existing", action="store_true", help="Include chunks already present in extraction_candidates.")
    parser.add_argument("--limit-chunks", type=int, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument(
        "--relevance-tiers",
        default=None,
        help="Comma-separated paper relevance tiers to include, e.g. core or core,adjacent. Omit to skip relevance filtering.",
    )
    args = parser.parse_args()
    relevance_tiers = None
    if args.relevance_tiers:
        relevance_tiers = [item.strip() for item in args.relevance_tiers.split(",") if item.strip()]
    stats = build_full_llm_extraction_plan(
        output_dir=args.output_dir,
        high_value_only=not args.all_chunks,
        include_existing=args.include_existing,
        limit_chunks=args.limit_chunks,
        relevance_tiers=relevance_tiers,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
