from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.literature_relevance_screener import screen_literature_relevance


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Screen PDFs into core, adjacent, irrelevant, and needs_review relevance tiers."
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument(
        "--llm-mode",
        choices=["off", "uncertain", "all"],
        default="uncertain",
        help="Use LLM for no papers, only uncertain papers, or every paper.",
    )
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--commit-every", type=int, default=25)
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--force-llm-when-paused", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--db-path", type=Path, default=None)
    args = parser.parse_args()

    stats = screen_literature_relevance(
        limit=args.limit,
        model=args.model,
        reset=args.reset,
        skip_existing=args.skip_existing,
        llm_mode=args.llm_mode,
        max_workers=args.max_workers,
        commit_every=args.commit_every,
        progress_every=args.progress_every,
        force_llm_when_paused=args.force_llm_when_paused,
        output_dir=args.output_dir,
        db_path=args.db_path,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    if stats.get("paused"):
        raise SystemExit(75)


if __name__ == "__main__":
    main()
