from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.table_extractor import extract_tables


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-pdfs", "--limit", type=int, default=None)
    parser.add_argument("--incremental", action="store_true", help="Only scan PDFs not already table-parsed.")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--progress-every", type=int, default=10)
    args = parser.parse_args()
    print(
        extract_tables(
            limit_pdfs=args.limit_pdfs,
            incremental=args.incremental,
            timeout_seconds=args.timeout_seconds,
            progress_every=args.progress_every,
        )
    )


if __name__ == "__main__":
    main()
