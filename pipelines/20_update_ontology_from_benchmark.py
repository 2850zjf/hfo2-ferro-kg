from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.benchmark_ontology import build_benchmark_ontology_extension


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-support", type=int, default=2)
    args = parser.parse_args()
    stats = build_benchmark_ontology_extension(min_support=args.min_support)
    print(json.dumps(stats, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()

