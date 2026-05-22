from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.hfo2_extractor import run_extraction
from backend.services.hfo2_parallel_extractor import run_parallel_extraction


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-chunks", type=int, default=None)
    parser.add_argument("--no-llm", action="store_true", help="Use only deterministic rule extraction.")
    parser.add_argument("--model", default=None, help="Override HFO2_FERROKG_LLM_MODEL.")
    parser.add_argument("--dry-run", action="store_true", help="Run extraction without writing candidates/facts.")
    parser.add_argument("--incremental", action="store_true", help="Only extract chunks without candidates for the current ontology.")
    parser.add_argument("--commit-every", type=int, default=25, help="Commit database writes every N processed chunks.")
    parser.add_argument("--progress-every", type=int, default=20, help="Print progress every N processed chunks.")
    parser.add_argument("--max-workers", type=int, default=1, help="Concurrent LLM calls for extraction.")
    args = parser.parse_args()
    runner = run_parallel_extraction if args.max_workers and args.max_workers > 1 else run_extraction
    result = runner(
        limit_chunks=args.limit_chunks,
        use_llm=not args.no_llm,
        llm_model=args.model,
        dry_run=args.dry_run,
        reset_existing=not args.incremental,
        commit_every=args.commit_every,
        progress_every=args.progress_every,
        **({"max_workers": args.max_workers} if runner is run_parallel_extraction else {}),
    )
    print(result)
    if result.get("paused"):
        raise SystemExit(75)


if __name__ == "__main__":
    main()
