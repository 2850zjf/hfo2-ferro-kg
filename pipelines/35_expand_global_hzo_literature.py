from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.global_literature_expansion import (
    GLOBAL_HZO_QUERIES,
    expand_global_hzo_literature,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", action="append", help="Override or add a global HZO query. Repeatable.")
    parser.add_argument("--from-date", default="2011-01-01")
    parser.add_argument("--to-date", default=None)
    parser.add_argument("--rows-per-source", type=int, default=75)
    parser.add_argument("--min-score", type=float, default=0.35)
    parser.add_argument("--download-limit", type=int, default=80)
    parser.add_argument("--skip-download", action="store_true")
    args = parser.parse_args()

    stats = expand_global_hzo_literature(
        from_date=args.from_date,
        to_date=args.to_date,
        rows_per_source=args.rows_per_source,
        min_score=args.min_score,
        download_limit=args.download_limit,
        skip_download=args.skip_download,
        queries=args.query or GLOBAL_HZO_QUERIES,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
