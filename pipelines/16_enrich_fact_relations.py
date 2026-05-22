from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.fact_relation_enricher import enrich_fact_relations


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(enrich_fact_relations(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
