from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.literature_card import build_literature_cards


def main() -> None:
    parser = argparse.ArgumentParser(description="Build LLM paper-level literature cards.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--commit-every", type=int, default=20)
    parser.add_argument("--progress-every", type=int, default=10)
    parser.add_argument("--force-llm-when-paused", action="store_true")
    args = parser.parse_args()
    stats = build_literature_cards(
        limit=args.limit,
        model=args.model,
        reset=args.reset,
        max_workers=args.max_workers,
        commit_every=args.commit_every,
        progress_every=args.progress_every,
        force_llm_when_paused=args.force_llm_when_paused,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    if stats.get("paused"):
        raise SystemExit(75)


if __name__ == "__main__":
    main()
