from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.design_model import train_tiered_design_models


def main() -> None:
    parser = argparse.ArgumentParser(description="Train and compare models across benchmark tiers.")
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--min-rows", type=int, default=12)
    parser.add_argument("--targets", default="")
    args = parser.parse_args()
    targets = [item.strip() for item in args.targets.split(",") if item.strip()] or None
    stats = train_tiered_design_models(
        output_root=args.output_root,
        targets=targets,
        min_rows=args.min_rows,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
