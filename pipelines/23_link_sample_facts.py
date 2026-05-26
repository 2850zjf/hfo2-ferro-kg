from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.sample_linker import build_sample_property_links


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build sample-level property links for HfO2 design graph and benchmark."
    )
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--no-benchmark", action="store_true")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--force-llm-when-paused", action="store_true")
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--commit-every", type=int, default=100)
    args = parser.parse_args()
    stats = build_sample_property_links(
        reset=args.reset,
        include_benchmark=not args.no_benchmark,
        use_llm=not args.no_llm,
        force_llm_when_paused=args.force_llm_when_paused,
        model=args.model,
        max_workers=args.max_workers,
        limit=args.limit,
        progress_every=args.progress_every,
        commit_every=args.commit_every,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    if stats.get("paused"):
        raise SystemExit(75)


if __name__ == "__main__":
    main()
