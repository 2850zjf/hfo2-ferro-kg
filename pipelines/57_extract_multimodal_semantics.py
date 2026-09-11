from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.multimodal_extractor import run_multimodal_queue, run_single_multimodal_asset


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract table, figure, and equation semantics with the configured Qwen model.")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--source-types", nargs="+", default=["figure", "table", "equation"])
    parser.add_argument("--priority-tiers", nargs="+", default=["P0"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--source-id", default=None)
    parser.add_argument("--source-type", choices=["figure", "table", "equation"], default=None)
    args = parser.parse_args()
    if args.source_id:
        if not args.source_type:
            parser.error("--source-type is required with --source-id")
        stats = run_single_multimodal_asset(
            source_type=args.source_type,
            source_id=args.source_id,
            model=args.model,
        )
    else:
        stats = run_multimodal_queue(
            limit=args.limit,
            source_types=args.source_types,
            priority_tiers=args.priority_tiers,
            model=args.model,
        )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
