from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.publication_extraction import build_publication_extraction_queue


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the publication-grade HfO2/HZO extraction queue.")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--shard-size", type=int, default=200)
    parser.add_argument("--smoke-per-lane", type=int, default=2)
    parser.add_argument("--include-low-value", action="store_true")
    args = parser.parse_args()
    stats = build_publication_extraction_queue(
        output_dir=args.output_dir,
        shard_size=args.shard_size,
        smoke_per_lane=args.smoke_per_lane,
        include_low_value=args.include_low_value,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
