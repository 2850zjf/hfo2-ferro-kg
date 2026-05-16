from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.hfo2_extractor import run_extraction


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-chunks", type=int, default=None)
    parser.add_argument("--no-llm", action="store_true", help="Use only deterministic rule extraction.")
    parser.add_argument("--model", default=None, help="Override HFO2_FERROKG_LLM_MODEL.")
    parser.add_argument("--dry-run", action="store_true", help="Run extraction without writing candidates/facts.")
    args = parser.parse_args()
    print(
        run_extraction(
            limit_chunks=args.limit_chunks,
            use_llm=not args.no_llm,
            llm_model=args.model,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    main()
