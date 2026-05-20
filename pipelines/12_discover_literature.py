from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.literature_discovery import DEFAULT_QUERIES, discover_literature


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", action="append", help="Search query. Repeat to add more queries.")
    parser.add_argument("--from-date", default="2024-01-01")
    parser.add_argument("--to-date", default=None)
    parser.add_argument("--rows-per-source", type=int, default=25)
    parser.add_argument("--min-score", type=float, default=0.25)
    parser.add_argument("--openalex-only", action="store_true")
    parser.add_argument("--crossref-only", action="store_true")
    args = parser.parse_args()

    queries = args.query or DEFAULT_QUERIES
    stats = discover_literature(
        queries=queries,
        from_date=args.from_date,
        to_date=args.to_date,
        rows_per_source=args.rows_per_source,
        include_openalex=not args.crossref_only,
        include_crossref=not args.openalex_only,
        min_score=args.min_score,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
