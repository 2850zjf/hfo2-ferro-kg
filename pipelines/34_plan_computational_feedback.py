from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.computation_planner import plan_computational_feedback_tasks


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plan computational feedback tasks for HfO2/HZO design candidates without launching jobs."
    )
    parser.add_argument("--candidates", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--max-candidates", type=int, default=20)
    parser.add_argument("--max-tasks", type=int, default=80)
    args = parser.parse_args()

    stats = plan_computational_feedback_tasks(
        candidates_path=args.candidates,
        output_dir=args.output_dir,
        max_candidates=args.max_candidates,
        max_tasks=args.max_tasks,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
