from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.paper_prototype_eval import evaluate_paper_prototype


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create or evaluate the HfO2-FerroKG paper prototype gold set."
    )
    parser.add_argument("--gold-set", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--sample-size", type=int, default=30)
    args = parser.parse_args()
    stats = evaluate_paper_prototype(
        gold_set_path=args.gold_set,
        output_dir=args.output_dir,
        sample_size=args.sample_size,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
