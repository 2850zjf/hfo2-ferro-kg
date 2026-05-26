from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.design_dataset import build_benchmark_tier_datasets


def main() -> None:
    parser = argparse.ArgumentParser(description="Build strong_only, strong_partial, and all_traceable benchmark datasets.")
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    stats = build_benchmark_tier_datasets(output_dir=args.output_dir)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
