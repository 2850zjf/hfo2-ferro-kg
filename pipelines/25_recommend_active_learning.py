from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.active_learning import DEFAULT_TARGET, recommend_active_learning_candidates


def main() -> None:
    parser = argparse.ArgumentParser(description="Recommend HfO2 active-learning material/process candidates.")
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--dataset", type=Path, default=None)
    parser.add_argument("--models-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--max-candidates", type=int, default=1200)
    parser.add_argument("--top-n", type=int, default=80)
    args = parser.parse_args()
    stats = recommend_active_learning_candidates(
        target_property=args.target,
        dataset_path=args.dataset,
        models_dir=args.models_dir,
        output_dir=args.output_dir,
        max_candidates=args.max_candidates,
        top_n=args.top_n,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
