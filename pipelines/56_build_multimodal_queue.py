from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.multimodal_queue import build_multimodal_queue


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the table/figure/equation semantic extraction queue.")
    parser.add_argument("--compute-hashes", action="store_true", help="Hash image assets to mark exact duplicates.")
    args = parser.parse_args()
    print(json.dumps(build_multimodal_queue(compute_hashes=args.compute_hashes), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
